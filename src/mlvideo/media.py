import array
import bisect
import json
import math
from math import ceil
import subprocess
import shutil
import wave
from fractions import Fraction as F
from .timeline import quantize, verify
from .util import atomic_json


def command(argv, work):
    index = len(list(work.glob("command-*.json")))
    prefix = work / f"command-{index:04}"
    atomic_json(prefix.with_suffix(".json"), {"argv": argv})
    with (
        prefix.with_suffix(".stdout").open("wb") as out,
        prefix.with_suffix(".stderr").open("wb") as err,
    ):
        subprocess.run(argv, stdout=out, stderr=err, check=True)
    return prefix.with_suffix(".stdout").read_bytes()


def probe(path, work, decode=True):
    r = json.loads(
        command(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ],
            work,
        )
    )
    if not any(s["codec_type"] == "video" for s in r["streams"]):
        raise ValueError("No video stream")
    if decode:
        command(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-xerror",
                "-i",
                str(path),
                "-map",
                "0:v",
                "-map",
                "0:a?",
                "-f",
                "null",
                "-",
            ],
            work,
        )
    return r


def preserve_source(source, info, work):
    """Keep the source bytes/PTS; extract PCM without adding or dropping video frames."""
    videos = [s for s in info["streams"] if s["codec_type"] == "video"]
    audios = [s for s in info["streams"] if s["codec_type"] == "audio"]
    if len(videos) != 1 or len(audios) != 1:
        raise ValueError("Ambiguous video/original audio tracks: REVIEW")
    v, a = videos[0], audios[0]
    audio_frames = json.loads(command([
        "ffprobe", "-v", "error", "-select_streams", "a:0", "-show_frames",
        "-show_entries", "frame=best_effort_timestamp_time,nb_samples", "-of", "json", str(source),
    ], work))["frames"]
    tolerance = max(F(a["time_base"]), F(1, int(a["sample_rate"])))
    for previous, current in zip(audio_frames, audio_frames[1:]):
        expected = F(previous["best_effort_timestamp_time"]) + F(previous["nb_samples"], int(a["sample_rate"]))
        if abs(F(current["best_effort_timestamp_time"]) - expected) > tolerance:
            raise ValueError("Unexplained audio timestamp gap/overlap; cannot extract safely")
    frames = json.loads(command([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
        "-show_entries", "frame=best_effort_timestamp_time,duration_time", "-of", "json", str(source),
    ], work))["frames"]
    pts = [F(f["best_effort_timestamp_time"]) for f in frames]
    if not pts or any(y <= x for x, y in zip(pts, pts[1:])):
        raise ValueError("Unexplained video timestamps")
    fps = F(v["r_frame_rate"])
    if fps <= 0:
        raise ValueError("Missing source frame rate")
    audio_start = F(audio_frames[0]["best_effort_timestamp_time"]) if audio_frames else F(a.get("start_time", "0"))
    origin = min(pts[0], audio_start)
    offset = audio_start - origin
    raw = work / "audio.raw.wav"
    command([
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(source),
        "-map", "0:a:0", "-vn", "-af", "aresample=48000", "-ac", "2",
        "-c:a", "pcm_s16le", str(raw),
    ], work)
    with wave.open(str(raw), "rb") as src:
        payload = src.getnframes()
    duration = F(frames[-1].get("duration_time", "0")) or 1 / fps
    end = max(pts[-1] + duration - origin, offset + F(payload, 48000))
    samples = quantize(end)
    shift = quantize(offset)
    with wave.open(str(raw), "rb") as src, wave.open(str(work / "canonical.wav"), "wb") as dst:
        dst.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
        write_zeros(dst, shift)
        while block := src.readframes(48000):
            dst.writeframesraw(block)
        write_zeros(dst, samples - shift - payload)
    raw.unlink()
    shutil.copyfile(source, work / "original.bin")
    atomic_json(work / "canonical.json", {
        "fps": str(fps), "sample_rate": 48000, "channels": 2,
        "source_frames": len(pts), "source_samples": samples,
        "source_pts": [str(p) for p in pts], "frame_mapping": list(range(len(pts))),
        "audio_offset_seconds": str(audio_start - pts[0]),
        "rotation": next((s["rotation"] for s in v.get("side_data_list", []) if "rotation" in s), 0),
        "video_lead_frames": 0, "video_tail_frames": 0,
        "audio_start_sample": shift, "audio_payload_samples": payload,
        "warnings": [], "timing_mode": "original",
        "frame_samples": [quantize(p - origin) for p in pts] + [samples],
        "origin_seconds": str(origin),
    })


