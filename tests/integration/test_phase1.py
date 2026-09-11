"""Real MySQL, real subprocesses and FFmpeg. Missing dependencies fail, never skip."""

import array
import json
import math
import os
import shutil
import subprocess
import sys
import time
import wave
from fractions import Fraction as F
from pathlib import Path
import pytest
from mlvideo.config import ROOT, load
from mlvideo.db import DB
from mlvideo.engine import Engine
from mlvideo.util import read_json, sha512, atomic_json, file_lock
from mlvideo.media import normalize, probe, render
from mlvideo.timeline import plan

OUT = Path(os.environ.get("MLVIDEO_EVIDENCE", ROOT / "evidence/development"))
OUT.mkdir(parents=True, exist_ok=True)
CONFIG = os.environ.get("MLVIDEO_TEST_CONFIG")


def cli(*args, env=None, check=True):
    argv = (
        [sys.executable, "-m", "mlvideo.cli"]
        + (["--config", CONFIG] if CONFIG else [])
        + list(args)
    )
    p = subprocess.run(argv, capture_output=True, text=True, env=env)
    with (OUT / "commands.jsonl").open("a") as f:
        f.write(
            json.dumps(
                {
                    "argv": argv,
                    "returncode": p.returncode,
                    "stdout": p.stdout,
                    "stderr": p.stderr,
                }
            )
            + "\n"
        )
    if check:
        assert p.returncode == 0, (p.stdout, p.stderr)
    return json.loads(p.stdout) if p.returncode == 0 else p


@pytest.fixture(scope="module")
def setup():
    cli("migrate")
    config = load(CONFIG)
    db = DB(config)
    e = Engine(db, config)
    fixture = ROOT / "tests/fixtures/generated/cfr.mkv"
    assert fixture.exists(), "Run scripts/make_fixtures.py"
    imported = cli("ingest", str(fixture))
    yield e, imported
    db.close()


def port(result, name):
    return next(
        a["artifact_id"]
        for a in result["artifacts"]
        if json.loads(a["metadata_json"])["port"] == name
    )


def execute(e, asset, node, strategy, inputs, params):
    with file_lock(e.root / ".writer.lock"):
        e.guard()
        return e.run(asset, node, strategy, inputs, params)


def test_db_store_retry_lineage(setup, tmp_path):
    e, imp = setup
    asset = imp["asset_sha512"]
    assert cli("doctor")["ok"]
    assert cli("migrate") == cli("migrate")
    copy = tmp_path / "renamed.mkv"
    shutil.copyfile(ROOT / "tests/fixtures/generated/cfr.mkv", copy)
    second = cli("ingest", str(copy))
    assert second["asset_sha512"] == asset
    assert second["acquisition_id"] != imp["acquisition_id"]
    copy.write_bytes(b"changed")
    assert sha512(e.root / "videos" / asset / "source/original.bin") == asset
    a = execute(e, asset, "TEST", "fixture_a", {}, {})
    ids = [a["execution_id"]]
    versions = [a["version"]]
    for _ in range(3):
        r = cli("retry", asset, ids[-1])
        assert r["retry_of"] == ids[-1]
        ids.append(r["execution_id"])
        versions.append(r["version"])
    assert len(set(ids)) == 4
    assert versions == list(range(versions[0], versions[0] + 4))
    invocations = [
        read_json(Path(e.artifact(port(e.result(i), "result"), asset)["path"]))[
            "invocation"
        ]
        for i in ids
    ]
    assert len(set(invocations)) == 4
    b = execute(e, asset, "TEST", "fixture_b", {}, {"text": "abc"})
    assert (
        read_json(Path(e.artifact(port(b, "result"), asset)["path"]))["text"] == "ABC"
    )
    p = execute(e, asset, "N03", "ffprobe", {"source": imp["source_artifact_id"]}, {})
    parent = e.db.query(
        "SELECT * FROM artifact_parents WHERE artifact_id=%s", (port(p, "probe"),)
    )
    assert parent[0]["parent_artifact_id"] == imp["source_artifact_id"]
    with pytest.raises(ValueError):
        e.artifact(imp["source_artifact_id"], "0" * 128)
    ref = e.artifact(port(a, "result"), asset)
    file = Path(ref["path"])
    old = file.read_bytes()
    file.write_bytes(b"tampered")
    try:
        with pytest.raises(ValueError):
            e.artifact(ref["artifact_id"], asset)
    finally:
        file.write_bytes(old)
    cli(
        "select",
        asset,
        "source",
        imp["source_artifact_id"],
        "--reason",
        "Phase 1 fixed source",
    )
    recipe = tmp_path / "recipe.json"
    atomic_json(
        recipe,
        {
            "steps": [
                {
                    "id": "probe",
                    "node": "N03",
                    "strategy": "ffprobe",
                    "inputs": {"source": {"selection": "source"}},
                }
            ]
        },
    )
    result = cli("pipeline", asset, "--recipe", str(recipe))
    assert result["bindings"]["probe"]["source"] == imp["source_artifact_id"]
    assert result["state"] == "REVIEW"
    atomic_json(
        OUT / "db-store-lineage-retry.json",
        {
            "asset": asset,
            "executions": ids,
            "versions": versions,
            "invocations": invocations,
            "pipeline": result,
        },
    )


