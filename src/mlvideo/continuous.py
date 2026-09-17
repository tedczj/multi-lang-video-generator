"""Bounded, standalone English-first continuous-video A/B experiment.

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
        end = group.get("speech_end_sample", pts[b])
        if type(end) is not int or not pts[a] < end <= pts[b]:
            raise ValueError("Speech end must lie inside its source group")
        length = n + duration
        speed = F(duration, length)
        unit = dict(
            group,
            source_start_sample=pts[a],
            source_end_sample=pts[b],
            output_start_sample=output,
            output_end_sample=output + length,
            dub_samples=n,
            english_start_sample=output,
            speech_end_sample=end,
            chinese_start_sample=output + end - pts[a],
            chinese_end_sample=output + end - pts[a] + n,
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
        "schema": "ContinuousExperiment.v3",
        "audio_order": "en-zh",
        "units": units,
        "continuous": continuous,
        "hold": hold,
        "output_samples": output,
        "quality_status": "REVIEW",
        "motion_review_units": [
            u.get("id", str(i))
            for i, u in enumerate(units)
            if F(u["video_speed"]) < F(2, 5)
        ],
    }


def mix(source, dubs, timeline):
    if (
        timeline.get("schema") != "ContinuousExperiment.v3"
        or timeline.get("audio_order") != "en-zh"
    ):
        raise ValueError("Only English-first v3 timelines may generate new audio")
    if len(source) // 4 != timeline["units"][-1]["source_end_sample"]:
        raise ValueError("Source audio length differs from timeline")
    if len(dubs) != len(timeline["units"]):
        raise ValueError("Dub count differs from timeline")
    result = bytearray()
    for u, dub in zip(timeline["units"], dubs, strict=True):
        if len(dub) != u["dub_samples"] * 4:
            raise ValueError("Dub duration changed")
        cut = u["speech_end_sample"] * 4
        result.extend(source[u["source_start_sample"] * 4 : cut])
        result.extend(dub)
        result.extend(source[cut : u["source_end_sample"] * 4])
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

    if pages:
        from fontTools.ttLib import TTFont

        table = TTFont(str(font_path), fontNumber=0)
        cmap = table.getBestCmap()
        missing = {
            c
            for p in pages
            for c in p["text"]
            if not c.isspace() and ord(c) not in cmap
        }
        table.close()
        if missing:
            raise ValueError("Subtitle font lacks required glyphs")
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
        av.open(
            str(destination),
            "w",
            options={"movflags": "+faststart"} if destination.suffix == ".mp4" else {},
        ) as dst,
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


def produce(manifest_path, work, output):
    import uuid

    import av

    manifest_path, work, output = (
        Path(p).resolve() for p in (manifest_path, work, output)
    )
    if output.exists():
        raise ValueError("Output exists; create a new version")
    output.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(output.parent).free < 2 * 1024**3 + 400 * 1024**2:
        raise ValueError("Need 2 GiB reserve plus 400 MiB render budget")
    work.mkdir(parents=True, exist_ok=False)
    manifest = json.loads(manifest_path.read_text())
    base = manifest_path.parent
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
        pts = [quantize(f.pts * f.time_base) for f in container.decode(video=0)]
    pts.append(len(source_pcm) // 4)
    if "canonical" in manifest:
        pts = json.loads((base / manifest["canonical"]).read_text())["frame_samples"]
    groups = []
    for group in manifest["groups"]:
        ends = [
            b
            for a, b in manifest["speech_intervals"]
            if pts[group["start_frame"]] <= a < b <= pts[group["end_frame"]]
        ]
        groups.append(
            group
            | {
                "speech_end_sample": group.get(
                    "speech_end_sample", max(ends) if ends else pts[group["end_frame"]]
                )
            }
        )
    timeline = plan(pts, groups, [len(d) // 4 for d in dubs])
    protect_speech(timeline, manifest["speech_intervals"])
    overlays = layouts(manifest["pages"], len(pts) - 1, width, height, font)
    for page in manifest["pages"]:
        if not (base / page["evidence"]).is_file():
            raise ValueError("Missing subtitle location evidence file")
    combined = mix(source_pcm, dubs, timeline)
    wav_path = work / "combined.wav"
    with wave.open(str(wav_path), "wb") as wav:
        wav.setparams((2, 2, SR, 0, "NONE", "not compressed"))
        wav.writeframes(combined)
    aac = work / "audio.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(wav_path),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(aac),
        ],
        check=True,
    )
    partial = output.with_name(
        "." + output.stem + "-" + uuid.uuid4().hex + ".partial.mp4"
    )
    render(source, aac, timeline["continuous"], overlays, partial, set())
    # No second delivery video or MKV. Original source remains in the workspace.
    timeline.pop("hold")
    timeline.update(
        manifest=str(manifest_path),
        output=str(output),
        dimensions=[width, height],
        source_frame_samples=pts,
        pages=[p for p, _ in overlays],
        human_listening="REVIEW",
        formal_acceptance="REVIEW",
    )
    timeline["inputs"] = {
        str(p): sha512(p)
        for p in [manifest_path, source, audio, font]
        + [base / g["dub"] for g in groups]
        + [base / p["evidence"] for p in manifest["pages"]]
    }
    if "canonical" in manifest:
        timeline["inputs"][str(base / manifest["canonical"])] = sha512(
            base / manifest["canonical"]
        )
    timeline["outputs"] = {"combined.wav": sha512(wav_path), "audio.m4a": sha512(aac)}
    timeline["output_sha512"] = sha512(partial)
    (work / "timeline.json").write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2) + "\n"
    )
    from .continuous_verify import verify_delivery

    report = verify_delivery(work, movie=partial)
    (work / "checks.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    if output.exists():
        raise ValueError("Output appeared during render; refusing replacement")
    partial.rename(output)
    return {
        "output": str(output),
        "work": str(work),
        "seconds": len(combined) / 4 / SR,
        "automated_checks": report["automated_checks"],
        "human_listening": "REVIEW",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, required=True, help="New delivery directory"
    )
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output directory exists; create a new version")
    print(
        json.dumps(
            produce(args.manifest, args.output / "work", args.output / "output.mp4"),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