def normalize(source, info, work, fps_override=None):
    videos = [s for s in info["streams"] if s["codec_type"] == "video"]
    audios = [s for s in info["streams"] if s["codec_type"] == "audio"]
    if len(videos) != 1 or len(audios) != 1:
        raise ValueError("Ambiguous video/original audio tracks: REVIEW")
    v, a = videos[0], audios[0]
    if (
        v.get("color_transfer") in ("smpte2084", "arib-std-b67")
        or v.get("color_primaries") == "bt2020"
    ):
        raise ValueError("HDR conversion is not supported")
    audio_frames = json.loads(
        command(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_frames",
                "-show_entries",
                "frame=best_effort_timestamp_time,nb_samples",
                "-of",
                "json",
                str(source),
            ],
            work,
        )
    )["frames"]
    tolerance = max(F(a["time_base"]), F(1, int(a["sample_rate"])))
    for previous, current in zip(audio_frames, audio_frames[1:]):
        expected = F(previous["best_effort_timestamp_time"]) + F(
            previous["nb_samples"], int(a["sample_rate"])
        )
        if abs(F(current["best_effort_timestamp_time"]) - expected) > tolerance:
            raise ValueError(
                "Unexplained audio timestamp gap/overlap; cannot normalize safely"
            )
    frames = json.loads(
        command(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_frames",
                "-show_entries",
                "frame=best_effort_timestamp_time",
                "-of",
                "json",
                str(source),
            ],
            work,
        )
    )["frames"]
    pts = [F(f["best_effort_timestamp_time"]) for f in frames]
    if not pts or any(y <= x for x, y in zip(pts, pts[1:])):
        raise ValueError("Unexplained video timestamps")
    rate = F(v["r_frame_rate"])
    is_cfr = all(abs((b - a) - 1 / rate) <= F(1, 1000) for a, b in zip(pts, pts[1:]))
    fps = F(fps_override) if fps_override else rate if is_cfr else F(25)
    if fps <= 0 or fps > 240:
        raise ValueError("Unsupported target frame rate")
    video = work / "canonical.mkv"
    audio = work / "canonical.wav"
    command(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-an",
            "-vf",
            f"setpts=PTS-STARTPTS,fps=fps={fps}:round=near",
            "-c:v",
            "ffv1",
            "-level",
            "3",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        work,
    )
    actual = json.loads(
        command(
            [
                "ffprobe",
                "-v",
                "error",
                "-count_frames",
                "-show_streams",
                "-of",
                "json",
                str(video),
            ],
            work,
        )
    )["streams"][0]
    count = int(actual["nb_read_frames"])
    samples = quantize(F(count) / fps)
    offset = F(a.get("start_time", "0")) - pts[0]
    raw = work / "audio.raw.wav"
    command(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-af",
            "aresample=48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(raw),
        ],
        work,
    )
    with wave.open(str(raw), "rb") as src:
        payload_samples = src.getnframes()
    # Container duration can exclude codec padding (e.g. AAC); PCM has no such trim.
    if "duration" in a and a["codec_name"] != "pcm_s16le":
        payload_samples = min(payload_samples, quantize(F(a["duration"])))
    lead_frames = max(0, ceil(-offset * fps))
    shift = quantize(offset + F(lead_frames) / fps)
    base_count = count
    count = max(count + lead_frames, ceil(F(shift + payload_samples, 48000) * fps))
    tail_frames = count - base_count - lead_frames
    samples = quantize(F(count) / fps)
    if lead_frames or tail_frames:
        # Padding writes a second lossless video before replacing the first.
        # Refuse this peak allocation before it can exhaust the host/MySQL disk.
        from .process import DISK_RESERVE_BYTES
        required = int(video.stat().st_size * 1.2) + DISK_RESERVE_BYTES
        if shutil.disk_usage(work).free < required:
            raise RuntimeError(f"视频补帧需要至少 {required / 1024**3:.2f} GiB 可用空间（包含临时副本与系统保留空间）；请释放空间后手动重试")
        padded = work / "padded.mkv"
        command(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-i",
                str(video),
                "-vf",
                f"tpad=start={lead_frames}:start_mode=clone:stop={tail_frames}:stop_mode=clone",
                "-c:v",
                "ffv1",
                "-pix_fmt",
                "yuv420p",
                str(padded),
            ],
            work,
        )
        padded.replace(video)
    with wave.open(str(raw), "rb") as src, wave.open(str(audio), "wb") as dst:
        dst.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
        write_zeros(dst, shift)
        remaining = payload_samples
        while remaining:
            n = min(48000, remaining)
            dst.writeframesraw(src.readframes(n))
            remaining -= n
        write_zeros(dst, samples - shift - payload_samples)
    relative = [p - pts[0] for p in pts]
    # FFmpeg fps round=near switches source frames at rounded source PTS.
    starts = [quantize(p * fps, 1) for p in relative]
    mapping = [max(0, bisect.bisect_right(starts, i) - 1) for i in range(base_count)]
    mapping = [mapping[0]] * lead_frames + mapping + [mapping[-1]] * tail_frames
    rotation = next(
        (s.get("rotation", 0) for s in v.get("side_data_list", []) if "rotation" in s),
        0,
    )
    result = {
        "fps": str(fps),
        "sample_rate": 48000,
        "channels": 2,
        "source_frames": count,
        "source_samples": samples,
        "source_pts": [str(p) for p in pts],
        "frame_mapping": mapping,
        "audio_offset_seconds": str(offset),
        "rotation": rotation,
        "video_lead_frames": lead_frames,
        "video_tail_frames": tail_frames,
        "audio_start_sample": shift,
        "audio_payload_samples": payload_samples,
        "warnings": [] if is_cfr else ["VFR converted to CFR"],
    }
    atomic_json(work / "canonical.json", result)