@pytest.mark.parametrize(
    "point",
    ["request", "inputs", "manifest", "partial_artifact", "event", "commit_ack"],
)
def test_recover_windows(setup, tmp_path, point):
    e, imp = setup
    asset = imp["asset_sha512"]
    params = tmp_path / "p.json"
    inputs = tmp_path / "i.json"
    node, name = "TEST", "fixture_a"
    values = {}
    if point == "partial_artifact":
        node, name = "N02", "source"
        original = e.db.one(
            "SELECT request_path FROM executions WHERE id=%s", (imp["execution_id"],)
        )
        values = read_json(e.root / original["request_path"])["params"]
    atomic_json(params, values)
    atomic_json(inputs, {})
    result = cli(
        "run",
        asset,
        node,
        name,
        "--inputs",
        str(inputs),
        "--params",
        str(params),
        env=os.environ | {"MLVIDEO_TEST_FAULT": point},
        check=False,
    )
    assert result.returncode == 86
    requests = sorted(
        (e.root / "videos" / asset / "nodes" / node / "video").glob("*/request.json"),
        key=lambda p: p.stat().st_mtime_ns,
    )
    req = read_json(requests[-1])
    identity = req["execution_id"]
    directory = requests[-1].parent
    invocation = (
        (directory / "work/fixture.json").read_bytes()
        if (directory / "work/fixture.json").exists()
        else None
    )
    if point == "partial_artifact":
        partial = e.db.query(
            "SELECT id FROM artifacts WHERE execution_id=%s", (identity,)
        )
        assert len(partial) == 1
        with pytest.raises(ValueError):
            e.artifact(partial[0]["id"], asset)
    recovered = cli("recover", asset, identity)
    if point == "partial_artifact":
        assert len(recovered["artifacts"]) == 2
    assert recovered["state"] == (
        "INTERRUPTED" if point in ("request", "inputs") else "SUCCEEDED"
    )
    if invocation:
        assert (directory / "work/fixture.json").read_bytes() == invocation
    if point not in ("request", "inputs"):
        cli("recover", asset, identity)
    log = e.root / "videos" / asset / "events.jsonl"
    log.write_text("{half")
    cli("recover", asset, identity)
    before = log.read_bytes()
    from mlvideo.events import rebuild

    rebuild(e.db, e.root, asset)
    assert log.read_bytes() == before
    atomic_json(
        OUT / f"recover-{point}.json",
        {"execution_id": identity, "injection": point, "result": recovered},
    )


