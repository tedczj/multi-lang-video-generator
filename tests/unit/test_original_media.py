"""Real FFmpeg/PyAV tests: source bytes, PCM payload and native presentation times."""
import json
import subprocess
import wave
from fractions import Fraction as F

import pytest

from mlvideo.config import ROOT
from mlvideo.contracts import validate
from mlvideo.media import preserve_source, probe, render
from mlvideo.timeline import plan, quantize, cut_frame
from mlvideo.util import read_json, sha512


@pytest.mark.parametrize("name", ["cfr", "ntsc", "vfr", "offset", "rotate", "hdr"])
def test_original_bytes_frames_and_complete_audio(name, tmp_path):
    source = ROOT / "tests/fixtures/generated" / (name + (".mp4" if name == "rotate" else ".mkv"))
    preserve_source(source, probe(source, tmp_path), tmp_path)
    c = read_json(tmp_path / "canonical.json")
    validate("CanonicalMedia.v1", c)
    assert sha512(source) == sha512(tmp_path / "original.bin")
    assert c["frame_mapping"] == list(range(c["source_frames"]))
    assert c["video_lead_frames"] == c["video_tail_frames"] == 0
    expected = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(source),
        "-map", "0:a:0", "-ar", "48000", "-ac", "2", "-f", "s16le", "-"])
    with wave.open(str(tmp_path / "canonical.wav")) as wav:
        assert wav.getnframes() == c["source_samples"]
        assert wav.readframes(c["audio_start_sample"]) == b"\0" * (c["audio_start_sample"] * 4)
        assert wav.readframes(c["audio_payload_samples"]) == expected
    assert not (tmp_path / "canonical.mkv").exists()
    assert not (tmp_path / "padded.mkv").exists()
    if name == "offset":
        assert c["source_frames"] == 125
        assert c["audio_start_sample"] == 9600
        assert c["audio_payload_samples"] == 240000
        assert c["source_samples"] == 249600


@pytest.mark.parametrize("name", ["cfr", "ntsc", "vfr", "offset", "rotate"])
@pytest.mark.parametrize("with_dub", [False, True])
def test_native_render_keeps_pts_and_audio_tail(name, with_dub, tmp_path):
    source = ROOT / "tests/fixtures/generated" / (name + (".mp4" if name == "rotate" else ".mkv"))
    preserve_source(source, probe(source, tmp_path), tmp_path)
    c = read_json(tmp_path / "canonical.json")
    units = [{"id": "u", "start_sample": 0, "end_sample": c["source_samples"],
              "safe_cut_frame": c["source_frames"], "dub_samples": 24000}] if with_dub else []
    t = plan(c, units)
    validate("TimelinePlan.v1", t)
    assert t["frame_samples"][:c["source_frames"]] == c["frame_samples"][:-1]
    assert cut_frame(c, c["source_samples"]) == c["source_frames"]
    seen = []
    def overlay(pixels, sample, rate):
        seen.append(round(sample / rate * 48000))
        return pixels
    render(tmp_path / "original.bin", tmp_path / "canonical.wav", t, tmp_path, overlay=overlay)
    assert seen == t["frame_samples"][:-1]
    for filename in ("master.mkv", "preview.mp4"):
        frames = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_frames", "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(tmp_path / filename)]))["frames"]
        assert len(frames) == t["output_frames"]
        actual = [quantize(F(f["best_effort_timestamp_time"])) for f in frames]
        assert max(abs(a-b) for a,b in zip(actual, t["frame_samples"])) <= 48  # Matroska ms clock
    with wave.open(str(tmp_path / "canonical.wav")) as original, wave.open(str(tmp_path / "master.wav")) as output:
        assert output.readframes(c["source_samples"]) == original.readframes(c["source_samples"])
        assert output.getnframes() == t["output_samples"]


def test_native_midstream_hold_preserves_source_spacing():
    c = {"fps": "25", "sample_rate": 48000, "source_frames": 5, "source_samples": 480000,
         "frame_samples": [0, 24000, 96000, 144000, 336000, 480000]}
    t = plan(c, [{"id": "a", "start_sample": 0, "end_sample": 24000, "safe_cut_frame": 2, "dub_samples": 96000},
                 {"id": "b", "start_sample": 100000, "end_sample": 144000, "safe_cut_frame": 5, "dub_samples": 24000}])
    assert any(p["kind"] == "hold" for p in t["pieces"])
    for p in t["pieces"]:
        if p["kind"] == "source":
            source = c["frame_samples"][p["source_start_frame"]:p["source_end_frame"]]
            output = t["frame_samples"][p["output_start_frame"]:p["output_end_frame"]]
            assert len({b-a for a,b in zip(source, output)}) == 1


def test_native_midstream_render_preserves_pixels_and_pcm(tmp_path):
    source = ROOT / "tests/fixtures/generated/vfr.mkv"
    preserve_source(source, probe(source, tmp_path), tmp_path)
    c = read_json(tmp_path / "canonical.json")
    t = plan(c, [{"id": "a", "start_sample": 0, "end_sample": 24000,
                  "safe_cut_frame": cut_frame(c, 60000), "dub_samples": 96000},
                 {"id": "b", "start_sample": 60000, "end_sample": 96000,
                  "safe_cut_frame": c["source_frames"], "dub_samples": 24000}])
    dubs = {}
    for dub in t["dubs"]:
        path = tmp_path / (dub["id"] + ".wav")
        with wave.open(str(path), "wb") as wav:
            wav.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\0" * (dub["samples"] * 4))
        dub["audio_sha512"] = sha512(path)
        dubs[dub["audio_sha512"]] = path
    render(tmp_path / "original.bin", tmp_path / "canonical.wav", t, tmp_path, dub_audio=dubs)
    def rgb(path):
        return subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0",
            "-fps_mode", "passthrough", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
    original = rgb(source)
    size = len(original) // c["source_frames"]
    indices = []
    for p in t["pieces"]:
        if p["kind"] == "source":
            indices.extend(range(p["source_start_frame"], p["source_end_frame"]))
        else:
            indices.extend([p["source_hold_frame"]] * (p["output_end_frame"] - p["output_start_frame"]))
    assert rgb(tmp_path / "master.mkv") == b"".join(original[i*size:(i+1)*size] for i in indices)
    with wave.open(str(tmp_path / "canonical.wav")) as src, wave.open(str(tmp_path / "master.wav")) as dst:
        cursor = 0
        for p in t["pieces"]:
            n = p["output_end_sample"] - p["output_start_sample"]
            if p["kind"] == "source":
                src.setpos(cursor)
                assert dst.readframes(n) == src.readframes(n)
                cursor += n
            else:
                assert dst.readframes(n) == b"\0" * (n * 4)
        assert cursor == c["source_samples"]
