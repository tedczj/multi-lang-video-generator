from .contracts import strategy
from .util import uid, atomic_json, sha512, safe_name


def run_recipe(engine, asset, recipe):
    steps = recipe["steps"]
    names = [s["id"] for s in steps]
    if len(set(names)) != len(names):
        raise ValueError("Duplicate recipe step")
    resolved = {}
    remaining = {s["id"]: s for s in steps}
    ordered = []
    # Freeze selections and validate the whole graph before executing any business.
    for s in steps:
        safe_name(s["id"])
        required, _, defaults = strategy(s["node"], s["strategy"])
        if set(s["inputs"]) != set(required) or set(s.get("params", {})) - set(
            defaults
        ):
            raise ValueError("Invalid recipe ports/parameters")
        resolved[s["id"]] = {}
        for port, b in s["inputs"].items():
            if isinstance(b, str):
                ref = engine.artifact(b, asset)
                if ref["schema_id"] != required[port]:
                    raise ValueError("Recipe schema mismatch")
                resolved[s["id"]][port] = b
            elif set(b) == {"selection"}:
                row = engine.db.one(
                    "SELECT artifact_id FROM selections WHERE asset_sha512=%s AND role=%s AND branch='main' ORDER BY created_at DESC,id DESC LIMIT 1",
                    (asset, b["selection"]),
                )
                if not row:
                    raise ValueError("Missing selection")
                ref = engine.artifact(row["artifact_id"], asset)
                if ref["schema_id"] != required[port]:
                    raise ValueError("Recipe selection schema mismatch")
                resolved[s["id"]][port] = row["artifact_id"]
            elif set(b) == {"step", "port"} and b["step"] in remaining:
                upstream = remaining[b["step"]]
                output = strategy(upstream["node"], upstream["strategy"])[1]
                if output.get(b["port"]) != required[port]:
                    raise ValueError("Recipe dependency schema mismatch")
                resolved[s["id"]][port] = b
            else:
                raise ValueError("Invalid recipe binding")
    while remaining:
        ready = [
            s
            for s in remaining.values()
            if all(
                not isinstance(b, dict) or b["step"] in ordered
                for b in resolved[s["id"]].values()
            )
        ]
        if not ready:
            raise ValueError("Recipe cycle")
        for s in ready:
            ordered.append(s["id"])
            del remaining[s["id"]]
    identity = uid("run")
    directory = engine.root / "videos" / asset / "runs" / identity
    atomic_json(directory / "plan.json", recipe)
    atomic_json(directory / "bindings.json", resolved)
    engine.db.insert(
        "runs",
        {
            "id": identity,
            "asset_sha512": asset,
            "recipe_path": str((directory / "plan.json").relative_to(engine.root)),
            "recipe_hash": sha512(directory / "plan.json"),
            "bindings_path": str(
                (directory / "bindings.json").relative_to(engine.root)
            ),
            "bindings_hash": sha512(directory / "bindings.json"),
            "state": "RUNNING",
        },
    )
    executions = {}
    artifacts = {}
    try:
        for name in ordered:
            s = next(x for x in steps if x["id"] == name)
            inputs = {
                p: artifacts[b["step"]][b["port"]] if isinstance(b, dict) else b
                for p, b in resolved[name].items()
            }
            result = engine.run(
                asset,
                s["node"],
                s["strategy"],
                inputs,
                s.get("params", {}),
                scope=name,
                run_id=identity,
            )
            executions[name] = result
            import json

            artifacts[name] = {
                json.loads(a["metadata_json"])["port"]: a["artifact_id"]
                for a in result["artifacts"]
            }
            atomic_json(directory / (name + ".bindings.json"), inputs)
        state = "REVIEW"  # Media execution does not satisfy business QA.
    except BaseException:
        engine.db.query(
            "UPDATE runs SET state='FAILED',finished_at=UTC_TIMESTAMP(6) WHERE id=%s",
            (identity,),
        )
        raise
    result = {
        "run_id": identity,
        "state": state,
        "bindings": resolved,
        "executions": executions,
    }
    atomic_json(directory / "result.json", result)
    engine.db.query(
        "UPDATE runs SET state=%s,finished_at=UTC_TIMESTAMP(6) WHERE id=%s",
        (state, identity),
    )
    return result
