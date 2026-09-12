"""Offline candidate audit using actual PCM and decoded image pixels."""

import argparse
import array
import json
import subprocess
import wave
from pathlib import Path

from mlvideo.config import load
from mlvideo.db import DB
from mlvideo.engine import Engine
from mlvideo.phase2_pipeline import ports
from mlvideo.util import atomic_json, read_json


def audit(engine, result, out):
    out.mkdir(parents=True, exist_ok=False)
    executions = result["executions"]
    asset = engine.db.one(
        "SELECT asset_sha512 FROM executions WHERE id=%s",
        (executions["render"]["execution_id"],),
    )["asset_sha512"]

    def ref(identity):
        return engine.artifact(identity, asset)

    def path(identity):
        return Path(ref(identity)["path"])

    def value(identity):
        return read_json(path(identity))

    for execution in executions.values():
        for item in execution["artifacts"]:
            ref(item["artifact_id"])
    canonical = ports(executions["canonical"])
    render = ports(executions["render"])
    t = value(result["timeline"]["timeline"])
    layout = value(result["layout"]["layout"])
    with wave.open(str(path(canonical["audio"]))) as w:
        original = w.readframes(w.getnframes())
        source_frames = w.getnframes()
    with wave.open(str(path(render["audio"]))) as w:
        actual = array.array("h", w.readframes(w.getnframes()))
        output_frames = w.getnframes()
    # Reconstruct source sample stream by removing each inserted hold from rendered PCM.
    expected = array.array("h", [0]) * (output_frames * 2)
    src_pos = 0
    for piece in t["pieces"]:
        if piece["kind"] == "source":
            start, end = piece["output_start_sample"], piece["output_end_sample"]
            n = end - start
            expected[start * 2 : end * 2] = array.array(
                "h", original[src_pos * 4 : (src_pos + n) * 4]
            )
            src_pos += n
    if src_pos != source_frames:
        raise ValueError("Source PCM coverage mismatch")
    units = []
    for item, dub in zip(result["units"], t["dubs"], strict=True):
        with wave.open(str(path(item["audio"]))) as w:
            samples = array.array("h", w.readframes(w.getnframes()))
            count = w.getnframes()
        if count != dub["samples"]:
            raise ValueError("Actual dub duration differs")
        start = dub["start_sample"] * 2
        for i, sample in enumerate(samples):
            expected[start + i] += sample
        tr = value(item["translation"])
        text = next(i["text"] for i in tr["items"] if i["unit_id"] == item["unit_id"])
        unit = next(u for u in t["utterances"] if u["id"] == item["unit_id"])
        units.append(
            item
            | {
                "translated_text": text,
                "actual_dub_frames": count,
                "dub_start_seconds": dub["start_sample"] / 48000,
                "pre_gap_samples": dub["start_sample"] - unit["output_end_sample"],
            }
        )
    if expected != actual:
        raise ValueError("Rendered PCM differs from intact source plus full dub")
    master = path(render["master"])
    for i, event in enumerate(layout["events"]):
        when = (event["start_sample"] + event["end_sample"]) / 2 / 48000
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                str(when),
                "-i",
                str(master),
                "-frames:v",
                "1",
                str(out / f"subtitle-{i:03d}.png"),
            ],
            check=True,
        )
    decoded = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(master),
            "-map",
            "0:a:0",
            "-f",
            "s16le",
            "-c:a",
            "pcm_s16le",
            "-",
        ]
    )
    if decoded != actual.tobytes():
        raise ValueError("Muxed master audio changed")
    report = {
        "run_id": result["run_id"],
        "artifact_hashes": "PASS",
        "source_and_dub_pcm": "PASS",
        "muxed_pcm": "PASS",
        "output_samples": output_frames,
        "source_samples": source_frames,
        "units": units,
        "human_review": "PENDING",
        "screenshots": [p.name for p in out.glob("*.png")],
    }
    atomic_json(out / "audit.json", report)
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--runs", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    config = load(a.config)
    db = DB(config)
    engine = Engine(db, config)
    try:
        for i, row in enumerate(read_json(Path(a.runs))):
            result = audit(engine, row["result"], Path(a.out) / f"run-{i + 1}")
            print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
