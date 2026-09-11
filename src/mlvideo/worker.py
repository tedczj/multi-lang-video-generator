import argparse
import shutil
import time
import uuid
from pathlib import Path
from . import media
from .contracts import validate
from .timeline import plan
from .util import read_json, atomic_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    r = read_json(Path(args.request))
    validate("WorkerRequest.v1", r)
    work = Path(r["output_dir"])
    p = r["params"]

    def source(port):
        return Path(r["inputs"][port][0]["path"])

    artifacts = []

    def output(port, path, schema, kind):
        artifacts.append(
            {"port": port, "path": path, "schema_id": schema, "kind": kind}
        )

    node = r["node"]
    if node == "N02":
        shutil.copyfile(p["source_path"], work / "source.bin")
        shutil.copyfile(p["receipt_path"], work / "receipt.json")
        output("source", "source.bin", "Binary.v1", "source")
        output("receipt", "receipt.json", "AcquisitionReceipt.v1", "receipt")
    elif node == "N03":
        atomic_json(work / "probe.json", media.probe(source("source"), work))
        output("probe", "probe.json", "MediaProbe.v1", "probe")
    elif node == "N04":
        media.normalize(source("source"), read_json(source("probe")), work, p["fps"])
        output("video", "canonical.mkv", "Video.v1", "canonical_video")
        output("audio", "canonical.wav", "Audio.v1", "canonical_audio")
        output("canonical", "canonical.json", "CanonicalMedia.v1", "canonical")
    elif node == "N16":
        atomic_json(
            work / "timeline.json",
            plan(read_json(source("canonical")), p["utterances"]),
        )
        output("timeline", "timeline.json", "TimelinePlan.v1", "timeline")
    elif node == "N18":
        media.render(
            source("video"), source("audio"), read_json(source("timeline")), work
        )
        for port, path, schema in [
            ("master", "master.mkv", "Video.v1"),
            ("audio", "master.wav", "Audio.v1"),
            ("preview", "preview.mp4", "Video.v1"),
            ("qa", "qa.json", "QAReport.v1"),
        ]:
            output(port, path, schema, "qa_report" if port == "qa" else port)
    elif node == "TEST":
        time.sleep(p["sleep"])
        if p["fill_bytes"]:
            with (work / "pressure.bin").open("wb") as f:
                remaining = p["fill_bytes"]
                while remaining:
                    n = min(remaining, 65536)
                    f.write(b"X" * n)
                    remaining -= n
        if p["fail"]:
            raise ValueError("Requested fixture failure")
        text = p["text"].upper() if r["strategy_id"] == "fixture_b" else p["text"]
        atomic_json(
            work / "fixture.json",
            {"text": text, "fixture": True, "invocation": uuid.uuid4().hex},
        )
        output("result", "fixture.json", "Fixture.v1", "fixture")
    else:
        raise ValueError("Unknown worker node")
    atomic_json(
        Path(args.result),
        {
            "protocol_version": 1,
            "state": "SUCCEEDED",
            "artifacts": artifacts,
            "warnings": [],
        },
    )


if __name__ == "__main__":
    main()
