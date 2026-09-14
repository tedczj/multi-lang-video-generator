import shutil
import sys
from pathlib import Path
from .process import run_worker
from .util import uid, now, sha512, atomic_json, durable_copy


DOWNLOAD_POLICY = "highest_resolution_v1"


def ingest(engine, source, download=False):
    identity = uid("acq")
    root = engine.root
    incoming = root / "incoming" / identity
    raw = incoming / "raw"
    raw.mkdir(parents=True)
    receipt = {
        "acquisition_id": identity,
        "kind": "download" if download else "local",
        "state": "RUNNING",
        "source": str(source),
        "started_at": now(),
    }
    atomic_json(incoming / "request.json", receipt)
    engine.db.insert(
        "acquisitions",
        {
            "id": identity,
            "kind": receipt["kind"],
            "state": "RUNNING",
            "receipt_path": str((incoming / "receipt.json").relative_to(root)),
        },
    )
    try:
        if download:
            receipt["download_policy"] = DOWNLOAD_POLICY
            argv = [
                sys.executable,
                "-m",
                "yt_dlp",
                "--js-runtimes",
                "node",
                "--no-playlist",
                "--no-cache-dir",
                "--force-overwrites",
                "--no-continue",
                "--write-info-json",
                "--socket-timeout",
                "30",
                "--retries",
                "1",
                "-f",
                "bv*+ba/b",
                "--format-sort-force",
                "-S",
                "res,fps",
                "--print-to-file",
                "after_move:filepath",
                str(raw / "final.txt"),
                "-o",
                str(raw / "media.%(ext)s"),
                str(source),
            ]
            run_worker(argv, raw, engine.config.get("worker_timeout", 600))
            lines = (raw / "final.txt").read_text().splitlines()
            if len(lines) != 1:
                raise ValueError("Expected exactly one final download")
            snapshot = Path(lines[0]).resolve()
            if not snapshot.is_relative_to(raw.resolve()) or snapshot.is_symlink():
                raise ValueError("Invalid download path")
        else:
            snapshot = raw / "media.bin"
            durable_copy(Path(source).resolve(), snapshot)
        probe_work = raw / "probe"
        run_worker(
            [
                sys.executable,
                "-m",
                "mlvideo.media",
                "--probe",
                str(snapshot),
                "--work",
                str(probe_work),
            ],
            probe_work,
            engine.config.get("worker_timeout", 600),
        )
        from .util import read_json

        info = read_json(probe_work / "probe.json")
        digest = sha512(snapshot)
        target = root / "videos" / digest / "source" / "original.bin"
        if target.exists():
            if sha512(target) != digest:
                raise ValueError("Existing source snapshot changed")
        else:
            durable_copy(snapshot, target)
        engine.db.ensure(
            "assets",
            {
                "sha512": digest,
                "source_path": str(target.relative_to(root)),
                "bytes": target.stat().st_size,
            },
            ("sha512",),
        )
        receipt.update(
            asset_sha512=digest, state="SUCCEEDED", finished_at=now(), probe=info
        )
        atomic_json(incoming / "receipt.json", receipt)
        archived = root / "videos" / digest / "acquisitions" / identity
        shutil.copytree(incoming, archived)
        engine.db.query(
            "UPDATE acquisitions SET asset_sha512=%s,state='SUCCEEDED',receipt_path=%s,finished_at=UTC_TIMESTAMP(6) WHERE id=%s",
            (digest, str((archived / "receipt.json").relative_to(root)), identity),
        )
        result = engine.run(
            digest,
            "N02",
            "source",
            {},
            {
                "source_path": str(target),
                "receipt_path": str(archived / "receipt.json"),
            },
        )
        artifact = next(
            a["artifact_id"]
            for a in result["artifacts"]
            if a["schema_id"] == "Binary.v1"
        )
        return {
            "acquisition_id": identity,
            "asset_sha512": digest,
            "source_artifact_id": artifact,
            "execution_id": result["execution_id"],
        }
    except BaseException as e:
        receipt.update(state="FAILED", error=str(e), finished_at=now())
        atomic_json(incoming / "receipt.json", receipt)
        engine.db.query(
            "UPDATE acquisitions SET state='FAILED',finished_at=UTC_TIMESTAMP(6) WHERE id=%s AND state='RUNNING'",
            (identity,),
        )
        raise


def recover_acquisition(engine, identity):
    from .process import confirm_stopped
    from .util import read_json

    incoming = engine.root / "incoming" / identity
    request = read_json(incoming / "request.json")
    confirm_stopped(incoming / "runtime.json")
    confirm_stopped(incoming / "raw" / "runtime.json")
    row = engine.db.one("SELECT * FROM acquisitions WHERE id=%s", (identity,))
    if not row:
        engine.db.insert(
            "acquisitions",
            {
                "id": identity,
                "kind": request["kind"],
                "state": "RUNNING",
                "receipt_path": str(
                    (incoming / "receipt.json").relative_to(engine.root)
                ),
            },
        )
    receipt = (
        read_json(incoming / "receipt.json")
        if (incoming / "receipt.json").exists()
        else None
    )
    if receipt and receipt["state"] == "SUCCEEDED":
        asset = receipt["asset_sha512"]
        source = engine.root / "videos" / asset / "source/original.bin"
        if sha512(source) != asset:
            raise ValueError("Acquisition source changed")
        engine.db.ensure(
            "assets",
            {
                "sha512": asset,
                "source_path": str(source.relative_to(engine.root)),
                "bytes": source.stat().st_size,
            },
            ("sha512",),
        )
        archive = engine.root / "videos" / asset / "acquisitions" / identity
        if not archive.exists():
            shutil.copytree(incoming, archive)
        if sha512(archive / "receipt.json") != sha512(incoming / "receipt.json"):
            raise ValueError("Archived receipt changed")
        engine.db.query(
            "UPDATE acquisitions SET asset_sha512=%s,state='SUCCEEDED',receipt_path=%s,finished_at=UTC_TIMESTAMP(6) WHERE id=%s AND state='RUNNING'",
            (asset, str((archive / "receipt.json").relative_to(engine.root)), identity),
        )
    else:
        engine.db.query(
            "UPDATE acquisitions SET state='INTERRUPTED',finished_at=UTC_TIMESTAMP(6) WHERE id=%s AND state='RUNNING'",
            (identity,),
        )
    return engine.db.one("SELECT * FROM acquisitions WHERE id=%s", (identity,))
