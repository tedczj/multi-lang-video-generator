import json
import os
from .util import uid, canonical


def emit(db, root, asset, kind, payload, execution=None, run=None, event_id=None):
    identity = event_id or uid("evt")
    if not db.one("SELECT id FROM events WHERE id=%s", (identity,)):
        seq = db.one(
            "SELECT COALESCE(MAX(seq),0)+1 AS n FROM events WHERE asset_sha512=%s",
            (asset,),
        )["n"]
        db.insert(
            "events",
            {
                "id": identity,
                "asset_sha512": asset,
                "execution_id": execution,
                "run_id": run,
                "seq": seq,
                "type": kind,
                "payload_json": json.dumps(payload),
            },
        )
    rebuild(db, root, asset)


def rebuild(db, root, asset):
    target = root / "videos" / asset / "events.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    with tmp.open("wb") as f:
        for row in db.query(
            "SELECT * FROM events WHERE asset_sha512=%s ORDER BY seq", (asset,)
        ):
            row["at"] = row["at"].isoformat() + "Z"
            row["payload_json"] = json.loads(row["payload_json"])
            f.write(canonical(row) + b"\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, target)
