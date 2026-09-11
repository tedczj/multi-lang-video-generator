"""Create local-only credentials once; never print their values."""

import os
import secrets
from pathlib import Path

path = Path(".env")
with path.open("x") as f:
    os.chmod(path, 0o600)
    for key in ("DB", "MIGRATION", "ROOT"):
        f.write(f"MLVIDEO_{key}_PASSWORD={secrets.token_hex(24)}\n")
values = dict(line.split("=", 1) for line in path.read_text().splitlines())
sql = Path("config/local-init.sql")
sql.write_text(
    "CREATE USER 'mlvideo_migrate'@'%' IDENTIFIED BY '"
    + values["MLVIDEO_MIGRATION_PASSWORD"]
    + "';\nGRANT ALL ON mlvideo.* TO 'mlvideo_migrate'@'%';\nREVOKE ALL PRIVILEGES ON mlvideo.* FROM 'mlvideo'@'%';\nGRANT SELECT, INSERT, UPDATE ON mlvideo.* TO 'mlvideo'@'%';\n"
)
os.chmod(sql, 0o600)
print("Created .env and config/local-init.sql (0600)")
