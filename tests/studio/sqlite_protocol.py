"""TEST ONLY: SQLite SQL-protocol double; NEVER evidence of MySQL compatibility.

It exercises application transactions, domain constraints and real Engine files.
Production still uses PyMySQL/MySQL 8.4. The separate --mysql acceptance is needed.
"""
import contextlib
import datetime
import re
import sqlite3

from mlvideo.db import DB
from mlvideo.config import ROOT
from mlvideo.util import sha512


class SQLiteProtocolDB(DB):
    def __init__(self, config, migration=False):
        self.config, self.migration = config, migration
        self.conn = sqlite3.connect(config["test_sqlite"], isolation_level=None, timeout=20, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")

    def query(self, sql, args=()):
        if "@@server_uuid" in sql:
            return [{"id": "TEST_SQLITE_NOT_MYSQL"}]
        sql = sql.replace("%s", "?").replace("UTC_TIMESTAMP(6)", "strftime('%Y-%m-%dT%H:%M:%f','now')")
        result = [dict(r) for r in self.conn.execute(sql, args).fetchall()]
        for row in result:
            for k, v in list(row.items()):
                if (k.endswith("_at") or k == "at") and isinstance(v, str):
                    row[k] = datetime.datetime.fromisoformat(v)
        return result

    @contextlib.contextmanager
    def transaction(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    def migrate(self):
        for path in sorted((ROOT / "migrations").glob("*.sql")):
            raw = path.read_text()
            raw = re.sub(r"ENUM\([^)]*\)", "TEXT", raw)
            raw = raw.replace(" CHARACTER SET ascii", "").replace(" UNSIGNED", "")
            raw = re.sub(r"DATETIME\(6\)", "TEXT", raw)
            raw = re.sub(r",\s*INDEX\([^)]*\)", "", raw)
            for statement in raw.split(";"):
                if statement.strip():
                    self.query(statement)
            version = int(path.name.split("_")[0])
            self.ensure("schema_migrations", {"version": version, "sha512": sha512(path)}, ("version",))
        self.bind_root()