def test_lock_timeout_kill(setup, tmp_path):
    e, imp = setup
    asset = imp["asset_sha512"]
    p = tmp_path / "p.json"
    i = tmp_path / "i.json"
    atomic_json(p, {"sleep": 30})
    atomic_json(i, {})
    argv = (
        [sys.executable, "-m", "mlvideo.cli"]
        + (["--config", CONFIG] if CONFIG else [])
        + ["run", asset, "TEST", "fixture_a", "--inputs", str(i), "--params", str(p)]
    )
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 15
        row = None
        while time.monotonic() < deadline:
            row = e.db.one(
                "SELECT * FROM executions WHERE asset_sha512=%s AND state='RUNNING' ORDER BY started_at DESC LIMIT 1",
                (asset,),
            )
            if (
                row
                and (e.root / row["request_path"])
                .parent.joinpath("runtime.json")
                .exists()
            ):
                break
            time.sleep(0.1)
        assert row
        failed = cli(
            "select",
            asset,
            "source",
            imp["source_artifact_id"],
            "--reason",
            "lock test",
            check=False,
        )
        assert failed.returncode != 0 and "已有任务运行" in failed.stdout
        assert cli("status", asset)["executions"]
        proc.kill()
        proc.communicate(timeout=5)
        rejected = cli("retry", asset, imp["execution_id"], check=False)
        assert rejected.returncode != 0
        recovered = cli("recover", asset, row["id"])
        assert recovered["state"] == "INTERRUPTED"
        config = e.config | {"worker_timeout": 0.2}
        other = Engine(e.db, config)
        with pytest.raises(subprocess.TimeoutExpired):
            execute(other, asset, "TEST", "fixture_a", {}, {"sleep": 10})
        assert not e.db.query("SELECT id FROM executions WHERE state='RUNNING'")
        atomic_json(
            OUT / "lock-kill-timeout.json",
            {"killed_execution": row["id"], "recovered": recovered},
        )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()


@pytest.mark.parametrize("name", ["cfr", "ntsc", "vfr", "offset", "rotate", "hdr"])
def test_normalization(name, tmp_path):
    case = next(
        c
        for c in read_json(ROOT / "tests/fixtures/manifest.json")["cases"]
        if c["case_id"] == name
    )
    source = ROOT / case["path"]
    assert sha512(source) == case["sha512"]
    assert sha512(ROOT / case["expected_path"]) == case["expected_sha512"]
    info = probe(source, tmp_path)
    if name == "hdr":
        with pytest.raises(ValueError, match="HDR"):
            normalize(source, info, tmp_path)
        return
    normalize(source, info, tmp_path)
    c = read_json(tmp_path / "canonical.json")
    assert len(c["frame_mapping"]) == c["source_frames"]
    if name in ("cfr", "ntsc"):
        expected = read_json(ROOT / case["expected_path"])
        assert c["source_frames"] == expected["frames"]
        assert c["source_samples"] == expected["samples"]
        with wave.open(str(tmp_path / "canonical.wav"), "rb") as w:
            actual = array.array("h", w.readframes(w.getnframes()))
        expected = array.array(
            "h",
            (
                v
                for i in range(c["source_samples"])
                for v in (i % 997 - 498, 498 - i % 997)
            ),
        )
        assert actual == expected
    if name == "offset":
        assert c["audio_offset_seconds"] == "1/5"
        with wave.open(str(tmp_path / "canonical.wav"), "rb") as w:
            assert w.readframes(9600) == b"\0" * 38400
            payload = w.readframes(240000)
        with wave.open(
            str(ROOT / "tests/fixtures/generated/cfr.wav"), "rb"
        ) as original:
            assert payload == original.readframes(240000)
        assert c["source_frames"] == 130 and c["video_tail_frames"] == 5
    if name == "rotate":
        assert abs(c["rotation"]) == 90
        stream = probe(tmp_path / "canonical.mkv", tmp_path, False)["streams"][0]
        assert (stream["width"], stream["height"]) == (48, 64)
    if name == "vfr":
        assert c["warnings"] and c["fps"] == "25"
    stream = probe(tmp_path / "canonical.mkv", tmp_path, False)["streams"][0]
    frame_size = stream["width"] * stream["height"] * 3 // 2

    def yuv(path):
        return subprocess.check_output(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-fps_mode",
                "passthrough",
                "-pix_fmt",
                "yuv420p",
                "-f",
                "rawvideo",
                "-",
            ]
        )

    original = yuv(source)
    converted = yuv(tmp_path / "canonical.mkv")
    expected = b"".join(
        original[index * frame_size : (index + 1) * frame_size]
        for index in c["frame_mapping"]
    )
    assert converted == expected
    atomic_json(OUT / f"media-{name}.json", c | {"frame_mapping_pixels_verified": True})


