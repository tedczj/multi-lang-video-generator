import os
from pathlib import Path
from .util import read_json

ROOT = Path(__file__).resolve().parents[2]


def load(path=None):
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line and not line.startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key, value)
    config = read_json(Path(path) if path else ROOT / "config/pipeline.json")
    config["data_root"] = str((ROOT / config["data_root"]).resolve())
    return config


def connection_options(config, migration=False):
    db = config["database"].copy()
    password_env = db.pop("password_env")
    if migration:
        db["user"] = config["migration_user"]
        password_env = config["migration_password_env"]
    db["password"] = os.environ[password_env]
    return db
