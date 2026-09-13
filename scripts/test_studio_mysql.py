#!/usr/bin/env python3
"""Opt-in REAL MySQL + real FFmpeg smoke. TTS is an explicit test double.

Never run on production: requires a new *_studio_acceptance DB AND directory.
No DROP/TRUNCATE. Existing assets/studio data make this script refuse to proceed.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
from mlvideo.config import load
from mlvideo.db import DB
from mlvideo.studio.catalog import Catalog
from mlvideo.util import atomic_json, file_lock


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", default="evidence/studio-mysql-result.json")
    args=parser.parse_args()
    config=load(args.config)
    if not config["database"]["database"].endswith("_studio_acceptance") or not Path(config["data_root"]).name.endswith("_studio_acceptance"):
        raise ValueError("必须使用独立的 *_studio_acceptance 数据库与 data_root")
    db=DB(config,migration=True)
    try:
        with file_lock(Path(config["data_root"])/".writer.lock"):
            db.migrate()
        if db.one("SELECT id FROM studio_series LIMIT 1") or db.one("SELECT sha512 FROM assets LIMIT 1"):
            raise ValueError("验收数据库必须为空；本脚本不删除旧数据")
        version=db.one("SELECT VERSION() version")["version"]
        if not version.startswith("8.4."):
            raise ValueError("需要真实 MySQL 8.4，不接受 SQLite 或 MariaDB 代替")
        import pytest
        from studio.test_flow import test_cross_episode_generation_retry_selection_and_real_render
        patch=pytest.MonkeyPatch()
        try:
            test_cross_episode_generation_retry_selection_and_real_render(Catalog(db,config),patch)
        finally:
            patch.undo()
        report={"status":"PASS", "database":version, "media":"real FFmpeg", "tts":"EXPLICIT TEST DOUBLE; not voice quality",
                "source_recognition":"declared fixtures, not ASR execution"}
        atomic_json(Path(args.out),report)
        print(json.dumps(report,ensure_ascii=False))
    finally:
        db.close()


if __name__ == "__main__":
    main()