def decoded_rgb(path):
    return subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ]
    )


@pytest.mark.parametrize(
    "fps,total,cut,end,expected_hold",
    [
        ("25", 50, 50, 48000, 38),
        ("25", 125, 125, 48000, 0),
        ("25", 25, 25, 48000, 63),
        ("30000/1001", 60, 60, 48000, 45),
    ],
)
def test_render_independent_oracle(tmp_path, fps, total, cut, end, expected_hold):
    name = "cfr" if fps == "25" else "ntsc"
    source = ROOT / f"tests/fixtures/generated/{name}.mkv"
    source_video = tmp_path / "trim.mkv"
    source_wav = tmp_path / "source.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-an",
            "-frames:v",
            str(total),
            "-c:v",
            "ffv1",
            "-pix_fmt",
            "bgr0",
            str(source_video),
        ],
        check=True,
    )

    # Independent decimal rational rounding; does not call planner for expected media.
    def q(frame):
        return int(F(frame * 48000) / F(fps) + F(1, 2))

    with (
        wave.open(str(ROOT / f"tests/fixtures/generated/{name}.wav"), "rb") as w,
        wave.open(str(source_wav), "wb") as dst,
    ):
        dst.setparams(w.getparams())
        dst.writeframes(w.readframes(q(total)))
    c = {
        "fps": fps,
        "sample_rate": 48000,
        "source_frames": total,
        "source_samples": q(total),
    }
    t = plan(
        c,
        [
            {
                "id": "u",
                "start_sample": 0,
                "end_sample": end,
                "safe_cut_frame": cut,
                "dub_samples": 24000,
                "tone_hz": 660,
            }
        ],
    )
    assert t["output_frames"] == total + expected_hold
    render(source_video, source_wav, t, tmp_path)
    original = decoded_rgb(source_video)
    actual = decoded_rgb(tmp_path / "master.mkv")
    size = 64 * 48 * 3
    expected = original + original[-size:] * expected_hold
    assert actual == expected
    with wave.open(str(source_wav), "rb") as w:
        pcm = array.array("h", w.readframes(w.getnframes()))
    pcm.extend([0] * ((q(total + expected_hold) - q(total)) * 2))
    for j in range(24000):
        tone = round(1000 * math.sin(2 * math.pi * 660 * j / 48000))
        for ch in (0, 1):
            pcm[(96000 + j) * 2 + ch] += tone
    with wave.open(str(tmp_path / "master.wav"), "rb") as w:
        assert array.array("h", w.readframes(w.getnframes())) == pcm
    dest = OUT / f"render-{name}-{total}"
    shutil.copytree(tmp_path, dest, dirs_exist_ok=True)
    atomic_json(
        dest / "verification.json",
        {
            "frames_compared": total + expected_hold,
            "sample_frames_compared": len(pcm) // 2,
            "hold_frames": expected_hold,
            "status": "PASS",
        },
    )


def admin_sql(sql):
    subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "mysql",
            "sh",
            "-c",
            'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot',
        ],
        input=sql,
        text=True,
        check=True,
        capture_output=True,
    )


