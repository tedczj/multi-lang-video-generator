"""Explicit media/ASR fixtures and a TTS test double. No real speech is synthesized."""
import array
import json
import math
import shutil
import subprocess
import sys
import wave
from pathlib import Path

from mlvideo.contracts import validate
from mlvideo.phase2_pipeline import ports
from mlvideo.studio.catalog import REFERENCE_CHECKS, VOICE_CHECKS
from mlvideo.studio.jobs import execute_next
from mlvideo.util import atomic_json, canonical, sha512, uid


def register(cat, asset, values, node="TEST", strategy="explicit_fixture"):
    identity = uid("exec")
    root = cat.root / "videos" / asset / "test-inputs" / identity
    root.mkdir(parents=True)
    request = root / "fixture.json"
    atomic_json(request, {"fixture": True, "execution_id": identity})
    cat.db.insert("executions", {"id": identity, "asset_sha512": asset, "node": node, "scope": identity,
        "version": 1, "strategy_id": strategy, "strategy_version": "TEST_ONLY", "state": "SUCCEEDED",
        "quality_status": "REVIEW", "request_path": str(request.relative_to(cat.root)), "request_hash": sha512(request)})
    result = {}
    for port, (schema, value) in values.items():
        out = root / (port + (Path(value).suffix if isinstance(value, Path) else ".json"))
        if isinstance(value, Path):
            shutil.copyfile(value, out)
        else:
            if schema != "Binary.v1": validate(schema, value)
            atomic_json(out, value)
        ident = uid("art")
        cat.db.insert("artifacts", {"id": ident, "execution_id": identity, "relative_path": str(out.relative_to(cat.root)),
            "schema_id": schema, "kind": port, "sha512": sha512(out), "bytes": out.stat().st_size,
            "metadata_json": canonical({"port": port, "fixture": True}).decode()})
        result[port] = ident
    return result


def seed_episode(cat, series=None, frequency=440, title="测试输入：提示音，不是真实人声"):
    series = series or cat.create_series("测试系列", "Fixture — NOT real voices")
    path = cat.root.parent / (uid("fixture") + ".mkv")
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=8",
        "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000:duration=8", "-c:v", "libx264", "-ac", "2",
        "-c:a", "pcm_s16le", str(path)], check=True)
    asset = sha512(path)
    source = cat.root / "videos" / asset / "source" / "original.bin"
    source.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(path, source)
    cat.db.ensure("assets", {"sha512": asset, "source_path": str(source.relative_to(cat.root)), "bytes": source.stat().st_size}, ("sha512",))
    src = register(cat, asset, {"source": ("Binary.v1", path)}, "N02", "source")["source"]
    ep = cat.create_episode(series["id"], title, asset_sha512=asset, source_artifact_id=src)
    probe = ports(cat.engine.run(asset, "N03", "ffprobe", {"source": src}, {}))
    can = ports(cat.engine.run(asset, "N04", "original", {"source": src, "probe": probe["probe"]}, {}))
    inv = ports(cat.engine.run(asset, "N05", "inventory", {"video": can["video"], "probe": probe["probe"]}, {}))
    rows = [{"id": "speech1", "start_sample": 24000, "end_sample": 96000, "text": "Hello there.", "speaker_id": None},
            {"id": "speech2", "start_sample": 192000, "end_sample": 264000, "text": "Let's go!", "speaker_id": None}]
    analysis = register(cat, asset, {"speech": ("SpeechTrack.v1", {"audio_artifact_id": can["audio"], "sample_rate": 48000,
        "segments": rows, "words": [], "protected_intervals": [{"start_sample": 0, "end_sample": 384000}],
        "coverage_status": "REVIEW", "warnings": ["Explicit ASR fixture, no speech recognition executed"]}),
        "vad": ("Binary.v1", {"sample_rate": 16000, "intervals": [{"start": 8000, "end": 32000}, {"start": 64000, "end": 88000}]})})
    captions = ports(cat.engine.run(asset, "N06", "studio_captions", {"speech": analysis["speech"], "inventory": inv["inventory"]}, {}))
    bindings = {k: can[k] for k in ("audio", "video", "canonical")} | analysis | captions
    rev = cat.create_revision(ep["id"], bindings, [{"start_sample": s["start_sample"], "end_sample": s["end_sample"],
                          "text": s["text"], "local_speaker": "local_00"} for s in rows])
    return series, ep, rev


