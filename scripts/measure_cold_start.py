"""Measure initialization on a NEW MySQL volume; never alter the active database."""

import argparse
import json
import os
import subprocess
import time
import uuid
from pathlib import Path
import psutil
from mlvideo.config import ROOT, load
from mlvideo.util import atomic_json, read_json


def docker(*args, **kwargs):
    return subprocess.run(["docker", *args], capture_output=True, text=True, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    load()
    name = "mlvideo-cold-" + uuid.uuid4().hex[:12]
    volume = name + "-data"
    image = read_json(ROOT / "config/upstreams.lock.json")["mysql"]["image"]
    env = os.environ | {
        "MYSQL_ROOT_PASSWORD": os.environ["MLVIDEO_ROOT_PASSWORD"],
        "MYSQL_PASSWORD": os.environ["MLVIDEO_DB_PASSWORD"],
    }
    samples = []
    docker("volume", "create", volume, check=True)
    try:
        argv = [
            "create",
            "--name",
            name,
            "--memory",
            "768m",
            "-e",
            "MYSQL_ROOT_PASSWORD",
            "-e",
            "MYSQL_PASSWORD",
            "-e",
            "MYSQL_USER=mlvideo",
            "-e",
            "MYSQL_DATABASE=mlvideo",
            "-v",
            volume + ":/var/lib/mysql",
            "-v",
            str(ROOT / "config/mysql.cnf") + ":/etc/mysql/conf.d/mlvideo.cnf:ro",
            "-v",
            str(ROOT / "config/local-init.sql")
            + ":/docker-entrypoint-initdb.d/10-accounts.sql:ro",
            image,
        ]
        docker(*argv, env=env, check=True)
        atomic_json(
            out / "request.json",
            {
                "argv": argv,
                "env_names_only": ["MYSQL_ROOT_PASSWORD", "MYSQL_PASSWORD"],
                "fresh_volume": volume,
            },
        )
        started = time.monotonic()
        docker("start", name, check=True)
        ready = False
        with (out / "memory-samples.jsonl").open("w") as stream:
            while time.monotonic() - started < 120:
                sample = docker("exec", name, "cat", "/sys/fs/cgroup/memory.current")
                if sample.returncode:
                    raise RuntimeError("Cold-start container stopped unexpectedly")
                r = {
                    "elapsed_seconds": time.monotonic() - started,
                    "container_cgroup_bytes": int(sample.stdout),
                    "controller_rss_bytes": psutil.Process().memory_info().rss,
                }
                samples.append(r)
                stream.write(json.dumps(r) + "\n")
                stream.flush()
                if len(samples) % 10 == 0:
                    check = docker(
                        "exec",
                        name,
                        "sh",
                        "-c",
                        'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot -Nse "SELECT VERSION(), @@port, @@innodb_buffer_pool_size, @@max_connections, @@performance_schema, @@log_bin"',
                    )
                    if check.returncode == 0 and "\t3306\t" in check.stdout:
                        ready = True
                        atomic_json(
                            out / "mysql.json", {"query_result": check.stdout.strip()}
                        )
                        break
                time.sleep(0.05)
        state = json.loads(
            docker("inspect", name, "--format", "{{json .State}}", check=True).stdout
        )
        events = docker(
            "exec", name, "cat", "/sys/fs/cgroup/memory.events", check=True
        ).stdout
        result = {
            "status": "PASS" if ready and not state["OOMKilled"] else "FAIL",
            "image": image,
            "memory_limit_bytes": 768 * 1024**2,
            "initialization_seconds": time.monotonic() - started,
            "sample_count": len(samples),
            "sampled_peak_cgroup_bytes": max(
                s["container_cgroup_bytes"] for s in samples
            ),
            "controller_peak_rss_bytes": max(
                s["controller_rss_bytes"] for s in samples
            ),
            "memory_events": events,
            "state": state,
            "limitation": "Discrete samples include page cache; kernel does not expose memory.peak. This is a sampled maximum, not an exact instantaneous peak.",
        }
        atomic_json(out / "result.json", result)
        print(json.dumps(result))
        if result["status"] != "PASS":
            raise RuntimeError("Empty-volume initialization failed")
    finally:
        (out / "mysql.log").write_text(
            docker("logs", name).stdout + docker("logs", name).stderr
        )
        docker("rm", "-f", name, check=True)
        docker("volume", "rm", volume, check=True)


if __name__ == "__main__":
    main()
