"""Full-video speaker segmentation/embedding/clustering in an isolated environment."""

import argparse
import importlib.metadata
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mlvideo.util import atomic_json, read_json, sha512


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--request", required=True)
    p.add_argument("--result", required=True)
    args = p.parse_args()
    r = read_json(Path(args.request))
    work = Path(r["output_dir"])
    model = r["models"][0]
    ref = r["inputs"]["audio"][0]
    for path, expected in [
        (ref["path"], ref["sha512"]),
        (model["segmentation_path"], model["segmentation_sha512"]),
        (model["embedding_path"], model["embedding_sha512"]),
    ]:
        if sha512(path) != expected:
            raise ValueError("Speaker model/input hash changed")
    import subprocess

    import sherpa_onnx
    import soundfile as sf

    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            ref["path"],
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(work / "diarization.wav"),
        ],
        check=True,
    )
    audio, sr = sf.read(work / "diarization.wav", dtype="float32")
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=model["segmentation_path"], window_shift_ratio=0.1
            ),
            num_threads=4,
            provider="cpu",
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=model["embedding_path"], num_threads=4, provider="cpu"
        ),
        clustering=sherpa_onnx.FastClusteringConfig(
            num_clusters=-1, threshold=r["params"]["cluster_threshold"]
        ),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not config.validate():
        raise ValueError("Invalid diarization deployment")
    diarizer = sherpa_onnx.OfflineSpeakerDiarization(config)
    if sr != diarizer.sample_rate:
        raise ValueError("Diarization sample rate mismatch")
    start = time.monotonic()

    def progress(done, total):
        if done % 20 == 0 or done == total:
            atomic_json(work / "progress.json", {"done": done, "total": total})
        return 0

    segments = diarizer.process(audio, callback=progress).sort_by_start_time()
    with wave.open(ref["path"]) as pcm:
        count = pcm.getnframes()
    labels = {}
    turns = []
    for item in segments:
        if item.speaker not in labels:
            labels[item.speaker] = f"speaker_{len(labels) + 1:02d}"
        a, b = max(0, round(item.start * 48000)), min(count, round(item.end * 48000))
        if a < b:
            turns.append(
                {"start_sample": a, "end_sample": b, "speaker_id": labels[item.speaker]}
            )
    if not turns:
        raise ValueError("No speaker turns detected")
    track = {
        "source_audio_artifact_id": ref["artifact_id"],
        "sample_rate": 48000,
        "estimated_speaker_count": len(labels),
        "turns": turns,
        "quality_status": "REVIEW",
        "confidence": None,
        "unknown_reason": "Cluster labels are estimates; no calibrated identity confidence or character names returned",
    }
    atomic_json(work / "speakers.json", track)
    (work / "turns.rttm").write_text(
        "".join(
            f"SPEAKER source 1 {t['start_sample'] / 48000:.6f} {(t['end_sample'] - t['start_sample']) / 48000:.6f} <NA> <NA> {t['speaker_id']} <NA> <NA>\n"
            for t in turns
        )
    )
    atomic_json(
        work / "receipt.json",
        {
            "sherpa_onnx_version": importlib.metadata.version("sherpa-onnx"),
            "segmentation_sha512": model["segmentation_sha512"],
            "embedding_sha512": model["embedding_sha512"],
            "config": str(config),
            "seconds": time.monotonic() - start,
            "device": "cpu",
            "sample_rate": sr,
        },
    )
    atomic_json(
        Path(args.result),
        {
            "protocol_version": 1,
            "state": "SUCCEEDED",
            "warnings": [track["unknown_reason"]],
            "artifacts": [
                {"port": port, "path": path, "schema_id": schema, "kind": port}
                for port, path, schema in [
                    ("speakers", "speakers.json", "SpeakerTrack.v1"),
                    ("raw", "turns.rttm", "Binary.v1"),
                    ("receipt", "receipt.json", "Binary.v1"),
                ]
            ],
        },
    )


if __name__ == "__main__":
    main()
