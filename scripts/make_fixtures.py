"""Generate tiny original fixtures; oracle uses explicitly specified frame insertions."""

import array
import hashlib
import json
import subprocess
import wave
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests/fixtures/generated"


def run(*args):
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", *map(str, args)], check=True
    )


def make():
    OUT.mkdir(parents=True, exist_ok=True)
    cases = []
    for name, fps, total in [("cfr", "25", 125), ("ntsc", "30000/1001", 150)]:
        rate = Fraction(fps)
        samples = int(Fraction(total * 48000, 1) / rate + Fraction(1, 2))
        rgb = OUT / (name + ".rgb")
        wav = OUT / (name + ".wav")
        video = OUT / (name + ".mkv")
        with rgb.open("wb") as f:
            for i in range(total):
                frame = bytearray()
                for y in range(48):
                    for x in range(64):
                        # Frame number rendered as eight binary bars plus index-coded pixels.
                        frame.extend(
                            (
                                i % 256,
                                255 if (i >> (x // 8)) & 1 and y < 16 else 0,
                                (x + y + i) % 256,
                            )
                        )
                f.write(frame)
        with wave.open(str(wav), "wb") as w:
            w.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
            w.writeframes(
                array.array(
                    "h",
                    (v for i in range(samples) for v in (i % 997 - 498, 498 - i % 997)),
                ).tobytes()
            )
        run(
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            "64x48",
            "-r",
            fps,
            "-i",
            rgb,
            "-i",
            wav,
            "-c:v",
            "ffv1",
            "-pix_fmt",
            "bgr0",
            "-c:a",
            "pcm_s16le",
            video,
        )
        expected = OUT / (name + ".expected.json")
        expected.write_text(
            json.dumps(
                {
                    "frames": total,
                    "samples": samples,
                    "width": 64,
                    "height": 48,
                    "fps": fps,
                    "pcm_formula": "left=i%997-498; right=-left",
                    "speech_end_sample": 48000,
                    "dub_samples": 24000,
                    "last_safe_cut": total,
                    "hold_frames": 0,
                }
            )
            + "\n"
        )
        cases.append(
            {
                "case_id": name,
                "path": str(video.relative_to(ROOT)),
                "sha512": hashlib.sha512(video.read_bytes()).hexdigest(),
                "authorization": "Original programmatically generated fixture; project testing authorized",
                "media": {
                    "fps": fps,
                    "frames": total,
                    "sample_rate": 48000,
                    "channels": 2,
                },
                "expected_path": str(expected.relative_to(ROOT)),
                "expected_sha512": hashlib.sha512(expected.read_bytes()).hexdigest(),
            }
        )
        rgb.unlink()
    run(
        "-i",
        OUT / "cfr.mkv",
        "-vf",
        "select='not(eq(mod(n,5),0))'",
        "-fps_mode",
        "vfr",
        "-c:v",
        "ffv1",
        "-c:a",
        "pcm_s16le",
        OUT / "vfr.mkv",
    )
    run(
        "-i",
        OUT / "cfr.mkv",
        "-itsoffset",
        "0.2",
        "-i",
        OUT / "cfr.wav",
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-c",
        "copy",
        OUT / "offset.mkv",
    )
    run(
        "-i", OUT / "cfr.mkv", "-c:v", "libx264", "-c:a", "aac", OUT / "rotate-base.mp4"
    )
    run(
        "-display_rotation",
        "90",
        "-i",
        OUT / "rotate-base.mp4",
        "-c",
        "copy",
        OUT / "rotate.mp4",
    )
    run(
        "-i",
        OUT / "cfr.mkv",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-x264-params",
        "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc",
        "-c:a",
        "copy",
        OUT / "hdr.mkv",
    )
    for name in ("vfr", "offset", "rotate", "hdr"):
        p = OUT / (name + (".mp4" if name == "rotate" else ".mkv"))
        expected = OUT / (name + ".expected.json")
        expected.write_text(
            json.dumps(
                {
                    "expected": "reject HDR" if name == "hdr" else "normalize",
                    "rotation": 90 if name == "rotate" else 0,
                    "audio_offset": "1/5" if name == "offset" else None,
                }
            )
            + "\n"
        )
        cases.append(
            {
                "case_id": name,
                "path": str(p.relative_to(ROOT)),
                "sha512": hashlib.sha512(p.read_bytes()).hexdigest(),
                "authorization": "Original generated fixture",
                "media": {"variant": name},
                "expected_path": str(expected.relative_to(ROOT)),
                "expected_sha512": hashlib.sha512(expected.read_bytes()).hexdigest(),
            }
        )
    (ROOT / "tests/fixtures/manifest.json").write_text(
        json.dumps(
            {"cases": cases, "youtube_url": None, "youtube_authorization": None},
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    make()