def test_backup_restore_restart(setup, tmp_path):
    e, imp = setup
    asset = imp["asset_sha512"]
    name = "mlvideo_restore_" + str(time.time_ns())
    target = OUT / name
    config = e.config | {"data_root": str(target)}
    config["database"] = e.config["database"] | {"database": name}
    admin_sql(
        f"CREATE DATABASE `{name}`; GRANT ALL ON `{name}`.* TO 'mlvideo_migrate'@'%'; GRANT SELECT, INSERT, UPDATE ON `{name}`.* TO 'mlvideo'@'%';"
    )
    migration = DB(config, True)
    migration.migrate()
    migration.close()
    backup = tmp_path / "backup"
    result = cli("backup", "--out", str(backup))
    from mlvideo.backup import restore

    db = DB(config, True)
    isolated = Engine(db, config)
    try:
        with file_lock(target / ".writer.lock"):
            restored = restore(isolated, backup)
        assert result["tables"] == restored["tables"]
        assert isolated.artifact(imp["source_artifact_id"], asset)["sha512"] == asset
        with file_lock(target / ".writer.lock"):
            retry = isolated.retry(asset, imp["execution_id"])
        assert retry["state"] == "SUCCEEDED"
        counts = e.db.one("SELECT COUNT(*) n FROM executions")["n"]
        e.db.close()
        subprocess.run(
            ["docker", "compose", "restart", "mysql"], check=True, capture_output=True
        )
        deadline = time.monotonic() + 30
        while True:
            try:
                e.db.connect()
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.2)
        assert e.db.one("SELECT COUNT(*) n FROM executions")["n"] == counts
        assert e.artifact(imp["source_artifact_id"], asset)
        # App has no DDL privileges.
        with pytest.raises(Exception):
            e.db.query("CREATE TABLE forbidden (id INT)")
        atomic_json(
            OUT / "backup-restart.json",
            {
                "backup": result,
                "restored": restored,
                "retry": retry,
                "rows_after_restart": counts,
            },
        )
    finally:
        db.close()


