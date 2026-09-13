"""Paused logical MySQL export, with a hash-verified complete file inventory."""

import json
import functools
import shutil
from pathlib import Path
from .util import atomic_json, read_json, sha512, confined, file_lock

TABLES = [
    "schema_migrations",
    "workspace_identity",
    "assets",
    "acquisitions",
    "runs",
    "executions",
    "artifacts",
    "execution_inputs",
    "artifact_parents",
    "selections",
    "events",
    "releases",
]

from .studio.catalog import TABLES as STUDIO_TABLES
TABLES += STUDIO_TABLES


def catalogue_locked(fn):
    @functools.wraps(fn)
    def wrapped(engine, *args, **kwargs):
        # Caller already owns the legacy media writer lock. Keep lock order.
        with file_lock(engine.root / ".studio.catalog.lock", blocking=True):
            return fn(engine, *args, **kwargs)
    return wrapped


@catalogue_locked
def backup(engine, out):
    out = Path(out).resolve()
    if out.is_relative_to(engine.root):
        raise ValueError("Backup must be outside active data root")
    out.mkdir(parents=True, exist_ok=False)
    dump = {}
    for table in TABLES:
        dump[table] = engine.db.query(f"SELECT * FROM `{table}`")
    # Datetimes preserve UTC precision; JSON columns remain JSON strings.
    (out / "database.json").write_text(
        json.dumps(dump, default=str, ensure_ascii=False, indent=2) + "\n"
    )
    files = {}
    for p in sorted(engine.root.rglob("*")):
        if p.is_symlink():
            raise ValueError("Symlink in backup source")
        if p.is_file() and p.name not in {".writer.lock", ".studio.catalog.lock", ".studio.worker.lock"}:
            relative = str(p.relative_to(engine.root))
            target = out / "files" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, target)
            files[relative] = {"sha512": sha512(target), "bytes": target.stat().st_size}
    manifest = {
        "source_root": str(engine.root),
        "database": {
            k: engine.config["database"][k] for k in ("host", "port", "database")
        },
        "server_uuid": engine.db.one("SELECT @@server_uuid id")["id"],
        "dump_sha512": sha512(out / "database.json"),
        "files": files,
    }
    atomic_json(out / "manifest.json", manifest)
    return {
        "backup": str(out),
        "files": len(files),
        "tables": {t: len(rows) for t, rows in dump.items()},
    }


@catalogue_locked
def restore(engine, source):
    source = Path(source).resolve()
    m = read_json(source / "manifest.json")
    target = engine.root
    endpoint = {k: engine.config["database"][k] for k in ("host", "port", "database")}
    same_database = (
        endpoint["database"] == m["database"]["database"]
        and engine.db.one("SELECT @@server_uuid id")["id"] == m["server_uuid"]
    )
    if (
        same_database
        or target == Path(m["source_root"])
        or target.is_relative_to(source)
        or source.is_relative_to(target)
    ):
        raise ValueError("Restore requires an isolated database AND data root")
    if any(p.name not in {".writer.lock", ".studio.catalog.lock", ".studio.worker.lock"} for p in target.iterdir()):
        raise ValueError("Restore root must be empty")
    if sha512(source / "database.json") != m["dump_sha512"]:
        raise ValueError("Backup dump changed")
    actual = {
        str(p.relative_to(source / "files"))
        for p in (source / "files").rglob("*")
        if p.is_file()
    }
    if actual != set(m["files"]):
        raise ValueError("Backup file inventory mismatch")
    for name, meta in m["files"].items():
        p = confined(source / "files", name)
        if sha512(p) != meta["sha512"] or p.stat().st_size != meta["bytes"]:
            raise ValueError("Backup file changed")
    for t in TABLES[2:]:
        if engine.db.one(f"SELECT 1 FROM `{t}` LIMIT 1"):
            raise ValueError("Restore database must be empty")
    dump = read_json(source / "database.json")
    for row in dump["schema_migrations"]:
        current = engine.db.one(
            "SELECT sha512 FROM schema_migrations WHERE version=%s", (row["version"],)
        )
        if not current or current["sha512"] != row["sha512"]:
            raise ValueError("Restore migration mismatch")
    for name in m["files"]:
        dst = confined(target, name)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / "files" / name, dst)
    for t in TABLES[2:]:
        pending = list(dump.get(t, []))
        # retry_of FKs require parent-first insertion, independently of SELECT order.
        while pending:
            ready = [
                r
                for r in pending
                if not r.get("retry_of")
                or engine.db.one(f"SELECT id FROM `{t}` WHERE id=%s", (r["retry_of"],))
            ]
            if not ready:
                raise ValueError("Invalid backup retry chain")
            for r in ready:
                engine.db.insert(t, r)
                pending.remove(r)
    atomic_json(target / "restore-origin.json", {"source_root": m["source_root"]})
    for name, meta in m["files"].items():
        if sha512(target / name) != meta["sha512"]:
            raise ValueError("Restored file mismatch")
    return {
        "state": "RESTORED",
        "files": len(m["files"]),
        "tables": {t: len(dump.get(t, [])) for t in TABLES},
    }
