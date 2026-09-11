from __future__ import annotations
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")


def uid(prefix: str) -> str:
    return prefix + "_" + uuid.uuid4().hex


def sha512(path: Path) -> str:
    h = hashlib.sha512()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha512(canonical(value)).hexdigest()


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(canonical(value) + b"\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
        fsync_dir(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


@contextlib.contextmanager
def file_lock(path: Path, blocking: bool = False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def safe_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,140}", value) or value in {".", ".."}:
        raise ValueError(f"Invalid identifier: {value!r}")
    return value


def confined(base: Path, relative: str) -> Path:
    p = Path(relative)
    if p.is_absolute() or ".." in p.parts:
        raise ValueError("Absolute paths/path traversal not allowed")
    if not p.parts or any(
        (base / Path(*p.parts[:i])).is_symlink() for i in range(1, len(p.parts) + 1)
    ):
        raise ValueError("Empty paths and symlinks are forbidden")
    result = (base / p).resolve()
    if not result.is_relative_to(base.resolve()):
        raise ValueError("Path or symlink escapes its artifact root")
    return result


def ensure_no_secrets(value, path: str = "params") -> None:
    """Configuration stores secret REFERENCES only, never API keys/cookies."""
    prohibited = {
        "api_key",
        "apikey",
        "authorization",
        "password",
        "access_token",
        "refresh_token",
        "cookie",
        "cookies",
        "secret",
    }
    if isinstance(value, dict):
        for k, v in value.items():
            if k.lower() in prohibited and v not in (None, ""):
                raise ValueError(
                    f"Use a secret_ref/environment reference, not {path}.{k}"
                )
            ensure_no_secrets(v, path + "." + k)
    elif isinstance(value, list):
        for v in value:
            ensure_no_secrets(v, path)


def code_provenance(snapshot: Path) -> dict:
    import zipfile

    root = Path(__file__).resolve().parents[2]
    files = sorted(
        p
        for folder in (
            "src",
            "schemas",
            "migrations",
            "workers",
            "config",
            "scripts",
            "tests",
        )
        for p in (root / folder).rglob("*")
        if p.is_file()
        and "__pycache__" not in p.parts
        and "generated" not in p.parts
        and not p.name.startswith("local")
    )
    files += [
        root / p
        for p in ("pyproject.toml", "requirements.lock", "compose.yaml")
        if (root / p).exists()
    ]
    tree = [{"path": str(p.relative_to(root)), "sha512": sha512(p)} for p in files]
    with zipfile.ZipFile(snapshot, "x", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, str(p.relative_to(root)))
    result = {
        "git_commit": None,
        "dirty": None,
        "source_tree_sha512": digest(tree),
        "source_files": tree,
        "snapshot": snapshot.name,
        "snapshot_sha512": sha512(snapshot),
    }
    try:
        result["git_commit"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip()
        result["dirty"] = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=root, text=True
            ).strip()
        )
    except subprocess.SubprocessError:
        result["unknown_reason"] = "not a Git checkout"
    return result


def durable_copy(source: Path, target: Path):
    import shutil

    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, target.open("xb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())
    fsync_dir(target.parent)
