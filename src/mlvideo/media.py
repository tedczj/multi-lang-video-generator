import array
import bisect
import json
import math
from math import ceil
import subprocess
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


def write_zeros(dst, n):
    while n:
        take = min(n, 48000)
        dst.writeframesraw(b"\0" * (take * 4))
        n -= take


def render(video, audio, t, work):
    verify(t)
    fps = F(t["fps"])
    info = probe(video, work, False)["streams"][0]
    width, height = info["width"], info["height"]
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
                end = quantize(F(p["source_end_frame"]) / fps)
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
                    tone = round(
                        1000
                        * math.sin(
                            2 * math.pi * d["tone_hz"] * (s - d["start_sample"]) / 48000
                        )
                    )
                    for ch in (0, 1):
                        idx = (s - position) * 2 + ch
                        value = values[idx] + tone
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
        f"{width}x{height}",
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
        work / "streaming-commands.json", {"decode": decode_argv, "encode": encode_argv}
    )
    with (
        (work / "decode.stderr").open("wb") as decerr,
        (work / "encode.stderr").open("wb") as encerr,
    ):
        decoder = subprocess.Popen(decode_argv, stdout=subprocess.PIPE, stderr=decerr)
        encoder = subprocess.Popen(encode_argv, stdin=subprocess.PIPE, stderr=encerr)
        try:
            last = None
            for p in t["pieces"]:
                for _ in range(p["output_end_frame"] - p["output_start_frame"]):
                    if p["kind"] == "source":
                        last = decoder.stdout.read(frame_bytes)
                        if len(last) != frame_bytes:
                            raise ValueError("Source frame count mismatch")
                    encoder.stdin.write(last)
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