def test_multicut_fractional_samples(tmp_path):
    fps = "30000/1001"
    total = 150
    video = ROOT / "tests/fixtures/generated/ntsc.mkv"
    audio = ROOT / "tests/fixtures/generated/ntsc.wav"
    q = lambda frame: int(F(frame * 48000) / F(fps) + F(1, 2))
    c = {
        "fps": fps,
        "sample_rate": 48000,
        "source_frames": total,
        "source_samples": q(total),
    }
    units = [
        {
            "id": "a",
            "start_sample": 0,
            "end_sample": 48000,
            "safe_cut_frame": 59,
            "dub_samples": 24001,
        },
        {
            "id": "b",
            "start_sample": 96000,
            "end_sample": 144000,
            "safe_cut_frame": 119,
            "dub_samples": 24003,
        },
        {
            "id": "c",
            "start_sample": 192000,
            "end_sample": 230000,
            "safe_cut_frame": 150,
            "dub_samples": 24005,
        },
    ]
    t = plan(c, units)
    render(video, audio, t, tmp_path)
    original = decoded_rgb(video)
    actual = decoded_rgb(tmp_path / "master.mkv")
    size = 64 * 48 * 3
    # Independently derive each minimal insertion with integer sample gap checks.
    expected = bytearray()
    cursor = delay = 0
    expected_pcm = array.array("h")
    dub_positions = []
    with wave.open(str(audio), "rb") as w:
        pcm = array.array("h", w.readframes(w.getnframes()))
    for u in units:
        cut = u["safe_cut_frame"]
        start = u["end_sample"] + q(total + delay) - q(total) + 48000
        hold = 0
        while (
            q(cut) + q(total + delay + hold) - q(total)
            < start + u["dub_samples"] + 48000
        ):
            hold += 1
        expected.extend(original[cursor * size : cut * size])
        expected.extend(original[(cut - 1) * size : cut * size] * hold)
        expected_pcm.extend(pcm[q(cursor) * 2 : q(cut) * 2])
        expected_pcm.extend(
            [0]
            * (
                (q(cut) + q(total + delay + hold) - q(total) - len(expected_pcm) // 2)
                * 2
            )
        )
        dub_positions.append((start, u["dub_samples"]))
        delay += hold
        cursor = cut
    assert actual == expected
    for start, n in dub_positions:
        for j in range(n):
            tone = round(1000 * math.sin(2 * math.pi * 660 * j / 48000))
            expected_pcm[(start + j) * 2] += tone
            expected_pcm[(start + j) * 2 + 1] += tone
    with wave.open(str(tmp_path / "master.wav"), "rb") as w:
        assert array.array("h", w.readframes(w.getnframes())) == expected_pcm
    atomic_json(
        OUT / "multicut.json",
        {
            "frames_compared": len(expected) // size,
            "samples_compared": len(expected_pcm) // 2,
            "timeline": t,
        },
    )


def test_render_fractional_long_tail(tmp_path):
    source = ROOT / "tests/fixtures/generated/ntsc.mkv"
    video = tmp_path / "source.mkv"
    audio = tmp_path / "source.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-stream_loop",
            "1",
            "-i",
            str(source),
            "-an",
            "-frames:v",
            "201",
            "-c:v",
            "ffv1",
            "-pix_fmt",
            "bgr0",
            str(video),
        ],
        check=True,
    )
    q = lambda f: int(F(f * 48000 * 1001, 30000) + F(1, 2))
    total = 201
    with wave.open(str(audio), "wb") as w:
        w.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
        w.writeframes(
            array.array("h", (i % 199 - 99 for i in range(q(total) * 2))).tobytes()
        )
    c = {
        "fps": "30000/1001",
        "sample_rate": 48000,
        "source_frames": total,
        "source_samples": q(total),
    }
    t = plan(
        c,
        [
            {
                "id": "a",
                "start_sample": 0,
                "end_sample": 48000,
                "safe_cut_frame": 59,
                "dub_samples": 24000,
            },
            {
                "id": "b",
                "start_sample": 96000,
                "end_sample": 100800,
                "safe_cut_frame": 201,
                "dub_samples": 1,
            },
        ],
    )
    render(video, audio, t, tmp_path)
    # Compare unmixed source audio: remove only the independently derived inserted zeros.
    with wave.open(str(tmp_path / "retimed.wav"), "rb") as w:
        got = w.readframes(w.getnframes())
    with wave.open(str(audio), "rb") as w:
        original = w.readframes(w.getnframes())
    inserted = q(total + 46) - q(total)
    assert got[: q(59) * 4] + got[(q(59) + inserted) * 4 :] == original
    assert len(got) // 4 == q(247)
    atomic_json(
        OUT / "fractional-tail.json",
        {
            "source_samples": q(total),
            "output_samples": len(got) // 4,
            "inserted_samples": inserted,
            "status": "PASS",
        },
    )


