import json
import os
import sys
from pathlib import Path
from . import events
from .contracts import MULTI_INPUT_PORTS, strategy, validate, validate_output
from .process import run_worker, confirm_stopped
from .util import (
    uid,
    sha512,
    read_json,
    atomic_json,
    confined,
    safe_name,
    code_provenance,
    durable_copy,
    ensure_no_secrets,
)


def fault(point):
    # Acceptance-only crash injection, never a recipe/worker parameter.
    if os.environ.get("MLVIDEO_TEST_FAULT") == point:
        os._exit(86)


class Engine:
    def __init__(self, db, config):
        self.db, self.config = db, config
        self.root = Path(config["data_root"])

    def guard(self):
        self.db.bind_root()
        acquisitions = self.db.query(
            "SELECT id FROM acquisitions WHERE state='RUNNING'"
        )
        if acquisitions:
            raise ValueError(
                f"Unfinished acquisition; recover incoming ACQUISITION first: {acquisitions}"
            )
        running = self.db.query("SELECT id FROM executions WHERE state='RUNNING'")
        running += self.db.query("SELECT id FROM runs WHERE state='RUNNING'")
        orphan = []
        for p in self.root.glob("videos/*/nodes/*/*/*/request.json"):
            if not self.db.one(
                "SELECT id FROM executions WHERE id=%s", (read_json(p)["execution_id"],)
            ):
                orphan.append(read_json(p)["execution_id"])
        if running or orphan:
            raise ValueError(f"Unfinished execution; recover first: {running + orphan}")

    def artifact(self, identity, asset):
        r = self.db.one(
            "SELECT a.*, e.asset_sha512, e.state, e.quality_status FROM artifacts a JOIN executions e ON e.id=a.execution_id WHERE a.id=%s",
            (identity,),
        )
        if not r or r["asset_sha512"] != asset or r["state"] != "SUCCEEDED":
            raise ValueError("Input is missing, cross-asset or producer not SUCCEEDED")
        if r["quality_status"] == "FAIL":
            raise ValueError("Input producer failed quality checks")
        p = confined(self.root, r["relative_path"])
        if p.stat().st_size != r["bytes"] or sha512(p) != r["sha512"]:
            raise ValueError("Artifact digest mismatch")
        return {
            "artifact_id": r["id"],
            "asset_sha512": asset,
            "execution_id": r["execution_id"],
            "relative_path": r["relative_path"],
            "schema_id": r["schema_id"],
            "sha512": r["sha512"],
            "bytes": r["bytes"],
            "path": str(p),
        }

    def run(
        self,
        asset,
        node,
        name,
        inputs,
        params,
        scope="video",
        retry_of=None,
        run_id=None,
        model_deployment=None,
    ):
        safe_name(node)
        safe_name(scope)
        required, outputs, defaults = strategy(node, name)
        if set(inputs) != set(required):
            raise ValueError(f"Expected ports {list(required)}")
        if set(params) - set(defaults):
            raise ValueError("Unknown strategy parameter")
        params = defaults | params
        ensure_no_secrets(params)
        models = (
            self.config.get("models", {}).get(f"{node}/{name}", [])
            if model_deployment is None
            else model_deployment
        )
        ensure_no_secrets(models)
        if node in {"N07", "N09", "N11", "N17"} and len(models) != 1:
            raise ValueError(f"Configure {node}/{name} in local models config")
        refs = {}
        for port, identity in inputs.items():
            identities = identity if isinstance(identity, list) else [identity]
            multiple = (node, name, port) in MULTI_INPUT_PORTS
            if (
                not identities
                or len(set(identities)) != len(identities)
                or (not multiple and len(identities) != 1)
            ):
                raise ValueError("Invalid port cardinality")
            refs[port] = []
            for item_id in identities:
                r = self.artifact(item_id, asset)
                if r["schema_id"] != required[port]:
                    raise ValueError(f"Wrong schema on port {port}")
                refs[port].append(r)
        if not self.db.one("SELECT sha512 FROM assets WHERE sha512=%s", (asset,)):
            raise ValueError("Unknown asset")
        if node == "N02":
            source = Path(params["source_path"])
            receipt = read_json(Path(params["receipt_path"]))
            if sha512(source) != asset or receipt["asset_sha512"] != asset:
                raise ValueError("Source/receipt does not belong to asset")
        version = self.db.one(
            "SELECT COALESCE(MAX(version),0)+1 n FROM executions WHERE asset_sha512=%s AND node=%s AND scope=%s",
            (asset, node, scope),
        )["n"]
        identity = uid("exec")
        directory = (
            self.root
            / "videos"
            / asset
            / "nodes"
            / node
            / scope
            / f"v{version:06}_{identity}"
        )
        work = directory / "work"
        work.mkdir(parents=True)
        request = {
            "protocol_version": 1,
            "execution_id": identity,
            "asset_sha512": asset,
            "run_id": run_id,
            "node": node,
            "scope": scope,
            "version": version,
            "strategy_id": name,
            "strategy_version": "1",
            "retry_of": retry_of,
            "inputs": refs,
            "params": params,
            "models": models,
            "output_dir": str(work),
            "code": code_provenance(directory / "source.snapshot.zip"),
        }
        validate("WorkerRequest.v1", request)
        atomic_json(directory / "request.json", request)
        fault("request")
        self.register_request(directory)
        fault("inputs")
        try:
            run_worker(
                [
                    sys.executable,
                    "-m",
                    "mlvideo.worker",
                    "--request",
                    str(directory / "request.json"),
                    "--result",
                    str(work / "worker-result.json"),
                ],
                work,
                self.config.get("worker_timeout", 600),
            )
            result = read_json(work / "worker-result.json")
            validate("WorkerResult.v1", result)
            if {
                a["port"]: a["schema_id"] for a in result["artifacts"]
            } != outputs or len(result["artifacts"]) != len(outputs):
                raise ValueError("Worker output ports/schema do not match registry")
            artifacts = []
            quality_status = "REVIEW"
            parents = [r["artifact_id"] for values in refs.values() for r in values]
            for a in result["artifacts"]:
                source = validate_output(work, a)
                if (
                    a["schema_id"] in {"AudioQA.v1", "QAReport.v1"}
                    and read_json(source)["overall"] == "FAIL"
                ):
                    quality_status = "FAIL"
                target = directory / "artifacts" / a["port"] / source.name
                durable_copy(source, target)
                artifacts.append(
                    {
                        "id": uid("art"),
                        "execution_id": identity,
                        "relative_path": str(target.relative_to(self.root)),
                        "schema_id": a["schema_id"],
                        "kind": a["kind"],
                        "sha512": sha512(target),
                        "bytes": target.stat().st_size,
                        "metadata_json": json.dumps(
                            {"port": a["port"], "lineage_kind": "conservative"}
                        ),
                        "parents": parents,
                    }
                )
            for port_refs in refs.values():
                for ref in port_refs:
                    self.artifact(ref["artifact_id"], asset)
            manifest = {
                "execution_id": identity,
                "request_sha512": sha512(directory / "request.json"),
                "artifacts": artifacts,
                "quality_status": quality_status,
                "code": request["code"],
            }
            atomic_json(directory / "manifest.json", manifest)
            atomic_json(
                directory / "manifest.sha512.json",
                {"sha512": sha512(directory / "manifest.json")},
            )
            fault("manifest")
            self.commit(directory)
        except BaseException as error:
            # A sealed result must remain recoverable after any DB/JSONL failure.
            if not (directory / "manifest.sha512.json").exists():
                try:
                    self.db.query(
                        "UPDATE executions SET state='FAILED', quality_status='FAIL', finished_at=UTC_TIMESTAMP(6) WHERE id=%s AND state='RUNNING'",
                        (identity,),
                    )
                    events.emit(
                        self.db,
                        self.root,
                        asset,
                        "execution.failed",
                        {"error": str(error)},
                        identity,
                        run_id,
                    )
                except Exception:
                    pass
            raise
        return self.result(identity)

    def register_request(self, directory):
        p = directory / "request.json"
        r = read_json(p)
        validate("WorkerRequest.v1", r)
        if sha512(directory / r["code"]["snapshot"]) != r["code"]["snapshot_sha512"]:
            raise ValueError("Code snapshot changed")
        row = {
            k: r[k]
            for k in (
                "run_id",
                "node",
                "scope",
                "version",
                "strategy_id",
                "strategy_version",
                "retry_of",
            )
        }
        row.update(
            id=r["execution_id"],
            asset_sha512=r["asset_sha512"],
            request_path=str(p.relative_to(self.root)),
            request_hash=sha512(p),
        )
        old = self.db.one("SELECT id FROM executions WHERE id=%s", (r["execution_id"],))
        if old:
            self.db.ensure("executions", row)
        else:
            self.db.insert("executions", row | {"state": "RUNNING"})
        for port, values in r["inputs"].items():
            for ordinal, ref in enumerate(values):
                self.artifact(ref["artifact_id"], r["asset_sha512"])
                self.db.ensure(
                    "execution_inputs",
                    {
                        "execution_id": r["execution_id"],
                        "port": port,
                        "ordinal": ordinal,
                        "artifact_id": ref["artifact_id"],
                    },
                    ("execution_id", "port", "ordinal"),
                )
        events.emit(
            self.db,
            self.root,
            r["asset_sha512"],
            "execution.started",
            {"request": row["request_path"], "request_sha512": row["request_hash"]},
            r["execution_id"],
            r["run_id"],
            r["execution_id"] + "_start",
        )

    def commit(self, directory):
        p = directory / "manifest.json"
        m = read_json(p)
        r = read_json(directory / "request.json")
        if read_json(directory / "manifest.sha512.json")["sha512"] != sha512(p):
            raise ValueError("Manifest seal changed")
        if (
            m["request_sha512"] != sha512(directory / "request.json")
            or m["execution_id"] != r["execution_id"]
        ):
            raise ValueError("Manifest/request mismatch")
        old = self.db.one("SELECT * FROM executions WHERE id=%s", (m["execution_id"],))
        if old["state"] not in ("RUNNING", "SUCCEEDED"):
            raise ValueError("Terminal execution cannot be overwritten")
        if old["manifest_hash"] and old["manifest_hash"] != sha512(p):
            raise ValueError("Sealed manifest changed")
        required, outputs, _ = strategy(r["node"], r["strategy_id"])
        if (
            len(m["artifacts"]) != len(outputs)
            or {
                json.loads(a["metadata_json"])["port"]: a["schema_id"]
                for a in m["artifacts"]
            }
            != outputs
        ):
            raise ValueError("Sealed output contract mismatch")
        expected_parents = {
            ref["artifact_id"] for refs in r["inputs"].values() for ref in refs
        }
        for a in m["artifacts"]:
            if (
                a["execution_id"] != r["execution_id"]
                or set(a["parents"]) != expected_parents
            ):
                raise ValueError("Sealed lineage mismatch")
            path = confined(self.root, a["relative_path"])
            if (
                not path.is_relative_to(directory / "artifacts")
                or sha512(path) != a["sha512"]
                or path.stat().st_size != a["bytes"]
            ):
                raise ValueError("Manifest artifact mismatch")
            validate_output(
                directory / "artifacts",
                {
                    "path": str(path.relative_to(directory / "artifacts")),
                    "schema_id": a["schema_id"],
                },
            )
            self.db.ensure("artifacts", {k: v for k, v in a.items() if k != "parents"})
            fault("partial_artifact")
            for parent in a["parents"]:
                self.db.ensure(
                    "artifact_parents",
                    {"artifact_id": a["id"], "parent_artifact_id": parent},
                    ("artifact_id", "parent_artifact_id"),
                )
        events.emit(
            self.db,
            self.root,
            r["asset_sha512"],
            "execution.finished",
            {"manifest": str(p.relative_to(self.root)), "sha512": sha512(p)},
            m["execution_id"],
            r["run_id"],
            m["execution_id"] + "_finish",
        )
        fault("event")
        self.db.query(
            "UPDATE executions SET state='SUCCEEDED',manifest_path=%s,manifest_hash=%s,finished_at=UTC_TIMESTAMP(6) WHERE id=%s AND state='RUNNING'",
            (str(p.relative_to(self.root)), sha512(p), m["execution_id"]),
        )
        fault("commit_ack")

    def result(self, identity):
        row = self.db.one(
            "SELECT id execution_id,version,state,quality_status,retry_of FROM executions WHERE id=%s",
            (identity,),
        )
        row["artifacts"] = self.db.query(
            "SELECT id artifact_id,schema_id,relative_path,metadata_json FROM artifacts WHERE execution_id=%s ORDER BY relative_path",
            (identity,),
        )
        return row

    def retry(self, asset, identity):
        r = self.db.one(
            "SELECT * FROM executions WHERE id=%s AND asset_sha512=%s",
            (identity, asset),
        )
        if not r:
            raise ValueError("Unknown execution")
        p = confined(self.root, r["request_path"])
        if sha512(p) != r["request_hash"]:
            raise ValueError("Request changed")
        req = read_json(p)
        if (self.root / "restore-origin.json").exists() and r["node"] == "N02":
            origin = Path(read_json(self.root / "restore-origin.json")["source_root"])
            for key in ("source_path", "receipt_path"):
                req["params"][key] = str(
                    self.root / Path(req["params"][key]).relative_to(origin)
                )
        return self.run(
            asset,
            r["node"],
            r["strategy_id"],
            {k: [ref["artifact_id"] for ref in v] for k, v in req["inputs"].items()},
            req["params"],
            r["scope"],
            identity,
            model_deployment=req["models"],
        )

    def recover(self, asset, identity):
        safe_name(identity)
        safe_name(asset)
        if identity.startswith("run_"):
            run = self.db.one(
                "SELECT * FROM runs WHERE id=%s AND asset_sha512=%s", (identity, asset)
            )
            if not run:
                raise ValueError("Unknown run")
            for ex in self.db.query(
                "SELECT id FROM executions WHERE run_id=%s AND state='RUNNING'",
                (identity,),
            ):
                self.recover(asset, ex["id"])
            self.db.query(
                "UPDATE runs SET state='INTERRUPTED',finished_at=UTC_TIMESTAMP(6) WHERE id=%s AND state='RUNNING'",
                (identity,),
            )
            return self.db.one("SELECT * FROM runs WHERE id=%s", (identity,))
        if identity.startswith("acq_"):
            from .store import recover_acquisition

            return recover_acquisition(self, identity)
        matches = list(
            (self.root / "videos" / asset / "nodes").glob(
                f"*/*/*_{identity}/request.json"
            )
        )
        if len(matches) != 1:
            raise ValueError("Execution request not found uniquely")
        directory = matches[0].parent
        confirm_stopped(directory / "runtime.json")
        self.register_request(directory)
        row = self.db.one("SELECT state FROM executions WHERE id=%s", (identity,))
        if (directory / "manifest.sha512.json").exists():
            self.commit(directory)
        elif row["state"] == "RUNNING":
            self.db.query(
                "UPDATE executions SET state='INTERRUPTED',finished_at=UTC_TIMESTAMP(6) WHERE id=%s",
                (identity,),
            )
        events.emit(
            self.db,
            self.root,
            asset,
            "execution.recovered",
            {"execution_id": identity},
            identity,
        )
        events.rebuild(self.db, self.root, asset)
        request = read_json(directory / "request.json")
        if request["run_id"]:
            self.db.query(
                "UPDATE runs SET state='INTERRUPTED',finished_at=UTC_TIMESTAMP(6) WHERE id=%s AND state='RUNNING'",
                (request["run_id"],),
            )
        return self.result(identity)

    def select(self, asset, role, artifact, reason):
        if not reason.strip():
            raise ValueError("Selection reason is required")
        self.artifact(artifact, asset)
        selection = {
            "id": uid("sel"),
            "asset_sha512": asset,
            "branch": "main",
            "role": safe_name(role),
            "artifact_id": artifact,
            "reason": reason,
        }
        atomic_json(
            self.root / "videos" / asset / "selections" / (selection["id"] + ".json"),
            selection,
        )
        self.db.insert("selections", selection)
        events.emit(self.db, self.root, asset, "selection.created", selection)
        return {"selection_id": selection["id"]}
