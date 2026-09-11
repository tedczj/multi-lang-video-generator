"""All subprocesses share the worker group, with an exec gate before business starts."""

import os
import signal
import socket
import subprocess
import sys
import psutil
from .util import atomic_json, read_json, now


def run_worker(argv, work, timeout):
    work.mkdir(parents=True, exist_ok=True)
    runtime = work.parent / "runtime.json"
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        in {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "PYTHONPATH", "SSL_CERT_FILE"}
    }
    with (
        (work / "stdout.log").open("wb") as out,
        (work / "stderr.log").open("wb") as err,
    ):
        p = subprocess.Popen(
            [sys.executable, "-m", "mlvideo.process", *argv],
            stdin=subprocess.PIPE,
            stdout=out,
            stderr=err,
            start_new_session=True,
            env=env,
        )
        record = {
            "host": socket.gethostname(),
            "pid": p.pid,
            "created": psutil.Process(p.pid).create_time(),
            "argv": argv,
            "started_at": now(),
            "stopped": False,
        }
        old = signal.getsignal(signal.SIGTERM)

        def terminate(signum, frame):
            raise KeyboardInterrupt("controller terminated")

        signal.signal(signal.SIGTERM, terminate)
        try:
            atomic_json(runtime, record)
            p.stdin.write(b"G")
            p.stdin.close()
            rc = p.wait(timeout=timeout)
            if rc:
                raise RuntimeError(f"Worker exit {rc}; see {work / 'stderr.log'}")
        finally:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            p.wait()
            record.update(stopped=True, finished_at=now(), returncode=p.returncode)
            atomic_json(runtime, record)
            signal.signal(signal.SIGTERM, old)


def confirm_stopped(runtime):
    if not runtime.exists():
        return  # The exec gate cannot start business before runtime is durable.
    r = read_json(runtime)
    if r["host"] != socket.gethostname():
        raise ValueError("Worker host differs; cannot confirm termination")
    if r["stopped"]:
        return
    try:
        p = psutil.Process(r["pid"])
        if p.create_time() != r["created"]:
            raise ValueError("PID reused; cannot confirm old process group")
        if p.status() != psutil.STATUS_ZOMBIE:
            os.killpg(r["pid"], signal.SIGKILL)
            psutil.wait_procs([p], timeout=5)
    except psutil.NoSuchProcess:
        pass
    # Group descendants can survive their group leader.
    try:
        os.killpg(r["pid"], 0)
    except ProcessLookupError:
        r.update(stopped=True, finished_at=now(), recovered=True)
        atomic_json(runtime, r)
        return
    raise ValueError("Worker process group still exists; recovery blocked")


if __name__ == "__main__":
    if sys.stdin.buffer.read(1) != b"G":
        sys.exit(2)
    os.execvpe(sys.argv[1], sys.argv[1:], os.environ)
