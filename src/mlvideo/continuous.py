"""Bounded, standalone Chinese-first continuous-video A/B experiment.

The manifest uses original frame indices; it is not an N16 Timeline.v1 artifact.
"""

import argparse
import json
import shutil
import subprocess
import wave
from fractions import Fraction as F
from itertools import pairwise
from pathlib import Path

from .subtitles import chinese_baseline, wrap
from .timeline import quantize
from .util import sha512

SR = 48000


def pcm(path):
    with wave.open(str(path), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (2, 2, SR):
            raise ValueError("Expected stereo 48 kHz s16 PCM")
        data = wav.readframes(wav.getnframes())
    if not data or len(data) % 4:
        raise ValueError("Empty or incomplete PCM")
    return data


def plan(pts, groups, dub_samples):
    if (
        len(pts) < 2
        or pts[0] != 0
        or any(type(x) is not int for x in pts)
        or any(b <= a for a, b in pairwise(pts))
    ):
        raise ValueError("Invalid source PTS")
    if not groups or len(groups) != len(dub_samples):
        raise ValueError("Missing groups or dubs")
    cursor = output = 0
    units, continuous, hold = [], [], []
    for group, n in zip(groups, dub_samples, strict=True):
        a, b = group["start_frame"], group["end_frame"]
        h = group["hold_frame"]
        if (
            type(a) is not int
            or type(b) is not int
            or a != cursor
            or not 0 <= a < b < len(pts)
            or type(h) is not int
            or not a <= h < b
        ):
            raise ValueError(
                "Groups must partition every source frame, with an in-group hold frame"
            )
        if type(n) is not int or n <= 0:
            raise ValueError("Invalid dub duration")
        duration = pts[b] - pts[a]
        length = n + SR + duration + SR
        speed = F(duration, length)
        if speed < F(2, 5):
            raise ValueError("Video below 0.4x: regroup/review; no freeze fallback")
        unit = dict(
            group,
            source_start_sample=pts[a],
            source_end_sample=pts[b],
            output_start_sample=output,
            output_end_sample=output + length,
            dub_samples=n,
            english_start_sample=output + n + SR,
            video_speed=str(speed),
        )
        units.append(unit)
        mapped = [
            output + quantize(F(pts[i] - pts[a], duration) * length, 1)
            for i in range(a, b + 1)
        ]
        for i in range(a, b):
            continuous.append([i, mapped[i - a], mapped[i - a + 1] - mapped[i - a]])
            hold.append([i, output + pts[i] - pts[a], pts[i + 1] - pts[i]])
        # Explicit baseline only. The continuous variant emits no such entries.
        pos = output + duration
        while pos < output + length:
            size = min(1600, output + length - pos)
            hold.append([h, pos, size])
            pos += size
        output += length
        cursor = b
    if cursor != len(pts) - 1:
        raise ValueError("Uncovered source tail")
    return {
        "schema": "ContinuousExperiment.v1",
        "units": units,
        "continuous": continuous,
        "hold": hold,
        "output_samples": output,
        "quality_status": "REVIEW",
    }


def mix(source, dubs, timeline):
    if len(source) // 4 != timeline["units"][-1]["source_end_sample"]:
        raise ValueError("Source audio length differs from timeline")
    if len(dubs) != len(timeline["units"]):
        raise ValueError("Dub count differs from timeline")
    result = bytearray()
    silence = bytes(SR * 4)
    for u, dub in zip(timeline["units"], dubs, strict=True):
        if len(dub) != u["dub_samples"] * 4:
            raise ValueError("Dub duration changed")
        result.extend(dub)
        result.extend(silence)
        result.extend(source[u["source_start_sample"] * 4 : u["source_end_sample"] * 4])
        result.extend(silence)
    if len(result) != timeline["output_samples"] * 4:
        raise ValueError("Output PCM length mismatch")
    return bytes(result)


def protect_speech(timeline, intervals):
    if not intervals:
        raise ValueError("Missing source speech intervals; review safe cuts first")
    for a, b in intervals:
        if (
            type(a) is not int
            or type(b) is not int
            or not 0 <= a < b
            or not any(
                u["source_start_sample"] <= a < b <= u["source_end_sample"]
                for u in timeline["units"]
            )
        ):
            raise ValueError(
                "Speech crosses group boundary or source bounds: regroup/review"
            )


def layouts(pages, total, width, height, font_path):
    from PIL import Image, ImageDraw, ImageFont

    result, previous = [], 0
    for page in pages:
        a, b = page["start_frame"], page["end_frame"]
        if type(a) is not int or type(b) is not int or not previous <= a < b <= total:
            raise ValueError("Invalid/overlapping caption pages")
        box = page["english_box"]
        if (
            len(box) != 4
            or any(type(v) is not int for v in box)
            or not 0 <= box[0] < box[2] <= width
            or not 0 <= box[1] < box[3] <= height
            or not page["text"].strip()
            or not page.get("evidence")
        ):
            raise ValueError("Missing measured English box/text/evidence")
        font = ImageFont.truetype(str(font_path), max(12, round(height * 0.036)))
        text = "\n".join(wrap(page["text"], font, width - 48))
        canvas = Image.new("RGBA", (width, height))
        draw = ImageDraw.Draw(canvas)
        ink = draw.multiline_textbbox(
            (0, 0), text, font=font, spacing=5, stroke_width=2
        )
        y = chinese_baseline(box, height, ink[1], ink[3])
        x = max(
            12 - ink[0],
            min((box[0] + box[2] - ink[0] - ink[2]) // 2, width - 12 - ink[2]),
        )
        zh = [x + ink[0], y + ink[1], x + ink[2], y + ink[3]]
        if (
            zh[0] < 0
            or zh[1] < 0
            or zh[2] > width
            or zh[3] > height
            or not (zh[3] <= box[1] or zh[1] >= box[3])
        ):
            raise ValueError("Chinese caption overlaps or leaves source canvas")
        draw.multiline_text(
            (x, y),
            text,
            font=font,
            fill="white",
            stroke_fill="black",
            stroke_width=2,
            spacing=5,
        )
        result.append((dict(page, chinese_box=zh), canvas))
        previous = b
    return result


def render(source, audio, entries, overlays, destination, hold_frames):
    import av
    from PIL import Image

    with (
        av.open(str(source)) as src,
        av.open(str(audio)) as sound,
        av.open(str(destination), "w") as dst,
    ):
        stream = src.streams.video[0]
        video = dst.add_stream("libx264", rate=stream.average_rate)
        video.width, video.height = stream.width, stream.height
        video.pix_fmt = "yuv420p"
        video.time_base = video.codec_context.time_base = F(1, SR)
        video.codec_context.max_b_frames = 0
        video.options = {"crf": "18", "preset": "fast", "threads": "2"}
        durations = {pts: duration for _, pts, duration in entries}

        def mux_video(packet):
            sample = quantize(packet.pts * packet.time_base)
            packet.duration = quantize(F(durations[sample], SR) / packet.time_base, 1)
            dst.mux(packet)

        out_audio = dst.add_stream_from_template(sound.streams.audio[0])
        packets = (p for p in sound.demux(sound.streams.audio[0]) if p.dts is not None)
        pending = next(packets, None)
        frames = iter(src.decode(video=0))
        decoded = -1
        cached_hold = None
        for index, pts, duration in entries:
            if index == decoded + 1:
                original = next(frames).to_image()
                decoded = index
                if index in hold_frames:
                    cached_hold = (index, original)
            elif cached_hold is not None and index == cached_hold[0]:
                original = cached_hold[1]
            else:
                raise ValueError("Unexpected frame reorder")
            picture = original
            for page, overlay in overlays:
                if page["start_frame"] <= index < page["end_frame"]:
                    picture = Image.alpha_composite(
                        original.convert("RGBA"), overlay
                    ).convert("RGB")
                    break
            frame = av.VideoFrame.from_image(picture)
            frame.pts, frame.time_base = pts, F(1, SR)
            for packet in video.encode(frame):
                mux_video(packet)
            while pending is not None and pending.pts * pending.time_base < F(
                pts + duration, SR
            ):
                pending.stream = out_audio
                dst.mux(pending)
                pending = next(packets, None)
        if next(frames, None) is not None:
            raise ValueError("Unconsumed source frames")
        for packet in video.encode():
            mux_video(packet)
        while pending is not None:
            pending.stream = out_audio
            dst.mux(pending)
            pending = next(packets, None)


def main():
    import av

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    base = args.manifest.resolve().parent
    source, audio = (base / manifest[k] for k in ("source", "audio"))
    font = Path(manifest["font"])
    dubs = [pcm(base / g["dub"]) for g in manifest["groups"]]
    source_pcm = pcm(audio)
    with av.open(str(source)) as container:
        if len(container.streams.video) != 1:
            raise ValueError("Expected one source video stream")
        stream = container.streams.video[0]
        width, height = stream.width, stream.height
        if stream.sample_aspect_ratio not in (None, F(1)):
            raise ValueError("Prepare square-pixel display geometry first")
        pts = [
            quantize(frame.pts * frame.time_base) for frame in container.decode(video=0)
        ]
    pts.append(len(source_pcm) // 4)
    timeline = plan(pts, manifest["groups"], [len(d) // 4 for d in dubs])
    protect_speech(timeline, manifest["speech_intervals"])
    overlays = layouts(manifest["pages"], len(pts) - 1, width, height, font)
    for page in manifest["pages"]:
        if not (base / page["evidence"]).is_file():
            raise ValueError("Missing subtitle location evidence file")
    for group in manifest["groups"]:
        if not any(
            p[0]["start_frame"] <= group["hold_frame"] < p[0]["end_frame"]
            for p in overlays
        ):
            raise ValueError(
                "Baseline hold must retain a measured English subtitle page"
            )
    if args.output.exists():
        raise ValueError("Output exists; create a new version")
    # This CLI is intentionally a short, bounded experiment, not a full-film renderer.
    if timeline["output_samples"] > 120 * SR:
        raise ValueError("Experiment limited to 120 output seconds")
    if (
        shutil.disk_usage(args.output.resolve().parent).free
        < 2 * 1024**3 + 400 * 1024**2
    ):
        raise ValueError("Need 2 GiB reserve plus 400 MiB experiment budget")
    args.output.mkdir()
    combined = mix(source_pcm, dubs, timeline)
    wav_path = args.output / "combined.wav"
    with wave.open(str(wav_path), "wb") as wav:
        wav.setparams((2, 2, SR, 0, "NONE", "not compressed"))
        wav.writeframes(combined)
    for mode in ("continuous", "hold"):
        render(
            source,
            wav_path,
            timeline[mode],
            overlays,
            args.output / f"{mode}.mkv",
            {g["hold_frame"] for g in manifest["groups"]},
        )
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-i",
                str(args.output / f"{mode}.mkv"),
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(args.output / f"{mode}.mp4"),
            ],
            check=True,
        )
    timeline["source_frame_samples"] = pts
    timeline["pages"] = [p for p, _ in overlays]
    timeline["inputs"] = {
        str(p): sha512(p)
        for p in [args.manifest, source, audio, font]
        + [base / g["dub"] for g in manifest["groups"]]
        + [base / p["evidence"] for p in manifest["pages"]]
    }
    timeline["outputs"] = {
        p.name: sha512(p) for p in args.output.iterdir() if p.is_file()
    }
    timeline["human_listening"] = timeline["formal_acceptance"] = "REVIEW"
    (args.output / "timeline.json").write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "seconds": len(combined) / 4 / SR,
                "video_speeds": [u["video_speed"] for u in timeline["units"]],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
