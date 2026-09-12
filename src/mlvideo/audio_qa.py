"""Conservative audio checks: report uncertain boundaries, never trim speech."""

import array
import json
import math
import wave

from .media import command
from .util import atomic_json, sha512


def inspect_audio(path, work, leading_review_seconds=1.0):
    if not 0 < leading_review_seconds < 30:
        raise ValueError("Invalid leading audio review threshold")
    info = json.loads(
        command(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_streams",
                "-of",
                "json",
                str(path),
            ],
            work,
        )
    )["streams"]
    if len(info) != 1:
        raise ValueError("Expected one audio stream")
    sr, channels = int(info[0]["sample_rate"]), int(info[0]["channels"])
    decoded = command(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-f",
            "f32le",
            "-c:a",
            "pcm_f32le",
            "-",
        ],
        work,
    )
    samples = array.array("f", decoded)
    if not samples or len(samples) % channels:
        raise ValueError("Empty or incomplete audio")
    if not all(math.isfinite(x) for x in samples):
        raise ValueError("Nonfinite audio samples")
    frames = len(samples) // channels
    peak = max(abs(x) for x in samples)
    window = max(1, round(sr * 0.02)) * channels
    rms = [
        math.sqrt(sum(x * x for x in samples[i : i + window]) / window)
        for i in range(0, len(samples) - window + 1, window)
    ]
    active = next((i for i in range(len(rms) - 2) if min(rms[i : i + 3]) > 0.01), None)
    onset = active * (window / channels / sr) if active is not None else None
    checks = [
        {"id": "decode", "status": "PASS", "required": True},
        {"id": "clipping", "status": "FAIL" if peak >= 1 else "PASS", "required": True},
        {
            "id": "non_silent",
            "status": "FAIL" if peak <= 0.00001 else "PASS",
            "required": True,
        },
        {
            "id": "leading_audio",
            "status": "REVIEW"
            if onset is None or onset > leading_review_seconds
            else "PASS",
            "required": True,
        },
        {"id": "content_and_tail", "status": "SKIPPED", "required": True},
        {"id": "human_listening", "status": "SKIPPED", "required": True},
    ]
    report = {
        "overall": "FAIL" if any(c["status"] == "FAIL" for c in checks) else "REVIEW",
        "method": "energy-v1: 20ms RMS, 3 consecutive windows above -40dBFS; not word alignment",
        "leading_review_seconds": leading_review_seconds,
        "source_sha512": sha512(path),
        "sample_rate": sr,
        "channels": channels,
        "frames": frames,
        "peak": peak,
        "energy_onset_seconds": onset,
        "checks": checks,
        "trimmed_samples": 0,
    }
    return report


def normalize_and_check(path, clip, work, leading_review_seconds=1.0):
    report = inspect_audio(path, work, leading_review_seconds)
    if (
        report["source_sha512"] != clip["audio_sha512"]
        or report["frames"] != clip["frames"]
        or report["sample_rate"] != clip["sample_rate"]
        or report["channels"] != clip["channels"]
    ):
        raise ValueError("RawDubClip does not match input audio")
    output = work / "normalized.wav"
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
            "0:a:0",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(output),
        ],
        work,
    )
    with wave.open(str(output), "rb") as f:
        count = f.getnframes()
        if f.getframerate() != 48000 or f.getnchannels() != 2 or f.getsampwidth() != 2:
            raise ValueError("Unexpected normalized audio format")
    expected = (report["frames"] * 48000 + report["sample_rate"] // 2) // report[
        "sample_rate"
    ]
    if count != expected:
        raise ValueError("Resampling changed audio duration")
    atomic_json(work / "qa.json", report)
    return report, count, sha512(output)


def safe_reference_pcm(raw, destination, work):
    """Preserve decoded FLOAT audio, attenuating before PCM16 conversion if needed."""
    import re

    index = len(list(work.glob("command-*.json")))
    command(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "info",
            "-i",
            str(raw),
            "-af",
            "astats=metadata=0:reset=0",
            "-f",
            "null",
            "-",
        ],
        work,
    )
    log = (work / f"command-{index:04}.stderr").read_text()
    peaks = re.findall(r"Peak level dB: ([-+\d.]+)", log)
    if not peaks:
        raise ValueError("Cannot determine reference source peak")
    peak_db = max(map(float, peaks))
    peak = 10 ** (peak_db / 20)
    gain = min(1.0, 0.95 / peak)
    command(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(raw),
            "-af",
            f"volume={gain:.12g}",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(destination),
        ],
        work,
    )
    return {
        "raw_peak_dbfs": peak_db,
        "applied_gain": gain,
        "peak_target": 0.95,
        "processing": "Uniform attenuation before integer conversion; no trimming, separation or repair of source recording",
    }
