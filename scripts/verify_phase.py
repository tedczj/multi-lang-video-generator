"""Fail-closed phase acceptance; each output directory is new and self-identifying."""

import argparse
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
import psutil
from mlvideo.cli import doctor
from mlvideo.config import ROOT, load
from mlvideo.util import atomic_json, read_json, sha512, code_provenance, file_lock
from mlvideo.db import DB
from mlvideo.engine import Engine


def resources(label):
    desktop = []
    for p in psutil.process_iter(["pid", "name", "memory_info"]):
        if any(
            x in (p.info["name"] or "").lower()
            for x in ("docker", "com.docker", "virtualization")
        ):
            desktop.append(
                {
                    "pid": p.pid,
                    "name": p.info["name"],
                    "rss_bytes": p.info["memory_info"].rss
                    if p.info["memory_info"]
                    else None,
                }
            )
    r = {
        "label": label,
        "at": time.time(),
        "controller_rss_bytes": psutil.Process().memory_info().rss,
        "docker_desktop_processes": desktop,
    }
    try:
        r["container"] = json.loads(
            subprocess.check_output(
                [
                    "docker",
                    "stats",
                    "--no-stream",
                    "--format",
                    "{{json .}}",
                    "multi-lang-video-generator-mysql-1",
                ],
                text=True,
            )
        )
        r["container_state"] = json.loads(
            subprocess.check_output(
                [
                    "docker",
                    "inspect",
                    "multi-lang-video-generator-mysql-1",
                    "--format",
                    "{{json .State}}",
                ],
                text=True,
            )
        )
    except Exception as e:
        r["error"] = str(e)
    return r


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--phase", type=int, choices=[1], required=True)
    p.add_argument("--fixtures", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--config")
    a = p.parse_args()
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    config = load(a.config)
    env = doctor(config)
    atomic_json(out / "environment.json", env)
    atomic_json(out / "code.json", code_provenance(out / "source.snapshot.zip"))
    manifest = read_json(Path(a.fixtures))
    atomic_json(out / "fixtures.json", manifest)
    resource = []
    db = DB(config)
    with file_lock(Path(config["data_root"]) / ".writer.lock"):
        Engine(db, config).guard()
        db.close()
        subprocess.run(
            ["docker", "compose", "restart", "mysql"], check=True, capture_output=True
        )
        deadline = time.monotonic() + 30
        while True:
            try:
                db = DB(config)
                db.close()
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.2)
        resource.append(resources("cold_process_restart_persistent_volume"))
        time.sleep(3)
        resource.append(resources("idle_after_restart"))
    atomic_json(out / "resources.json", resource)
    errors = []
    if manifest != read_json(ROOT / "tests/fixtures/manifest.json"):
        errors.append(
            "This phase suite requires the generated fixture manifest used by its independent oracles"
        )
    for c in manifest["cases"]:
        for key, hashkey in [("path", "sha512"), ("expected_path", "expected_sha512")]:
            if not (ROOT / c[key]).is_file() or sha512(ROOT / c[key]) != c[hashkey]:
                errors.append("Fixture mismatch: " + c["case_id"])
    if not env["ok"]:
        errors.append("doctor failed")
    child_env = os.environ | {"MLVIDEO_EVIDENCE": str(out)}
    if a.config:
        child_env["MLVIDEO_TEST_CONFIG"] = a.config
    argv = [
        sys.executable,
        "-m",
        "pytest",
        "tests/unit",
        "tests/integration",
        "-q",
        "--junitxml=" + str(out / "junit.xml"),
    ]
    container_log = (out / "container-samples.jsonl").open("w")
    monitor = subprocess.Popen(
        [
            "docker",
            "stats",
            "--format",
            "{{json .}}",
            "multi-lang-video-generator-mysql-1",
        ],
        stdout=container_log,
        stderr=subprocess.DEVNULL,
    )
    with (out / "pytest.log").open("w") as log:
        proc = subprocess.Popen(
            argv, cwd=ROOT, env=child_env, stdout=log, stderr=subprocess.STDOUT
        )
        peaks = {}
        while proc.poll() is None:
            for process in [
                psutil.Process(proc.pid),
                *psutil.Process(proc.pid).children(recursive=True),
            ]:
                try:
                    peaks[str(process.pid)] = {
                        "name": process.name(),
                        "rss_bytes": max(
                            peaks.get(str(process.pid), {}).get("rss_bytes", 0),
                            process.memory_info().rss,
                        ),
                    }
                except psutil.NoSuchProcess:
                    pass
            time.sleep(0.1)
    monitor.terminate()
    monitor.wait()
    container_log.close()
    resource.append(resources("after_continuous_tasks"))
    atomic_json(
        out / "resources.json",
        {
            "snapshots": resource,
            "test_process_peak_rss": peaks,
            "limitations": [
                "cold snapshot is a process restart with persistent volume, not empty-volume initialization; sampling is not a continuous high-water mark",
                "Docker Desktop RSS includes other running containers; not attributable solely to MySQL",
            ],
        },
    )
    cases = []
    required = {
        "T-DB": [
            "test_db_store_retry_lineage",
            "test_backup_restore_restart",
            "test_database_disconnect",
        ],
        "T-LOCK": ["test_lock_timeout_kill"],
        "T-STORE": ["test_db_store_retry_lineage"],
        "T-LINEAGE": ["test_db_store_retry_lineage"],
        "T-RETRY": ["test_db_store_retry_lineage"],
        "T-CONTRACT": ["test_paths_and_schemas", "test_db_store_retry_lineage"],
        "T-RECOVER": ["test_recover_windows"],
        "T-LOG": ["test_recover_windows"],
        "T-MEDIA": ["test_normalization"],
        "T-TIMELINE": ["test_independent_hold_counts", "test_rational_quantization"],
        "T-RENDER": [
            "test_render_independent_oracle",
            "test_multicut_fractional_samples",
            "test_render_fractional_long_tail",
            "test_full_media_recipe",
        ],
        "T-FAULT": [
            "test_lock_timeout_kill",
            "test_recover_windows",
            "test_bounded_disk_full",
        ],
        "T-BACKUP": ["test_backup_restore_restart"],
    }
    junit = ET.parse(out / "junit.xml")
    collected = list(junit.iter("testcase"))
    for identity, names in required.items():
        selected = [
            c
            for c in collected
            if any(c.attrib["name"].split("[")[0] == n for n in names)
        ]
        missing = [
            n
            for n in names
            if not any(c.attrib["name"].split("[")[0] == n for c in selected)
        ]
        bad = [
            c.attrib["name"]
            for c in selected
            if any(c.find(tag) is not None for tag in ("failure", "error", "skipped"))
        ]
        cases.append(
            {
                "id": identity,
                "status": "PASS" if selected and not missing and not bad else "FAIL",
                "tests": [c.attrib["name"] for c in selected],
                "missing": missing,
                "failed_or_skipped": bad,
                "evidence": "junit.xml and commands.jsonl",
            }
        )
    url = os.environ.get("MLVIDEO_YOUTUBE_URL") or manifest.get("youtube_url")
    authorization = os.environ.get("MLVIDEO_YOUTUBE_AUTHORIZATION") or manifest.get(
        "youtube_authorization"
    )
    download = {
        "id": "T-DOWNLOAD",
        "status": "FAIL",
        "reason": "Authorized YouTube URL and authorization statement are required",
        "attempts": [],
    }
    if url and authorization:
        download["authorization"] = authorization
        download["reason"] = (
            "YouTube download failed; see attempt output and acquisition stderr logs"
        )
        for i in range(2):
            argv = (
                [sys.executable, "-m", "mlvideo.cli"]
                + (["--config", a.config] if a.config else [])
                + ["download", url]
            )
            result = subprocess.run(argv, text=True, capture_output=True)
            atomic_json(
                out / f"download-{i + 1}.json",
                {
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
            )
            download["attempts"].append(
                {
                    "returncode": result.returncode,
                    "evidence": f"download-{i + 1}.json",
                    "result": json.loads(result.stdout)
                    if result.returncode == 0
                    else None,
                }
            )
        if all(r["returncode"] == 0 for r in download["attempts"]):
            ids = [r["result"]["acquisition_id"] for r in download["attempts"]]
            runtimes = [
                Path(config["data_root"]) / "incoming" / identity / "runtime.json"
                for identity in ids
            ]
            if len(set(ids)) == 2 and all(
                p.exists() and read_json(p)["stopped"] for p in runtimes
            ):
                download.update(status="PASS", reason=None)
    cases.append(download)
    result = {
        "phase": 1,
        "status": "PASS"
        if proc.returncode == 0
        and not errors
        and all(c["status"] == "PASS" for c in cases)
        else "INCOMPLETE",
        "business_qa": "REVIEW",
        "errors": errors,
        "pytest_returncode": proc.returncode,
        "collected_tests": len(collected),
        "cases": cases,
    }
    atomic_json(out / "case-results.json", result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
