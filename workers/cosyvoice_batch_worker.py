"""Resident CosyVoice3 generation for a source-bound automatic script candidate."""

import argparse
import gc
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mlvideo.util import atomic_json, digest, sha512


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text())
    model_config = request["model"]
    work = Path(request["output_dir"])
    work.mkdir(parents=True, exist_ok=True)
    root = Path(model_config["source_dir"])
    weights = Path(model_config["model_dir"])
    revision = (
        (weights / ".cache/huggingface/download/config.json.metadata")
        .read_text()
        .splitlines()[0]
    )
    if revision != model_config["revision"]:
        raise ValueError("CosyVoice model revision changed")
    for ref in request["references"].values():
        if sha512(ref["audio"]) != ref["audio_sha512"]:
            raise ValueError("Voice reference changed")
    sys.path[:0] = [str(root), str(root / "third_party/Matcha-TTS")]
    import numpy as np
    import soundfile as sf
    import torch
    from cosyvoice.cli.cosyvoice import AutoModel

    torch.set_num_threads(8)
    started = time.monotonic()
    model = AutoModel(model_dir=str(weights), fp16=False)
    if type(model).__name__ != "CosyVoice3":
        raise ValueError("Expected CosyVoice3")
    atomic_json(
        work / "environment.json",
        {
            "requested_model": model_config["model_id"],
            "resolved_model": type(model).__name__,
            "revision": revision,
            "device": str(model.model.device),
            "dtype": str(next(model.model.llm.parameters()).dtype),
            "worker_sha512": sha512(__file__),
            "load_seconds": time.monotonic() - started,
            "quality_status": "REVIEW",
        },
    )
    cached = set()
    receipts = []
    for item in request["items"]:
        identity = item["id"]
        ref = request["references"][item["speaker"]]
        key = digest([model_config, item, ref, sha512(__file__)])
        audio = work / f"{identity}.raw.wav"
        receipt = work / f"{identity}.json"
        if receipt.exists():
            saved = json.loads(receipt.read_text())
            if saved["binding"] != key or sha512(audio) != saved["audio_sha512"]:
                raise ValueError("Completed voice changed; use a new run")
            receipts.append(saved)
            continue
        role = item["speaker"]
        if role not in cached:
            model.add_zero_shot_spk(
                "You are a helpful assistant.<|endofprompt|>" + ref["text"],
                ref["audio"],
                role,
            )
            cached.add(role)
        random.seed(42)
        np.random.seed(42)
        torch.manual_seed(42)
        start = time.monotonic()
        chunks = [
            x["tts_speech"].detach().cpu()
            for x in model.inference_zero_shot(
                item["translation"],
                "",
                "",
                zero_shot_spk_id=role,
                stream=False,
                speed=1.0,
                text_frontend=False,
            )
        ]
        if not chunks:
            raise ValueError("Empty CosyVoice output")
        data = torch.cat(chunks, dim=1).numpy().reshape(-1)
        if not np.isfinite(data).all() or len(data) == 0:
            raise ValueError("Invalid CosyVoice samples")
        sf.write(audio, data, model.sample_rate, subtype="FLOAT")
        value = {
            "id": identity,
            "speaker": role,
            "text": item["translation"],
            "binding": key,
            "reference_sha512": ref["audio_sha512"],
            "audio_sha512": sha512(audio),
            "sample_rate": model.sample_rate,
            "frames": len(data),
            "seconds": time.monotonic() - start,
            "peak": float(np.max(np.abs(data))),
            "quality_status": "REVIEW",
        }
        atomic_json(receipt, value)
        receipts.append(value)
        atomic_json(
            work / "progress.json",
            {
                "generated": len(receipts),
                "total": len(request["items"]),
                "last": identity,
            },
        )
        print(
            "TTS",
            len(receipts),
            "/",
            len(request["items"]),
            identity,
            len(data) / model.sample_rate,
            flush=True,
        )
    del model
    gc.collect()
    atomic_json(work / "result.json", {"items": receipts, "quality_status": "REVIEW"})


if __name__ == "__main__":
    main()
