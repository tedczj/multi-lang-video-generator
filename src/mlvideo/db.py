import json
import pymysql
from .config import ROOT, connection_options
from .util import sha512


class DB:
    def __init__(self, config, migration=False):
        self.config, self.migration = config, migration
        self.connect()

    def connect(self):
        self.conn = pymysql.connect(
            **connection_options(self.config, self.migration),
            autocommit=True,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=5,
            read_timeout=30,
            write_timeout=30,
            init_command="SET time_zone = '+00:00'",
        )

    def close(self):
        self.conn.close()

    def query(self, sql, args=()):
        with self.conn.cursor() as cur:
            cur.execute(sql, args)
            return list(cur.fetchall())

    def one(self, sql, args=()):
        rows = self.query(sql, args)
        return rows[0] if rows else None

    def insert(self, table, row):
        # Identifiers are exclusively internal table/field names.
        keys = list(row)
        self.query(
            f"INSERT INTO `{table}` ({','.join('`' + k + '`' for k in keys)}) VALUES ({','.join(['%s'] * len(keys))})",
            tuple(row.values()),
        )

    def ensure(self, table, row, keys=("id",)):
        where = " AND ".join(f"`{key}`=%s" for key in keys)
        old = self.one(
            f"SELECT * FROM `{table}` WHERE {where}", tuple(row[k] for k in keys)
        )
        if old is None:
            self.insert(table, row)
        else:
            for key, value in row.items():
                a, b = old[key], value
                if key.endswith("_json"):
                    a, b = json.loads(a), json.loads(b)
                if a != b:
                    raise ValueError(f"Immutable {table}.{key} mismatch")

    def bind_root(self):
        self.ensure(
            "workspace_identity", {"id": 1, "data_root": self.config["data_root"]}
        )

    def migrate(self):
        paths = sorted((ROOT / "migrations").glob("[0-9]*.sql"))
        self.query(paths[0].read_text().split(";")[0])
        for path in paths:
            version = int(path.name.split("_")[0])
            old = self.one(
                "SELECT * FROM schema_migrations WHERE version=%s", (version,)
            )
            if old:
                if old["sha512"] != sha512(path):
                    raise ValueError("Migration checksum changed")
                continue
            for stmt in path.read_text().split(";"):
                if stmt.strip():
                    self.query(stmt)
            self.insert(
                "schema_migrations", {"version": version, "sha512": sha512(path)}
            )
        self.bind_root()
        return self.query("SELECT * FROM schema_migrations ORDER BY version")
