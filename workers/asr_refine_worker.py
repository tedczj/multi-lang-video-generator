"""Re-decode bounded English audio windows using on-screen subtitles as hints."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mlvideo.util import atomic_json, sha512


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--request", type=Path, required=True)
    args = p.parse_args()
    r = json.loads(args.request.read_text())
    model = r["model"]
    out = Path(r["output_dir"])
    out.mkdir(parents=True, exist_ok=True)
    if (
        sha512(model["model_path"]) != model["sha512"]
        or sha512(r["audio"]) != r["audio_sha512"]
    ):
        raise ValueError("ASR repair binding changed")
    import torch
    import whisper

    torch.set_num_threads(8)
    m = whisper.load_model(model["model_path"], device="cpu")
    audio = whisper.load_audio(r["audio"])
    patches = []
    for item in r["items"]:
        result = m.transcribe(
            audio[round(item["start"] * 16000) : round(item["end"] * 16000)],
            language="en",
            fp16=False,
            word_timestamps=True,
            temperature=0,
            condition_on_previous_text=False,
            initial_prompt=item["ocr_text"],
        )
        patches.append(item | {"result": result})
        atomic_json(out / "patches.json", patches)
        print(item["index"], result["text"], flush=True)
    atomic_json(
        out / "receipt.json",
        {
            "audio_sha512": r["audio_sha512"],
            "model_sha512": model["sha512"],
            "worker_sha512": sha512(__file__),
            "quality_status": "REVIEW",
        },
    )


if __name__ == "__main__":
    main()
