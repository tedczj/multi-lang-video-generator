"""Independent Whisper environment. Unverified coverage protects the entire source."""

import argparse
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mlvideo.util import atomic_json, digest, read_json, sha512


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--request", required=True)
    p.add_argument("--result", required=True)
    args = p.parse_args()
    r = read_json(Path(args.request))
    work = Path(r["output_dir"])
    ref = r["inputs"]["audio"][0]
    deployment = r["models"][0]
    model_path = Path(deployment["model_path"])
    if (
        sha512(model_path) != deployment["sha512"]
        or sha512(ref["path"]) != ref["sha512"]
    ):
        raise ValueError("ASR model/input hash mismatch")
    import torch
    import whisper

    sys.path.insert(0, deployment["vad_package_dir"])
    from silero_vad import get_speech_timestamps, load_silero_vad, read_audio

    vad_path = Path(deployment["vad_package_dir"]) / "silero_vad/data/silero_vad.onnx"
    if sha512(vad_path) != deployment["vad_sha512"]:
        raise ValueError("VAD weight identity mismatch")
    vad = get_speech_timestamps(
        read_audio(ref["path"], sampling_rate=16000),
        load_silero_vad(onnx=True),
        sampling_rate=16000,
    )
    atomic_json(
        work / "vad.json",
        {
            "sample_rate": 16000,
            "intervals": vad,
            "model_sha512": sha512(vad_path),
            "model": "silero-vad-6.2.1",
            "onnx": True,
        },
    )

    torch.set_num_threads(8)
    start = time.monotonic()
    model = whisper.load_model(str(model_path), device="cpu")
    result = model.transcribe(
        ref["path"],
        language="en",
        fp16=False,
        temperature=0,
        word_timestamps=True,
        condition_on_previous_text=False,
    )
    atomic_json(work / "asr.json", result)
    with wave.open(ref["path"]) as audio:
        count = audio.getnframes()
        if audio.getframerate() != 48000:
            raise ValueError("Expected canonical 48 kHz PCM")
    segments, words = [], []
    for seg in result["segments"]:
        a, b = round(seg["start"] * 48000), min(count, round(seg["end"] * 48000))
        if not 0 <= a < b or not seg["text"].strip():
            raise ValueError("Invalid ASR interval: REVIEW")
        segments.append(
            {
                "id": "speech_" + digest([ref["artifact_id"], seg["id"]])[:20],
                "start_sample": a,
                "end_sample": b,
                "text": seg["text"].strip(),
                "speaker_id": None,
            }
        )
        for word in seg.get("words", []):
            words.append(
                {
                    "start_sample": max(0, round(word["start"] * 48000)),
                    "end_sample": min(count, round(word["end"] * 48000)),
                    "text": word["word"],
                }
            )
    # Independent VAD is retained, but requires human coverage/speaker confirmation.
    from mlvideo.speech import apply_annotation

    protected, coverage, annotation = apply_annotation(
        r["params"].get("annotation"), ref, count, segments, vad
    )

    track = {
        "audio_artifact_id": ref["artifact_id"],
        "sample_rate": 48000,
        "segments": segments,
        "words": words,
        "protected_intervals": protected,
        "coverage_status": coverage,
        "warnings": [
            "ASR/VAD estimates and speaker mapping require human confirmation; unconfirmed coverage protects entire source"
        ],
    }
    atomic_json(work / "speech.json", track)
    atomic_json(
        work / "receipt.json",
        {
            "model": "whisper",
            "model_sha512": sha512(model_path),
            "whisper_version": whisper.__version__,
            "torch_version": torch.__version__,
            "device": "cpu",
            "dtype": "float32",
            "seconds": time.monotonic() - start,
            "word_alignment": "Whisper cross-attention DTW; not independent forced alignment",
            "speaker_identity": None,
            "reason": "No automatic diarization deployment configured; optional hash-bound human annotation",
            "annotation": annotation,
            "vad_sha512": deployment["vad_sha512"],
        },
    )
    atomic_json(
        Path(args.result),
        {
            "protocol_version": 1,
            "state": "SUCCEEDED",
            "warnings": track["warnings"],
            "artifacts": [
                {"port": port, "path": path, "schema_id": schema, "kind": port}
                for port, path, schema in [
                    ("speech", "speech.json", "SpeechTrack.v1"),
                    ("raw", "asr.json", "Binary.v1"),
                    ("vad", "vad.json", "Binary.v1"),
                    ("receipt", "receipt.json", "Binary.v1"),
                ]
            ],
        },
    )


if __name__ == "__main__":
    main()
