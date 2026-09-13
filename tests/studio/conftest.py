from pathlib import Path
import sys
import pytest
from mlvideo.studio.catalog import Catalog
from .sqlite_protocol import SQLiteProtocolDB


@pytest.fixture
def config(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    return {"data_root": str(root), "test_sqlite": str(tmp_path / "test.sqlite"), "worker_timeout": 90,
            "database": {"host": "TEST", "port": 0, "database": "protocol_not_mysql"},
            "models": {"N11/cosyvoice3_zero_shot": [{"model_id": "FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
                        "python": sys.executable, "source_dir": "/TEST-ONLY-NO-MODEL", "model_dir": "/TEST-ONLY-NO-WEIGHTS"}]}}


@pytest.fixture
def catalog(config):
    db = SQLiteProtocolDB(config)
    db.migrate()
    yield Catalog(db, config)
    db.close()
