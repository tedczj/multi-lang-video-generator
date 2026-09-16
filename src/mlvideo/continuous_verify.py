"""Decode and independently audit a completed continuous A/B experiment."""

import argparse
import json
from pathlib import Path

from .continuous import SR, pcm
from .timeline import quantize
from .util import sha512


def verify(directory):
    import av
    import numpy as np

    t = json.loads((directory / "timeline.json").read_text())
    for path, expected in t["inputs"].items():
        if sha512(Path(path)) != expected:
            raise ValueError(f"Input changed: {path}")
    for name, expected in t["outputs"].items():
        if sha512(directory / name) != expected:
            raise ValueError(f"Output changed: {name}")
    manifest_path = next(Path(p) for p in t["inputs"] if Path(p).suffix == ".json")
    manifest = json.loads(manifest_path.read_text())
    base = manifest_path.resolve().parent
    combined = pcm(directory / "combined.wav")
    source_audio = pcm(base / manifest["audio"])
    if len(combined) != t["output_samples"] * 4:
        raise ValueError("Combined audio duration mismatch")
    cursor = 0
    for u, g in zip(t["units"], manifest["groups"], strict=True):
        dub = pcm(base / g["dub"])
        n = len(dub)
        if combined[cursor : cursor + n] != dub:
            raise ValueError("Chinese PCM changed")
        cursor += n
        if combined[cursor : cursor + SR * 4] != bytes(SR * 4):
            raise ValueError("Missing language gap")
        cursor += SR * 4
        start, end = u["source_start_sample"] * 4, u["source_end_sample"] * 4
        if combined[cursor : cursor + end - start] != source_audio[start:end]:
            raise ValueError("English/source PCM changed")
        cursor += end - start
        if combined[cursor : cursor + SR * 4] != bytes(SR * 4):
            raise ValueError("Missing group gap")
        cursor += SR * 4
    if cursor != len(combined):
        raise ValueError("Unaccounted audio tail")

    def feature(frame):
        im = frame.to_image()
        return np.asarray(
            im.crop((0, 0, im.width, im.height * 3 // 4)).resize((64, 36)),
            dtype=np.float32,
        )

    with av.open(str(base / manifest["source"])) as container:
        stream = container.streams.video[0]
        geometry = (stream.width, stream.height)
        originals = [feature(f) for f in container.decode(video=0)]
    if [e[0] for e in t["continuous"]] != list(range(len(originals))):
        raise ValueError(
            "Continuous timeline does not preserve every source frame once"
        )
    reports = {}
    for mode in ("continuous", "hold"):
        entries = t[mode]
        with av.open(str(directory / f"{mode}.mkv")) as container:
            stream = container.streams.video[0]
            if (stream.width, stream.height) != geometry:
                raise ValueError("Output dimensions changed")
            errors, clock_errors = [], []
            count = 0
            previous = -1
            for f, (index, sample, duration) in zip(
                container.decode(video=0), entries, strict=True
            ):
                actual = quantize(f.pts * f.time_base)
                if actual <= previous or abs(actual - sample) > 48:
                    raise ValueError("Output frame timestamp mismatch")
                previous = actual
                clock_errors.append(abs(actual - sample))
                errors.append(float(np.abs(feature(f) - originals[index]).mean()))
                count += 1
            if max(errors) > 8:
                raise ValueError("Decoded scene differs from expected source frame")
            video_end = quantize((f.pts + f.duration) * f.time_base)
            if abs(video_end - t["output_samples"]) > 96:
                raise ValueError("Video tail duration mismatch")
        with av.open(str(directory / f"{mode}.mkv")) as container:
            raw = b"".join(
                bytes(f.planes[0])[: f.samples * 4] for f in container.decode(audio=0)
            )
        if raw != combined:
            raise ValueError("Decoded lossless PCM differs from A/B source")
        with av.open(str(directory / f"{mode}.mp4")) as container:
            for f, (_, sample, _) in zip(
                container.decode(video=0), entries, strict=True
            ):
                if (f.width, f.height) != geometry or abs(
                    quantize(f.pts * f.time_base) - sample
                ) > 48:
                    raise ValueError("MP4 video dimensions/timing differ")
        with av.open(str(directory / f"{mode}.mp4")) as container:
            decoded_audio_samples = sum(f.samples for f in container.decode(audio=0))
        if (
            not t["output_samples"]
            <= decoded_audio_samples
            <= t["output_samples"] + 2048
        ):
            raise ValueError("MP4 AAC duration differs beyond codec padding")
        reports[mode] = {
            "frames": count,
            "max_frame_timestamp_error_samples": max(clock_errors),
            "max_thumbnail_mae": max(errors),
            "decoded_pcm_equal": True,
            "mp4_full_decode": True,
            "video_end_error_samples": abs(video_end - t["output_samples"]),
            "output_sha512": sha512(directory / f"{mode}.mkv"),
        }
    return {
        "automated_checks": "PASS",
        "dimensions": list(geometry),
        "output_seconds": len(combined) / 4 / SR,
        "variants": reports,
        "source_frames": len(originals),
        "continuous_added_hold_frames": 0,
        "pixel_comparison": "Lossy H.264: per-frame central ROI thumbnail MAE <= 8; not pixel identity or human motion acceptance",
        "subtitle_layout": "Geometric checks in renderer; source page detection and visual review remain separate",
        "human_listening": "REVIEW",
        "formal_acceptance": "REVIEW",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        parser.error("Report exists; use a new version")
    report = verify(args.directory)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
