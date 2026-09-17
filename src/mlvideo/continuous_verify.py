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
        start, end = u["source_start_sample"] * 4, u["source_end_sample"] * 4
        english = source_audio[start:end]
        if t["schema"] == "ContinuousExperiment.v1":
            segments = [("Chinese", dub), ("English", english)]
        elif t["schema"] == "ContinuousExperiment.v2" and t["audio_order"] == "en-zh":
            if (
                u["english_start_sample"] * 4 != cursor
                or u["chinese_start_sample"] * 4 != cursor + len(english) + SR * 4
            ):
                raise ValueError("English/Chinese placement mismatch")
            segments = [("English", english), ("Chinese", dub)]
        else:
            raise ValueError("Unsupported experiment audio order")
        for language, segment in segments:
            n = len(segment)
            if combined[cursor : cursor + n] != segment:
                raise ValueError(f"{language} PCM/order changed")
            cursor += n
            if combined[cursor : cursor + SR * 4] != bytes(SR * 4):
                raise ValueError("Missing language/group gap")
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


def verify_delivery(directory, movie=None):
    """Audit lossless construction and the sole lossy H.264/AAC delivery."""
    import av
    import numpy as np

    directory = Path(directory)
    t = json.loads((directory / "timeline.json").read_text())
    if t["schema"] != "ContinuousExperiment.v3" or t["audio_order"] != "en-zh":
        raise ValueError("Expected immediate English-to-Chinese v3 timeline")
    for path, expected in t["inputs"].items():
        if sha512(Path(path)) != expected:
            raise ValueError(f"Input changed: {path}")
    for name, expected in t["outputs"].items():
        if sha512(directory / name) != expected:
            raise ValueError(f"Work artifact changed: {name}")
    movie = Path(movie or t["output"])
    if sha512(movie) != t["output_sha512"]:
        raise ValueError("Output changed")
    manifest_path = Path(t["manifest"])
    manifest = json.loads(manifest_path.read_text())
    base = manifest_path.parent
    source = pcm(base / manifest["audio"])
    combined = pcm(directory / "combined.wav")
    cursor = source_cursor = 0
    for u, g in zip(t["units"], manifest["groups"], strict=True):
        a, b, e = (
            u["source_start_sample"],
            u["source_end_sample"],
            u["speech_end_sample"],
        )
        dub = pcm(base / g["dub"])
        if a != source_cursor or u["english_start_sample"] != cursor // 4:
            raise ValueError("Source or English start discontinuity")
        prefix, tail = source[a * 4 : e * 4], source[e * 4 : b * 4]
        if u["chinese_start_sample"] * 4 != cursor + len(prefix):
            raise ValueError("Chinese must immediately follow English speech")
        for data in (prefix, dub, tail):
            if combined[cursor : cursor + len(data)] != data:
                raise ValueError("Original/dub PCM or order changed")
            cursor += len(data)
        if u["chinese_end_sample"] - u["chinese_start_sample"] != len(dub) // 4:
            raise ValueError("Chinese duration mismatch")
        source_cursor = b
    if (
        cursor != len(combined)
        or source_cursor * 4 != len(source)
        or cursor // 4 != t["output_samples"]
    ):
        raise ValueError("Unaccounted audio samples")
    entries = t["continuous"]
    if [x[0] for x in entries] != list(range(len(t["source_frame_samples"]) - 1)):
        raise ValueError("Original frames missing/repeated/reordered")

    def feature(f):
        im = f.to_image()
        return np.asarray(
            im.crop((0, 0, im.width, im.height * 3 // 4)).resize((64, 36)),
            dtype=np.float32,
        )

    errors = []
    clock = []
    previous = -1
    with (
        av.open(str(base / manifest["source"])) as original,
        av.open(str(movie)) as output,
    ):
        v, a = output.streams.video[0], output.streams.audio[0]
        if (
            v.codec_context.name != "h264"
            or v.pix_fmt != "yuv420p"
            or a.codec_context.name != "aac"
        ):
            raise ValueError("Delivery must be H.264 yuv420p + AAC")
        for src, dst, (_, sample, duration) in zip(
            original.decode(video=0), output.decode(video=0), entries, strict=True
        ):
            actual = quantize(dst.pts * dst.time_base)
            if (
                (dst.width, dst.height) != tuple(t["dimensions"])
                or actual <= previous
                or abs(actual - sample) > 48
            ):
                raise ValueError("Video dimensions/timestamps mismatch")
            clock.append(abs(actual - sample))
            previous = actual
            errors.append(float(np.abs(feature(src) - feature(dst)).mean()))
        end = quantize((dst.pts + dst.duration) * dst.time_base)
        if abs(end - t["output_samples"]) > 96 or max(errors) > 8:
            raise ValueError("Video frame mapping/tail mismatch")
    with av.open(str(movie)) as output:
        decoded = []
        for f in output.decode(audio=0):
            if f.sample_rate != SR or f.layout.name != "stereo":
                raise ValueError("Unexpected AAC format")
            decoded.append(f.to_ndarray().T)
        actual = np.concatenate(decoded)
    expected = (
        np.frombuffer(combined, dtype="<i2").reshape(-1, 2).astype(np.float32) / 32768
    )
    if not len(expected) <= len(actual) <= len(expected) + 2048:
        raise ValueError("AAC duration outside codec padding")
    relative_error = float(
        np.sqrt(np.mean((actual[: len(expected)] - expected) ** 2))
        / max(1e-4, float(np.sqrt(np.mean(expected**2))))
    )
    if relative_error > 0.25:
        raise ValueError(
            f"AAC decoded audio differs from PCM timing/content: {relative_error}"
        )
    return {
        "automated_checks": "PASS",
        "dimensions": t["dimensions"],
        "frames": len(entries),
        "output_seconds": t["output_samples"] / SR,
        "added_english_chinese_gap_samples": 0,
        "source_and_dub_pcm_preserved": True,
        "max_frame_timestamp_error_samples": max(clock),
        "video_end_error_samples": abs(end - t["output_samples"]),
        "max_thumbnail_mae": max(errors),
        "aac_relative_rms_error": relative_error,
        "delivery_format": "MP4/H.264/yuv420p/AAC/faststart",
        "motion_review_units": t.get("motion_review_units", []),
        "output_sha512": t["output_sha512"],
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
    timeline = json.loads((args.directory / "timeline.json").read_text())
    report = (
        verify_delivery(args.directory)
        if timeline["schema"] == "ContinuousExperiment.v3"
        else verify(args.directory)
    )
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
