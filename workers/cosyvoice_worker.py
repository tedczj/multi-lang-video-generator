"""CosyVoice3 zero-shot worker; launched in its own model environment."""

import argparse
import importlib.metadata
import random
import subprocess
import sys
import time
import zipfile
from itertools import pairwise
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mlvideo.util import atomic_json, digest, read_json, sha512

MODEL = "FunAudioLLM/Fun-CosyVoice3-0.5B-2512"


def select_text(translation, unit_id):
    ids = [x["unit_id"] for x in translation["items"]]
    if (
        not unit_id
        or any(not i for i in ids)
        or len(set(ids)) != len(ids)
        or ids.count(unit_id) != 1
    ):
        raise ValueError("Missing or duplicate translation unit ID")
    return next(x["text"] for x in translation["items"] if x["unit_id"] == unit_id)


def reference_frames(reference):
    if "segments" not in reference:
        return reference["end_sample"] - reference["start_sample"]
    segments = reference["segments"]
    if (
        not segments
        or any(not 0 <= s["start_sample"] < s["end_sample"] for s in segments)
        or any(b["start_sample"] < a["end_sample"] for a, b in pairwise(segments))
        or reference["gap_samples"] < 0
    ):
        raise ValueError("Invalid pooled reference source intervals")
    count = sum(s["end_sample"] - s["start_sample"] for s in segments) + reference[
        "gap_samples"
    ] * (len(segments) - 1)
    if count != reference["frames"]:
        raise ValueError(
            "Pooled reference frame count does not match its source intervals"
        )
    return count


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    request = read_json(Path(args.request))
    if request["node"] != "N11" or request["strategy_id"] not in {
        "cosyvoice3_zero_shot",
        "cosyvoice3_from_bank",
    }:
        raise ValueError("Unsupported model strategy")
    deployment = request["models"][0]
    if deployment["model_id"] != MODEL:
        raise ValueError("Expected the selected CosyVoice3 checkpoint")
    source = Path(deployment["source_dir"]).resolve()
    model_dir = Path(deployment["model_dir"]).resolve()
    if not (model_dir / "cosyvoice3.yaml").is_file():
        raise ValueError("The local CosyVoice3 checkpoint is missing")
    work = Path(request["output_dir"])
    inputs = {p: refs[0] for p, refs in request["inputs"].items()}
    for ref in inputs.values():
        if sha512(Path(ref["path"])) != ref["sha512"]:
            raise ValueError("Model input hash changed")
    translation = read_json(Path(inputs["translation"]["path"]))
    reference = read_json(Path(inputs["reference"]["path"]))
    unit = request["params"]["unit_id"]
    text = select_text(translation, unit)
    if not text.strip() or not reference["text"].strip():
        raise ValueError("Empty text/reference transcript")
    if reference["audio_sha512"] != inputs["audio"]["sha512"]:
        raise ValueError("Reference transcript/audio binding changed")
    revision_file = model_dir / ".cache/huggingface/download/config.json.metadata"
    revision = (
        revision_file.read_text().splitlines()[0] if revision_file.exists() else None
    )
    if deployment.get("revision") and revision != deployment["revision"]:
        raise ValueError("Model revision does not match configured deployment")
    files = sorted(
        p
        for folder in [source / "cosyvoice", source / "third_party/Matcha-TTS/matcha"]
        for p in folder.rglob("*.py")
    )
    if not files:
        raise ValueError("The configured model source is missing")
    tree = [{"path": str(p.relative_to(source)), "sha512": sha512(p)} for p in files]
    snapshot = work / "model-source.zip"
    with zipfile.ZipFile(snapshot, "x", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, str(p.relative_to(source)))
    commit = dirty = None
    try:
        commit = subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "-C", str(source), "status", "--porcelain"], text=True
            )
        )
    except subprocess.CalledProcessError:
        pass
    sys.path[:0] = [str(source), str(source / "third_party/Matcha-TTS")]
    import numpy as np
    import soundfile as sf
    import torch
    from cosyvoice.cli.cosyvoice import AutoModel

    ref_info = sf.info(inputs["audio"]["path"])
    if (
        ref_info.samplerate != reference["sample_rate"]
        or ref_info.frames != reference_frames(reference)
        or not 0 < ref_info.duration <= 30
    ):
        raise ValueError(
            "Reference interval/format mismatch or duration outside (0,30] seconds"
        )
    torch.set_num_threads(8)
    model = AutoModel(model_dir=str(model_dir), fp16=False)
    if type(model).__name__ != "CosyVoice3":
        raise ValueError("Loaded model is not CosyVoice3")
    seed = request["params"]["seed"]
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    start = time.perf_counter()
    chunks = []
    for result in model.inference_zero_shot(
        text,
        "You are a helpful assistant.<|endofprompt|>" + reference["text"],
        inputs["audio"]["path"],
        stream=False,
        text_frontend=False,
    ):
        chunk = result["tts_speech"].detach().cpu()
        if chunk.ndim != 2 or chunk.shape[0] != 1 or chunk.shape[1] == 0:
            raise ValueError("Invalid audio chunk")
        chunks.append(chunk)
    elapsed = time.perf_counter() - start
    if not chunks:
        raise ValueError("Empty model output")
    waveform = torch.cat(chunks, dim=1).numpy().reshape(-1)
    if not np.isfinite(waveform).all():
        raise ValueError("Nonfinite model output")
    audio = work / "raw.wav"
    sf.write(audio, waveform, model.sample_rate, subtype="FLOAT")
    atomic_json(
        work / "clip.json",
        {
            "unit_id": unit,
            "speaker_id": reference["speaker_id"],
            "text": text,
            "translation_artifact_id": inputs["translation"]["artifact_id"],
            "reference_artifact_id": inputs["reference"]["artifact_id"],
            "reference_audio_artifact_id": inputs["audio"]["artifact_id"],
            "audio_sha512": sha512(audio),
            "sample_rate": model.sample_rate,
            "channels": 1,
            "frames": len(waveform),
        },
    )
    atomic_json(
        work / "receipt.json",
        {
            "requested_model": MODEL,
            "resolved_model": type(model).__name__,
            "revision": revision,
            "weight_sha512": None,
            "unknown_reason": "Revision from local HF download metadata; full checkpoint hash not measured by this invocation",
            "backend": "pytorch",
            "device": str(model.model.device),
            "dtype": str(next(model.model.llm.parameters()).dtype),
            "seed": seed,
            "sample_rate": model.sample_rate,
            "chunk_frames": [x.shape[1] for x in chunks],
            "generation_seconds": elapsed,
            "source_commit": commit,
            "source_dirty": dirty,
            "source_tree_sha512": digest(tree),
            "source_snapshot_sha512": sha512(snapshot),
            "packages": {
                n: importlib.metadata.version(n)
                for n in ["torch", "torchaudio", "transformers", "numpy", "onnxruntime"]
            },
        },
    )
    atomic_json(
        Path(args.result),
        {
            "protocol_version": 1,
            "state": "SUCCEEDED",
            "warnings": ["Content, timbre, leading audio and tail require N12/review"],
            "artifacts": [
                {"port": port, "path": path, "schema_id": schema, "kind": kind}
                for port, path, schema, kind in [
                    ("audio", "raw.wav", "Audio.v1", "raw_dub_audio"),
                    ("clip", "clip.json", "RawDubClip.v1", "raw_dub_clip"),
                    ("receipt", "receipt.json", "ModelReceipt.v1", "model_receipt"),
                    ("worker_source", "model-source.zip", "Binary.v1", "worker_source"),
                ]
            ],
        },
    )


if __name__ == "__main__":
    main()
