"""Auxiliary fixed-ASR content check; never substitutes for human listening."""

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mlvideo.util import atomic_json, read_json, sha512


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--request", required=True)
    p.add_argument("--result", required=True)
    args = p.parse_args()
    r = read_json(Path(args.request))
    deployment = r["models"][0]
    work = Path(r["output_dir"])
    model_path = Path(deployment["model_path"])
    if sha512(model_path) != deployment["sha512"]:
        raise ValueError("Content ASR model hash mismatch")
    import torch
    import whisper

    torch.set_num_threads(8)
    start = time.monotonic()
    model = whisper.load_model(str(model_path), device="cpu")
    clip = read_json(Path(r["inputs"]["clip"][0]["path"]))
    result = model.transcribe(
        r["inputs"]["audio"][0]["path"],
        language="zh",
        temperature=0,
        fp16=False,
        condition_on_previous_text=False,
        word_timestamps=True,
    )
    atomic_json(work / "content.raw.json", result)
    expected, actual = [
        re.sub(r"[^\w]", "", s).lower() for s in (clip["text"], result["text"])
    ]
    row = list(range(len(actual) + 1))
    for i, x in enumerate(expected, 1):
        new = [i]
        for j, y in enumerate(actual, 1):
            new.append(min(row[j] + 1, new[-1] + 1, row[j - 1] + (x != y)))
        row = new
    report = {
        "expected_text": clip["text"],
        "recognized_text": result["text"],
        "character_error_rate": row[-1] / max(1, len(expected)),
        "status": "REVIEW",
        "reason": "ASR is not human truth; script/number/name spelling and word timestamps require listening",
        "model_sha512": deployment["sha512"],
        "source_sha512": r["inputs"]["audio"][0]["sha512"],
        "whisper_version": whisper.__version__,
        "seconds": time.monotonic() - start,
        "raw": result,
    }
    atomic_json(Path(args.result), report)


if __name__ == "__main__":
    main()
