"""Original CosyVoice3 on Mac: resident FP32, thread counts and reference caching."""

import argparse
import importlib.metadata
import random
import resource
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mlvideo.util import atomic_json, read_json, sha512


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    request = read_json(Path(args.request))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    deployment = request["model"]
    source = Path(deployment["source_dir"])
    sys.path[:0] = [str(source), str(source / "third_party/Matcha-TTS")]
    import numpy as np
    import soundfile as sf
    import torch
    from cosyvoice.cli.cosyvoice import AutoModel

    for ref in request["references"].values():
        if sha512(ref["audio"]) != ref["audio_sha512"]:
            raise ValueError("Benchmark reference changed")
    torch.set_num_threads(8)
    started = time.monotonic()
    model = AutoModel(model_dir=deployment["model_dir"], fp16=False)
    load_seconds = time.monotonic() - started
    if str(model.model.device) != "cpu":
        raise ValueError("This comparison requires the original CPU FP32 path")
    environment = {
        "backend": "official-pytorch",
        "device": str(model.model.device),
        "load_seconds": load_seconds,
        "main_module_dtypes": {
            name: sorted(
                {str(p.dtype) for p in getattr(model.model, name).parameters()}
            )
            for name in ("llm", "flow", "hift")
        },
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("torch", "torchaudio", "transformers", "onnxruntime", "numpy")
        },
        "source_commit": subprocess.check_output(
            [
                "/Library/Developer/CommandLineTools/usr/bin/git",
                "-C",
                str(source),
                "rev-parse",
                "HEAD",
            ],
            text=True,
        ).strip(),
        "model_revision": deployment["revision"],
        "script_sha512": sha512(__file__),
        "model_storage": deployment["model_dir"],
        "cache_kind": "Reference conditioning only; every output synthesized anew",
    }
    atomic_json(out / "environment.json", environment)
    results = []

    def generate(label, reference, threads, seed, cached=False):
        torch.set_num_threads(threads)
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        ref = request["references"][reference]
        cpu = resource.getrusage(resource.RUSAGE_SELF)
        start = time.monotonic()
        chunks = list(
            model.inference_zero_shot(
                request["target"],
                ""
                if cached
                else "You are a helpful assistant.<|endofprompt|>" + ref["text"],
                "" if cached else ref["audio"],
                zero_shot_spk_id=("bench_" + reference) if cached else "",
                stream=False,
                speed=1.0,
                text_frontend=False,
            )
        )
        elapsed = time.monotonic() - start
        cpu_after = resource.getrusage(resource.RUSAGE_SELF)
        audio = (
            torch.cat([item["tts_speech"] for item in chunks], dim=1)
            .detach()
            .cpu()
            .numpy()
            .reshape(-1)
        )
        if not np.isfinite(audio).all():
            raise ValueError("Non-finite synthesized audio")
        path = out / (label + ".wav")
        sf.write(path, audio, model.sample_rate, subtype="FLOAT")
        record = {
            "label": label,
            "reference": reference,
            "reference_sha512": ref["audio_sha512"],
            "threads": threads,
            "seed": seed,
            "reference_cached": cached,
            "seconds": elapsed,
            "frames": len(audio),
            "sample_rate": model.sample_rate,
            "duration_seconds": len(audio) / model.sample_rate,
            "rtf": elapsed / (len(audio) / model.sample_rate),
            "cpu_seconds": cpu_after.ru_utime
            + cpu_after.ru_stime
            - cpu.ru_utime
            - cpu.ru_stime,
            "process_peak_rss_bytes": cpu_after.ru_maxrss,
            "chunk_frames": [item["tts_speech"].shape[1] for item in chunks],
            "sha512": sha512(path),
            "audio": path.name,
        }
        results.append(record)
        atomic_json(out / "results.json", results)
        print(label, round(elapsed, 3), flush=True)

    generate("first_request", "fixed", 8, 41)
    start = time.monotonic()
    ref = request["references"]["fixed"]
    model.add_zero_shot_spk(
        "You are a helpful assistant.<|endofprompt|>" + ref["text"],
        ref["audio"],
        "bench_fixed",
    )
    atomic_json(
        out / "conditioning-cache.json",
        {
            "build_seconds": time.monotonic() - start,
            "reference_sha512": ref["audio_sha512"],
        },
    )
    for repeat, seed in enumerate(request["seeds"]):
        for threads in request["threads"]:
            for cached in (False, True) if repeat % 2 == 0 else (True, False):
                generate(
                    f"fixed_t{threads}_{'cached' if cached else 'uncached'}_s{seed}",
                    "fixed",
                    threads,
                    seed,
                    cached,
                )
    atomic_json(
        out / "complete.json",
        {
            "generated_requests": len(results),
            "status": "EXECUTED",
            "human_timbre_review": "PENDING",
        },
    )


if __name__ == "__main__":
    main()
