"""Read-only comparison of a screen recording with its source video."""

import argparse
import json
import subprocess
from pathlib import Path

from .util import sha512


def sample_features(path, crop, rate, seconds):
    import numpy as np

    filters = f"{crop},fps={rate},scale=96:54,crop=76:34:10:8,format=gray"
    raw = subprocess.check_output(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-t",
            str(seconds),
            "-vf",
            filters,
            "-f",
            "rawvideo",
            "-",
        ]
    )
    pixels = np.frombuffer(raw, np.uint8).reshape(-1, 76 * 34).astype(np.float32)
    pixels -= pixels.mean(axis=1, keepdims=True)
    pixels /= np.maximum(np.linalg.norm(pixels, axis=1, keepdims=True), 1e-6)
    return pixels


def compare(source, reference, source_rate=10, reference_rate=5, start=18):
    import numpy as np

    scores = reference @ source.T
    rows = []
    for i, score in enumerate(scores):
        best = int(score.argmax())
        distant = score.copy()
        distant[max(0, best - source_rate) : best + source_rate + 1] = -1
        rows.append(
            {
                "recording_seconds": i / reference_rate,
                "source_seconds": best / source_rate,
                "correlation": float(score[best]),
                "distant_margin": float(score[best] - distant.max()),
            }
        )
    # Exclude flat/repeated scenes from the fit, but retain them in the evidence.
    candidates = [
        r
        for r in rows
        if r["recording_seconds"] >= start
        and r["correlation"] >= 0.97
        and r["distant_margin"] > 0.0001
    ]
    if len(candidates) < 10:
        raise ValueError("Too few unambiguous matching frames")
    x = np.array([r["recording_seconds"] for r in candidates])
    y = np.array([r["source_seconds"] for r in candidates])
    rng = np.random.default_rng(20260917)
    best = np.zeros(len(x), dtype=bool)
    for _ in range(1000):
        a, b = rng.choice(len(x), 2, replace=False)
        if abs(x[a] - x[b]) < 10:
            continue
        slope = (y[b] - y[a]) / (x[b] - x[a])
        intercept = y[a] - slope * x[a]
        mask = np.abs(y - (slope * x + intercept)) < 0.35
        if mask.sum() > best.sum():
            best = mask
    if best.sum() < 10:
        raise ValueError(
            "No supported continuous mapping; inspect cuts/replays manually"
        )
    slope, intercept = np.polyfit(x[best], y[best], 1)
    residual = np.abs(y[best] - (slope * x[best] + intercept))
    return rows, {
        "source_seconds_per_recording_second": float(slope),
        "source_intercept_seconds": float(intercept),
        "fitted_recording_range": [float(x[best].min()), float(x[best].max())],
        "candidate_count": len(candidates),
        "inlier_count": int(best.sum()),
        "sample_count": len(rows),
        "residual_p95_seconds": float(np.quantile(residual, 0.95)),
        "limits": "Global dominant mapping only; ambiguous stills and unmatched points do not prove freezes or replays. Audio speed is not measured.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--recording", type=Path, required=True)
    parser.add_argument(
        "--recording-crop", required=True, help="ffmpeg w:h:x:y, analysis only"
    )
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--fit-start", type=float, default=18)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        args.seconds <= 0
        or not 0 <= args.fit_start < args.seconds
        or args.output.exists()
    ):
        parser.error("Invalid bounds or output already exists")
    source = sample_features(args.source, "null", 10, args.seconds)
    reference = sample_features(
        args.recording, "crop=" + args.recording_crop, 5, args.seconds
    )
    rows, result = compare(source, reference, start=args.fit_start)
    args.output.mkdir(parents=True)
    for name, value in [
        ("matches.json", rows),
        (
            "report.json",
            result
            | {
                "source_sha512": sha512(args.source),
                "recording_sha512": sha512(args.recording),
                "recording_crop": args.recording_crop,
                "sampling": {
                    "source_fps": 10,
                    "recording_fps": 5,
                    "seconds": args.seconds,
                },
            },
        ),
    ]:
        (args.output / name).write_text(
            json.dumps(value, indent=2, ensure_ascii=False) + "\n"
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
