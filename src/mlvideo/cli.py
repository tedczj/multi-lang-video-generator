import argparse
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from .config import load, ROOT
from .db import DB
from .engine import Engine
from .util import file_lock, read_json


def doctor(config):
    result = {
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "free_disk_bytes": shutil.disk_usage(
            config["data_root"] if Path(config["data_root"]).exists() else ROOT
        ).free,
        "tools": {},
        "missing": [],
    }
    for name, argv in {
        "ffmpeg": ["ffmpeg", "-version"],
        "ffprobe": ["ffprobe", "-version"],
        "node": ["node", "--version"],
        "docker": ["docker", "version", "--format", "{{json .}}"],
        "yt-dlp": [sys.executable, "-m", "yt_dlp", "--version"],
    }.items():
        try:
            result["tools"][name] = subprocess.check_output(
                argv, text=True, stderr=subprocess.STDOUT, timeout=20
            ).splitlines()[0]
        except (OSError, subprocess.SubprocessError) as e:
            result["missing"].append(name)
            result["tools"][name] = str(e)
    try:
        enc = subprocess.check_output(
            ["ffmpeg", "-encoders"], text=True, stderr=subprocess.DEVNULL
        )
        for codec in ("ffv1", "libx264", "aac", "pcm_s16le"):
            if codec not in enc:
                result["missing"].append(codec)
        result["image"] = read_json(ROOT / "config/upstreams.lock.json")["mysql"]
        subprocess.run(
            ["docker", "image", "inspect", result["image"]["image_id"]],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except Exception as e:
        result["missing"].append("image/codecs")
        result["image_error"] = str(e)
    try:
        db = DB(config)
        result["database"] = db.one(
            "SELECT VERSION() version, @@autocommit autocommit, @@innodb_buffer_pool_size buffer_pool, @@max_connections max_connections, @@performance_schema performance_schema, @@log_bin log_bin"
        )
        result["database"]["mysqlx"] = db.query(
            "SELECT PLUGIN_STATUS FROM INFORMATION_SCHEMA.PLUGINS WHERE PLUGIN_NAME='mysqlx'"
        )
        d = result["database"]
        if (
            not d["version"].startswith("8.4.")
            or d["buffer_pool"] != 134217728
            or d["max_connections"] != 10
            or d["performance_schema"]
            or d["log_bin"]
            or any(p["PLUGIN_STATUS"] == "ACTIVE" for p in d["mysqlx"])
        ):
            result["missing"].append("database configuration")
        db.close()
    except Exception as e:
        result["missing"].append("database")
        result["database_error"] = str(e)
    result["ok"] = not result["missing"] and sys.version_info >= (3, 11)
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("doctor", "migrate"):
        sub.add_parser(name)
    for name in ("ingest", "download"):
        s = sub.add_parser(name)
        s.add_argument("source")
    s = sub.add_parser("run")
    s.add_argument("asset")
    s.add_argument("node")
    s.add_argument("strategy")
    s.add_argument("--inputs", required=True)
    s.add_argument("--params", required=True)
    s = sub.add_parser("pipeline")
    s.add_argument("asset")
    s.add_argument("--recipe", required=True)
    s = sub.add_parser("candidate")
    s.add_argument("asset")
    s.add_argument("--source", required=True)
    s.add_argument("--settings", required=True)
    for name in ("retry", "recover"):
        s = sub.add_parser(name)
        s.add_argument("asset")
        s.add_argument("execution")
    s = sub.add_parser("select")
    s.add_argument("asset")
    s.add_argument("role")
    s.add_argument("artifact")
    s.add_argument("--reason", required=True)
    for name in ("history", "status"):
        s = sub.add_parser(name)
        s.add_argument("asset")
    s = sub.add_parser("backup")
    s.add_argument("--out", required=True)
    s = sub.add_parser("restore")
    s.add_argument("backup")
    args = p.parse_args()
    db = None
    try:
        config = load(args.config)
        if args.command == "doctor":
            result = doctor(config)
            print(json.dumps(result, ensure_ascii=False, default=str))
            return 0 if result["ok"] else 1
        db = DB(config, migration=args.command in ("migrate", "restore"))
        engine = Engine(db, config)
        if args.command in ("history", "status"):
            result = {
                "executions": db.query(
                    "SELECT * FROM executions WHERE asset_sha512=%s ORDER BY started_at,id",
                    (args.asset,),
                ),
                "acquisitions": db.query(
                    "SELECT * FROM acquisitions WHERE asset_sha512=%s", (args.asset,)
                ),
                "selections": db.query(
                    "SELECT * FROM selections WHERE asset_sha512=%s", (args.asset,)
                ),
            }
        else:
            with file_lock(engine.root / ".writer.lock"):
                if args.command == "migrate":
                    if db.one(
                        "SELECT TABLE_NAME FROM information_schema.tables WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='executions'"
                    ):
                        engine.guard()
                    result = db.migrate()
                elif args.command == "restore":
                    from .backup import restore

                    result = restore(engine, args.backup)
                else:
                    if args.command != "recover":
                        engine.guard()
                    else:
                        db.bind_root()
                    if args.command in ("ingest", "download"):
                        from .store import ingest

                        result = ingest(engine, args.source, args.command == "download")
                    elif args.command == "run":
                        result = engine.run(
                            args.asset,
                            args.node,
                            args.strategy,
                            read_json(Path(args.inputs)),
                            read_json(Path(args.params)),
                        )
                    elif args.command == "retry":
                        result = engine.retry(args.asset, args.execution)
                    elif args.command == "recover":
                        result = engine.recover(args.asset, args.execution)
                    elif args.command == "select":
                        result = engine.select(
                            args.asset, args.role, args.artifact, args.reason
                        )
                    elif args.command == "pipeline":
                        from .pipeline import run_recipe

                        result = run_recipe(
                            engine, args.asset, read_json(Path(args.recipe))
                        )
                    elif args.command == "candidate":
                        from .phase2_pipeline import run_candidate

                        result = run_candidate(
                            engine,
                            args.asset,
                            args.source,
                            read_json(Path(args.settings)),
                        )
                    elif args.command == "backup":
                        from .backup import backup

                        result = backup(engine, args.out)
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    except BlockingIOError:
        print(json.dumps({"error": "已有任务运行"}, ensure_ascii=False))
        return 1
    except Exception as e:
        print(
            json.dumps({"error": str(e), "type": type(e).__name__}, ensure_ascii=False)
        )
        return 1
    finally:
        if db:
            db.close()


if __name__ == "__main__":
    sys.exit(main())