def annotate_all(cat, rev, char, chinese=True):
    for s in cat.revision_view(rev["id"])["segments"]:
        cat.annotate(rev["id"], s["id"], s["annotation"]["version"] if s["annotation"] else 0, char["id"], "CONFIRMED",
                     s["text"], "你好，一起出发吧。" if chinese else "Hello, let's go.", "TEST REVIEWER", "状态机测试，不代表听感验收")


def run_next(cat):
    assert execute_next(cat)
    rows = cat.rows("studio_jobs", "state IN ('FAILED','INTERRUPTED')")
    if rows:
        raise AssertionError(rows[-1]["error_text"])


def profile_ready(cat, ep, rev, char):
    annotate_all(cat, rev, char)
    s = rev["segments"][0]
    ref = cat.create_reference(rev["id"], char["id"], s["start_sample"], s["end_sample"], s["text"], "TEST", "测试参考候选")
    run_next(cat)
    cat.approve_reference(ref["id"], "TEST", "测试参考批准状态机", dict.fromkeys(REFERENCE_CHECKS, True))
    p = cat.create_profile(char["id"], ref["id"], "TEST: fixture only")
    job = cat.preview(p["id"], "你好，测试音色。")
    run_next(cat)
    cat.publish_profile(p["id"], job["id"], "TEST", "仅测试发布门禁，不代表真实声音合格", dict.fromkeys(VOICE_CHECKS, True))
    return cat.get("studio_profiles", p["id"]), cat.get("studio_references", ref["id"])


def fake_tts(monkeypatch):
    """Replace ONLY the model boundary; other nodes use real subprocess workers."""
    import mlvideo.engine as eng
    original = eng.run_worker
    calls = []

    def dispatch(argv, work, timeout):
        if "mlvideo.worker" not in argv:
            return original(argv, work, timeout)
        req = json.loads(Path(argv[argv.index("--request")+1]).read_text())
        if req["node"] != "N11":
            return original(argv, work, timeout)
        calls.append(req)
        if getattr(dispatch, "fail", False):
            raise RuntimeError("TEST injected model failure")
        refs = {k: v[0] for k, v in req["inputs"].items()}
        reference = json.loads(Path(refs["reference"]["path"]).read_text())
        translations = json.loads(Path(refs["translation"]["path"]).read_text())
        u = next(u for u in translations["items"] if u["unit_id"] == req["params"]["unit_id"])
        frames = 24000 + len(calls)*240
        amplitude = 0 if getattr(dispatch, "silence", False) else 4000
        pcm = array.array("h", [int(amplitude*math.sin(2*math.pi*330*i/48000)) for i in range(frames)])
        if sys.byteorder != "little": pcm.byteswap()
        with wave.open(str(work/"raw.wav"), "wb") as wav:
            wav.setparams((1, 2, 48000, 0, "NONE", "not compressed")); wav.writeframes(pcm.tobytes())
        atomic_json(work/"clip.json", {"unit_id": u["unit_id"], "speaker_id": reference["speaker_id"], "text": u["text"],
            "translation_artifact_id": refs["translation"]["artifact_id"], "reference_artifact_id": refs["reference"]["artifact_id"],
            "reference_audio_artifact_id": refs["audio"]["artifact_id"], "audio_sha512": sha512(work/"raw.wav"),
            "sample_rate": 48000, "channels": 1, "frames": frames})
        (work/"TEST-NOT-A-MODEL.txt").write_text("Explicit TTS boundary fixture. No voice cloned.")
        atomic_json(work/"receipt.json", {"requested_model": "TEST_DOUBLE", "resolved_model": "TEST_TONE_NOT_SPEECH", "revision": None,
            "weight_sha512": None, "unknown_reason": "TEST DOUBLE ONLY", "backend": "test", "device": "cpu", "dtype": "int16",
            "seed": req["params"]["seed"], "sample_rate": 48000, "chunk_frames": [frames], "generation_seconds": 0,
            "source_commit": None, "source_dirty": None, "source_tree_sha512": "0"*128,
            "source_snapshot_sha512": sha512(work/"TEST-NOT-A-MODEL.txt"), "packages": {}})
        atomic_json(work/"worker-result.json", {"protocol_version": 1, "state": "SUCCEEDED", "warnings": ["EXPLICIT TEST FIXTURE"],
            "artifacts": [{"port": port, "path": filename, "schema_id": schema, "kind": port}
                for port, filename, schema in [("audio", "raw.wav", "Audio.v1"), ("clip", "clip.json", "RawDubClip.v1"),
                    ("receipt", "receipt.json", "ModelReceipt.v1"), ("worker_source", "TEST-NOT-A-MODEL.txt", "Binary.v1")]]})
    monkeypatch.setattr(eng, "run_worker", dispatch)
    return calls, dispatch
