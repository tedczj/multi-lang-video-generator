"""Real Phase 2 acceptance. Missing inputs/review produce an INCOMPLETE report."""

import json
import subprocess
import sys
from pathlib import Path

from mlvideo.cli import doctor
from mlvideo.config import ROOT, load
from mlvideo.db import DB
from mlvideo.engine import Engine
from mlvideo.phase2_pipeline import run_candidate
from mlvideo.store import ingest
from mlvideo.util import atomic_json, code_provenance, file_lock, read_json, sha512


def verify(a):
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    config = load(a.config)
    atomic_json(out / "code.json", code_provenance(out / "source.snapshot.zip"))
    environment = doctor(config)
    atomic_json(out / "environment.json", environment)
    manifest = read_json(Path(a.fixtures))
    atomic_json(out / "fixtures.json", manifest)
    cases = []
    runs = []
    errors = (
        [] if environment["ok"] else [{"environment": environment.get("missing", [])}]
    )
    with (out / "pytest.log").open("w") as log:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/unit",
                "tests/integration/test_phase2_media.py",
                "-q",
                "--junitxml=" + str(out / "junit.xml"),
            ],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    cases.append(
        {
            "id": "T-CONTRACT",
            "status": "PASS" if proc.returncode == 0 else "FAIL",
            "evidence": "junit.xml",
        }
    )
    if manifest.get("phase") != 2 or not manifest.get("samples"):
        errors.append(
            "Phase 2 requires a phase=2 manifest with real source samples; Phase 1 synthetic fixtures cannot substitute"
        )
    elif environment["ok"]:
        db = None
        try:
            db = DB(config)
            engine = Engine(db, config)
            with file_lock(engine.root / ".writer.lock"):
                engine.guard()
                for sample in manifest["samples"]:
                    try:
                        source = Path(sample["path"])
                        if sha512(source) != sample["sha512"]:
                            raise ValueError("Source hash mismatch")
                        imported = ingest(engine, str(source), False)
                        atomic_json(
                            out / (sample["id"] + "-acquisition.json"), imported
                        )
                        for attempt in range(2):
                            result = run_candidate(
                                engine,
                                imported["asset_sha512"],
                                imported["source_artifact_id"],
                                sample["settings"],
                            )
                            runs.append(
                                {
                                    "sample": sample["id"],
                                    "attempt": attempt + 1,
                                    "result": result,
                                }
                            )
                            atomic_json(out / "runs.json", runs)
                        for key in ("translate_0", "tts_0"):
                            old = runs[-1]["result"]["executions"][key]
                            retry = engine.retry(
                                imported["asset_sha512"], old["execution_id"]
                            )
                            old_req = db.one(
                                "SELECT request_path FROM executions WHERE id=%s",
                                (old["execution_id"],),
                            )
                            new_req = db.one(
                                "SELECT request_path FROM executions WHERE id=%s",
                                (retry["execution_id"],),
                            )
                            before = read_json(engine.root / old_req["request_path"])
                            after = read_json(engine.root / new_req["request_path"])
                            if (
                                before["inputs"] != after["inputs"]
                                or before["models"] != after["models"]
                                or before["params"] != after["params"]
                                or retry["execution_id"] == old["execution_id"]
                                or retry["version"] <= old["version"]
                            ):
                                raise ValueError(
                                    "Retry failed immutable input/version checks"
                                )
                            atomic_json(out / f"{sample['id']}-{key}-retry.json", retry)
                        cases.append(
                            {
                                "id": "T-RETRY",
                                "sample": sample["id"],
                                "status": "PASS",
                                "evidence": f"{sample['id']}-*-retry.json",
                            }
                        )
                    except Exception as error:  # noqa: BLE001 - retain each failed sample and continue acceptance
                        errors.append({"sample": sample["id"], "error": str(error)})
                        atomic_json(out / "errors.json", errors)
                        print(json.dumps(errors[-1], ensure_ascii=False), flush=True)
        except Exception as error:  # noqa: BLE001 - always retain environment/lock failures
            errors.append({"controller": str(error)})
        finally:
            if db is not None:
                db.close()
    completed = len(runs)
    for name in (
        "T-OCR",
        "T-SPEECH",
        "T-UTTERANCE",
        "T-TRANSLATE",
        "T-TTS",
        "T-AUDIO",
        "T-TIMELINE",
        "T-LAYOUT",
        "T-RENDER",
        "T-QA",
        "T-LINEAGE",
    ):
        cases.append(
            {
                "id": name,
                "status": "REVIEW" if completed else "NOT_RUN",
                "reason": "Technical runs are evidence only; formal corpus, independent oracles and human review remain required",
            }
        )
    report = {
        "phase": 2,
        "status": "INCOMPLETE",
        "business_qa": "REVIEW",
        "errors": errors,
        "completed_candidate_runs": completed,
        "cases": cases,
        "unresolved": [
            "Real single/multi-speaker video corpus and independent 20-frame OCR truth required",
            "Independent VAD/diarization/forced alignment coverage not yet validated; current worker protects full source",
            "Human translation, voice, leading noise, tail and pixel-layout review required for each exact render",
            "N19 requires a hash-bound human decision covering every unresolved check; none supplied in this run",
        ],
    }
    atomic_json(out / "case-results.json", report)
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 1