def join_voice_parts(paths, destination, gap_samples=7680):
    """Join normalized role/narrator PCM into one sentence, retaining part offsets."""
    if not paths or type(gap_samples) is not int or gap_samples < 0:
        raise ValueError("Voice parts and a nonnegative sample gap are required")
    for path in paths:
        with wave.open(str(path), "rb") as source:
            if (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (48000, 2, 2):
                raise ValueError("Voice parts must be normalized 48 kHz stereo PCM16")
    offsets, cursor = [], 0
    with wave.open(str(destination), "wb") as target:
        target.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
        for index, path in enumerate(paths):
            if index:
                write_zeros(target, gap_samples)
                cursor += gap_samples
            with wave.open(str(path), "rb") as source:
                count = source.getnframes()
                offsets.append({"path": str(path), "start_sample": cursor, "end_sample": cursor + count})
                while block := source.readframes(48000):
                    target.writeframesraw(block)
                cursor += count
    return offsets


def write_zeros(dst, n):
    while n:
        take = min(n, 48000)
        dst.writeframesraw(b"\0" * (take * 4))
        n -= take


def video_geometry(stream):
    width, height = stream["width"], stream["height"]
    rotation = next((s["rotation"] for s in stream.get("side_data_list", []) if "rotation" in s), 0)
    return (height, width) if abs(rotation) % 180 == 90 else (width, height)


def render_original_frames(decode_argv, audio, t, work, width, height, overlay, output_height):
    """Timestamp each original/intentional hold frame; never resample the source FPS."""
    import av
    from PIL import Image

    pts = t["frame_samples"]
    frame_bytes = width * height * 3
    with (work / "decode.stderr").open("wb") as err:
        decoder = subprocess.Popen(decode_argv, stdout=subprocess.PIPE, stderr=err)
        try:
            with av.open(str(audio)) as sound, av.open(str(work / "master.mkv"), "w") as target:
                video = target.add_stream("ffv1", rate=F(t["fps"]))
                video.width, video.height = width, output_height or height
                video.pix_fmt = "bgr0"
                video.time_base = video.codec_context.time_base = F(1, 48000)
                audio_stream = target.add_stream_from_template(sound.streams.audio[0])
                packets = (p for p in sound.demux(sound.streams.audio[0]) if p.dts is not None)
                pending = next(packets, None)
                last = None
                index = 0
                for piece in t["pieces"]:
                    for _ in range(piece["output_end_frame"] - piece["output_start_frame"]):
                        if piece["kind"] == "source":
                            last = decoder.stdout.read(frame_bytes)
                            if len(last) != frame_bytes:
                                raise ValueError("Source frame count mismatch")
                        # Overlay's clock argument is expressed in samples for native PTS.
                        pixels = overlay(last, pts[index], F(48000)) if overlay else last
                        frame = av.VideoFrame.from_image(Image.frombytes("RGB", (width, output_height or height), pixels))
                        frame.pts, frame.time_base = pts[index], F(1, 48000)
                        for packet in video.encode(frame):
                            packet.duration = pts[index + 1] - pts[index]
                            target.mux(packet)
                        while pending is not None and pending.pts * pending.time_base < F(pts[index + 1], 48000):
                            pending.stream = audio_stream
                            target.mux(pending)
                            pending = next(packets, None)
                        index += 1
                for packet in video.encode():
                    target.mux(packet)
                while pending is not None:
                    pending.stream = audio_stream
                    target.mux(pending)
                    pending = next(packets, None)
                if decoder.stdout.read(1) or decoder.wait():
                    raise ValueError("Unexpected source frames or decoding failure")
        finally:
            if decoder.poll() is None:
                decoder.kill()
            decoder.wait()
            decoder.stdout.close()


def render(video, audio, t, work, dub_audio=None, overlay=None, output_height=None):
    verify(t)
    fps = F(t["fps"])
    info = next(s for s in probe(video, work, False)["streams"] if s["codec_type"] == "video")
    width, height = video_geometry(info)
    frame_bytes = width * height * 3
    retimed = work / "retimed.wav"
    with wave.open(str(audio), "rb") as src, wave.open(str(retimed), "wb") as dst:
        if (
            src.getnchannels(),
            src.getsampwidth(),
            src.getframerate(),
            src.getnframes(),
        ) != (2, 2, 48000, t["source_samples"]):
            raise ValueError("Canonical PCM mismatch")
        dst.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
        source_pos = output_pos = 0
        for p in t["pieces"]:
            if p["kind"] == "source":
                end = (t["source_frame_samples"][p["source_end_frame"]] if "source_frame_samples" in t
                       else quantize(F(p["source_end_frame"]) / fps))
                n = end - source_pos
                src.setpos(source_pos)
                while n:
                    take = min(n, 48000)
                    dst.writeframesraw(src.readframes(take))
                    n -= take
                output_pos += end - source_pos
                source_pos = end
            else:
                target = p["output_end_sample"]
                write_zeros(dst, target - output_pos)
                output_pos = target
        # Fractional segment rounding may differ by one at final boundary.
        if output_pos < t["output_samples"]:
            write_zeros(dst, t["output_samples"] - output_pos)
        if output_pos > t["output_samples"]:
            raise ValueError("Audio cumulative quantization overflow")
    dub_samples = {}
    if dub_audio is not None:
        for d in t["dubs"]:
            with wave.open(str(dub_audio[d["audio_sha512"]]), "rb") as wav:
                if (
                    wav.getnchannels(),
                    wav.getsampwidth(),
                    wav.getframerate(),
                    wav.getnframes(),
                ) != (2, 2, 48000, d["samples"]):
                    raise ValueError("Dub PCM format/sample count mismatch")
                dub_samples[d["id"]] = array.array(
                    "h", wav.readframes(wav.getnframes())
                )
    master_audio = work / "master.wav"
    with (
        wave.open(str(retimed), "rb") as src,
        wave.open(str(master_audio), "wb") as dst,
    ):
        dst.setparams(src.getparams())
        position = 0
        while block := src.readframes(48000):
            values = array.array("h", block)
            for d in t["dubs"]:
                lo = max(position, d["start_sample"])
                hi = min(position + len(values) // 2, d["start_sample"] + d["samples"])
                for s in range(lo, hi):
                    tone = (
                        0
                        if dub_audio is not None
                        else round(
                            1000
                            * math.sin(
                                2
                                * math.pi
                                * d["tone_hz"]
                                * (s - d["start_sample"])
                                / 48000
                            )
                        )
                    )
                    for ch in (0, 1):
                        idx = (s - position) * 2 + ch
                        added = (
                            dub_samples[d["id"]][(s - d["start_sample"]) * 2 + ch]
                            if dub_audio is not None
                            else tone
                        )
                        value = values[idx] + added
                        if not -32768 <= value <= 32767:
                            raise ValueError("Audio clipping")
                        values[idx] = value
            dst.writeframesraw(values.tobytes())
            position += len(values) // 2
    decode_argv = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(video),
        "-map",
        "0:v:0",
        "-fps_mode",
        "passthrough",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-",
    ]
    encode_argv = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{output_height or height}",
        "-r",
        str(fps),
        "-i",
        "-",
        "-i",
        str(master_audio),
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-c:v",
        "ffv1",
        "-pix_fmt",
        "bgr0",
        "-c:a",
        "pcm_s16le",
        str(work / "master.mkv"),
    ]
    atomic_json(
        work / "streaming-commands.json", {"decode": decode_argv, "encode": (
            {"backend": "PyAV", "codec": "ffv1", "pixel_format": "bgr0",
             "time_base": "1/48000", "timestamps": t["frame_samples"], "audio": str(master_audio)}
            if "frame_samples" in t else encode_argv)}
    )
    if "frame_samples" in t:
        render_original_frames(decode_argv, master_audio, t, work, width, height, overlay, output_height)
    else:
        with (
            (work / "decode.stderr").open("wb") as decerr,
            (work / "encode.stderr").open("wb") as encerr,
        ):
            decoder = subprocess.Popen(decode_argv, stdout=subprocess.PIPE, stderr=decerr)
            encoder = subprocess.Popen(encode_argv, stdin=subprocess.PIPE, stderr=encerr)
            try:
                last = None
                frame_index = 0
                for p in t["pieces"]:
                    for _ in range(p["output_end_frame"] - p["output_start_frame"]):
                        if p["kind"] == "source":
                            last = decoder.stdout.read(frame_bytes)
                            if len(last) != frame_bytes:
                                raise ValueError("Source frame count mismatch")
                        encoder.stdin.write(
                            overlay(last, frame_index, fps) if overlay else last
                        )
                        frame_index += 1
                if decoder.stdout.read(1):
                    raise ValueError("Unexpected extra source frames")
                encoder.stdin.close()
                if decoder.wait() or encoder.wait():
                    raise ValueError("Streaming FFmpeg failed")
            finally:
                for child in (decoder, encoder):
                    if child.poll() is None:
                        child.kill()
                    child.wait()
                decoder.stdout.close()
                if not encoder.stdin.closed:
                    encoder.stdin.close()
    command(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            str(work / "master.mkv"),
            "-fps_mode",
            "passthrough",
            *(["-enc_time_base:v", "1:48000"] if "frame_samples" in t else []),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(work / "preview.mp4"),
        ],
        work,
    )
    probe(work / "preview.mp4", work)
    atomic_json(
        work / "qa.json",
        {
            "overall": "REVIEW",
            "checks": [
                {"id": "full_decode", "status": "PASS", "required": True},
                *[
                    {
                        "id": x,
                        "status": "SKIPPED",
                        "required": True,
                        "reason": "Phase 1 tone demo; not implemented",
                    }
                    for x in (
                        "translation",
                        "voice_identity",
                        "subtitle_layout",
                        "human_review",
                    )
                ],
            ],
        },
    )


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", required=True)
    parser.add_argument("--work", required=True)
    arguments = parser.parse_args()
    work = Path(arguments.work)
    atomic_json(work / "probe.json", probe(Path(arguments.probe), work))