def test_bounded_disk_full(tmp_path):
    """ENOSPC is confined to a 16 MiB disk image, never the host volume."""
    image = tmp_path / "bounded.dmg"
    mount = tmp_path / "mount"
    mount.mkdir()
    subprocess.run(
        [
            "hdiutil",
            "create",
            "-size",
            "16m",
            "-fs",
            "HFS+",
            "-volname",
            "MLVideoFault",
            str(image),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["hdiutil", "attach", "-nobrowse", "-mountpoint", str(mount), str(image)],
        check=True,
        capture_output=True,
    )
    db = None
    try:
        name = "mlvideo_disk_" + str(time.time_ns())
        config = load(CONFIG)
        config["data_root"] = str(mount / "data")
        config["database"] = config["database"] | {"database": name}
        admin_sql(
            f"CREATE DATABASE `{name}`; GRANT ALL ON `{name}`.* TO 'mlvideo_migrate'@'%'; GRANT SELECT, INSERT, UPDATE ON `{name}`.* TO 'mlvideo'@'%';"
        )
        db = DB(config, True)
        db.migrate()
        engine = Engine(db, config)
        asset = "1" * 128
        db.insert("assets", {"sha512": asset, "source_path": "test-only", "bytes": 0})
        with pytest.raises(Exception):
            execute(
                engine, asset, "TEST", "fixture_a", {}, {"fill_bytes": 32 * 1024 * 1024}
            )
        row = db.one("SELECT * FROM executions")
        assert row["state"] == "FAILED"
        directory = (engine.root / row["request_path"]).parent
        assert not (directory / "manifest.sha512.json").exists()
        assert not db.query("SELECT * FROM artifacts")
        pressure = directory / "work/pressure.bin"
        assert pressure.exists() and pressure.stat().st_size < 16 * 1024 * 1024
        filled_size = pressure.stat().st_size
        pressure.unlink()
        shutil.copytree(directory, OUT / "disk-full-execution", dirs_exist_ok=True)
        assert "No space left on device" in (directory / "work/stderr.log").read_text()
        atomic_json(
            OUT / "disk-full.json",
            {
                "execution_id": row["id"],
                "state": row["state"],
                "injection": "worker artifact write on 16 MiB HFS+ image",
                "pressure_bytes": filled_size,
                "artifacts": 0,
            },
        )
    finally:
        if db:
            db.close()
        subprocess.run(
            ["hdiutil", "detach", str(mount)], check=True, capture_output=True
        )


def test_full_media_recipe(setup, tmp_path):
    e, imp = setup
    asset = imp["asset_sha512"]
    recipe = {
        "steps": [
            {
                "id": "probe",
                "node": "N03",
                "strategy": "ffprobe",
                "inputs": {"source": imp["source_artifact_id"]},
            },
            {
                "id": "canonical",
                "node": "N04",
                "strategy": "ffmpeg",
                "inputs": {
                    "source": imp["source_artifact_id"],
                    "probe": {"step": "probe", "port": "probe"},
                },
            },
            {
                "id": "timeline",
                "node": "N16",
                "strategy": "gap_first",
                "inputs": {"canonical": {"step": "canonical", "port": "canonical"}},
                "params": {
                    "utterances": [
                        {
                            "id": "u",
                            "start_sample": 0,
                            "end_sample": 192000,
                            "safe_cut_frame": 125,
                            "dub_samples": 24000,
                        }
                    ]
                },
            },
            {
                "id": "render",
                "node": "N18",
                "strategy": "ffmpeg",
                "inputs": {
                    "video": {"step": "canonical", "port": "video"},
                    "audio": {"step": "canonical", "port": "audio"},
                    "timeline": {"step": "timeline", "port": "timeline"},
                },
            },
        ]
    }
    path = tmp_path / "recipe.json"
    atomic_json(path, recipe)
    result = cli("pipeline", asset, "--recipe", str(path))
    assert result["state"] == "REVIEW"
    render_result = result["executions"]["render"]
    assert render_result["state"] == "SUCCEEDED"
    retry = cli("retry", asset, render_result["execution_id"])
    assert retry["version"] == render_result["version"] + 1
    assert retry["retry_of"] == render_result["execution_id"]
    master = e.artifact(port(render_result, "master"), asset)
    second = e.artifact(port(retry, "master"), asset)
    assert decoded_rgb(master["path"]) == decoded_rgb(second["path"])
    parents = e.db.query(
        "SELECT * FROM artifact_parents WHERE artifact_id=%s", (master["artifact_id"],)
    )
    assert len(parents) == 3
    target = OUT / "media-demo"
    target.mkdir(exist_ok=True)
    for name in ("master", "preview", "audio", "qa"):
        ref = e.artifact(port(render_result, name), asset)
        shutil.copyfile(ref["path"], target / Path(ref["path"]).name)
    atomic_json(
        OUT / "full-recipe.json", {"recipe": recipe, "result": result, "retry": retry}
    )


def test_database_disconnect(setup, tmp_path):
    e, imp = setup
    asset = imp["asset_sha512"]
    p = tmp_path / "p.json"
    i = tmp_path / "i.json"
    atomic_json(p, {})
    atomic_json(i, {})
    crashed = cli(
        "run",
        asset,
        "TEST",
        "fixture_a",
        "--inputs",
        str(i),
        "--params",
        str(p),
        env=os.environ | {"MLVIDEO_TEST_FAULT": "manifest"},
        check=False,
    )
    assert crashed.returncode == 86
    row = e.db.one("SELECT * FROM executions WHERE state='RUNNING'")
    directory = (e.root / row["request_path"]).parent
    before = sha512(directory / "work/fixture.json")
    connection = e.db.one("SELECT CONNECTION_ID() id")["id"]
    admin_sql(f"KILL CONNECTION {connection}")
    with pytest.raises(Exception):
        e.commit(directory)
    e.db.connect()
    result = cli("recover", asset, row["id"])
    assert result["state"] == "SUCCEEDED"
    assert sha512(directory / "work/fixture.json") == before
    duplicate = e.db.one("SELECT * FROM executions WHERE id=%s", (row["id"],))
    duplicate["id"] = "exec_duplicate_constraint"
    with pytest.raises(Exception):
        e.db.insert("executions", duplicate)
    atomic_json(
        OUT / "db-disconnect.json",
        {
            "execution_id": row["id"],
            "killed_connection": connection,
            "result": result,
            "worker_output_unchanged": True,
        },
    )


def test_downloader_real_http(setup):
    """Exercise actual yt-dlp HTTP transfer; does not replace authorized YouTube acceptance."""
    import functools
    import http.server
    import threading

    e, imp = setup
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler,
        directory=str(ROOT / "tests/fixtures/generated"),
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/rotate.mp4"
        first = cli("download", url)
        second = cli("download", url)
        for result in (first, second):
            directory = e.root / "incoming" / result["acquisition_id"]
            assert "[download]" in (directory / "raw/stdout.log").read_text()
            assert read_json(directory / "runtime.json")["argv"][2] == "yt_dlp"
            assert read_json(directory / "raw/runtime.json")["stopped"]

        assert first["acquisition_id"] != second["acquisition_id"]
        assert (
            first["asset_sha512"]
            == second["asset_sha512"]
            == sha512(ROOT / "tests/fixtures/generated/rotate.mp4")
        )
        failed = cli(
            "download",
            f"http://127.0.0.1:{server.server_port}/missing.mp4",
            check=False,
        )
        assert failed.returncode != 0
        row = e.db.one(
            "SELECT * FROM acquisitions WHERE kind='download' ORDER BY created_at DESC LIMIT 1"
        )
        assert row["state"] == "FAILED" and row["asset_sha512"] is None
        assert (e.root / row["receipt_path"]).exists()
        atomic_json(
            OUT / "download-http.json",
            {
                "first": first,
                "second": second,
                "failed_acquisition": row["id"],
                "authorization": "Original generated media served locally",
                "youtube_acceptance": False,
            },
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_audio_ahead_preserved(tmp_path):
    source = tmp_path / "audio-ahead.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-itsoffset",
            "0.2",
            "-i",
            str(ROOT / "tests/fixtures/generated/cfr.mkv"),
            "-i",
            str(ROOT / "tests/fixtures/generated/cfr.wav"),
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c",
            "copy",
            str(source),
        ],
        check=True,
    )
    normalize(source, probe(source, tmp_path), tmp_path)
    c = read_json(tmp_path / "canonical.json")
    assert c["video_lead_frames"] == 5 and c["source_frames"] == 130
    assert c["audio_start_sample"] == 0
    with wave.open(str(tmp_path / "canonical.wav"), "rb") as w:
        payload = w.readframes(240000)
        assert w.readframes(9600) == b"\0" * 38400
    with wave.open(str(ROOT / "tests/fixtures/generated/cfr.wav"), "rb") as w:
        assert payload == w.readframes(240000)
    atomic_json(
        OUT / "audio-ahead.json",
        c | {"source_sha512": sha512(source), "pcm_verified": True},
    )
