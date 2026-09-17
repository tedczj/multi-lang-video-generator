"""Auxiliary Chinese transcription for a complete synthesized script; never human approval."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mlvideo.util import atomic_json, sha512


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--request", type=Path, required=True)
    a = p.parse_args()
    r = json.loads(a.request.read_text())
    cfg = r["model"]
    out = Path(r["output_dir"])
    out.mkdir(parents=True, exist_ok=True)
    if sha512(cfg["model_path"]) != cfg["sha512"]:
        raise ValueError("Chinese ASR model changed")
    import torch
    import whisper

    torch.set_num_threads(8)
    model = whisper.load_model(cfg["model_path"], device="cpu")
    records = []
    for item in r["items"]:
        target = out / (item["id"] + ".json")
        audio_hash = sha512(item["audio"])
        if target.exists():
            row = json.loads(target.read_text())
            if row["audio_sha512"] != audio_hash or row["expected"] != item["text"]:
                raise ValueError("Chinese ASR resume binding changed")
        else:
            result = model.transcribe(
                item["audio"],
                language="zh",
                fp16=False,
                temperature=0,
                condition_on_previous_text=False,
            )
            row = {
                "id": item["id"],
                "expected": item["text"],
                "recognized": result["text"],
                "audio_sha512": audio_hash,
                "model_sha512": cfg["sha512"],
                "status": "REVIEW",
                "raw": result,
            }
            atomic_json(target, row)
        records.append(row)
        atomic_json(
            out / "progress.json", {"done": len(records), "total": len(r["items"])}
        )
        print("Chinese ASR", len(records), "/", len(r["items"]), flush=True)
    atomic_json(
        out / "report.json",
        {
            "items": records,
            "status": "REVIEW",
            "reason": "ASR is auxiliary; not human timbre, naturalness or semantic acceptance",
        },
    )


if __name__ == "__main__":
    main()
