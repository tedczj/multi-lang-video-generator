"""Independent sample oracle for real WAV insertion (not model-quality evidence)."""

import array
import subprocess
import wave

import pytest

from mlvideo.config import ROOT
from mlvideo.media import normalize, probe, render
from mlvideo.timeline import plan
from mlvideo.util import read_json, sha512


@pytest.mark.parametrize("footer", [0, 16])
def test_real_dub_pcm_and_dynamic_overlay(tmp_path, footer):
    source = ROOT / "tests/fixtures/generated/cfr.mkv"
    normalized = tmp_path / "normalized"
    normalized.mkdir()
    normalize(source, probe(source, normalized), normalized)
    c = read_json(normalized / "canonical.json")
    # Five seconds of source, full speech protected. One second pre + 0.5 dub + one second post.
    t = plan(
        c,
        [
            {
                "id": "u",
                "start_sample": 0,
                "end_sample": 240000,
                "safe_cut_frame": 125,
                "dub_samples": 24000,
            }
        ],
    )
    assert t["output_frames"] == 188  # ceil(7.5 seconds * 25 fps)
    assert t["dubs"][0]["start_sample"] == 288000
    dub = tmp_path / "dub.wav"
    samples = array.array(
        "h", (v for i in range(24000) for v in (i % 701 - 350, 350 - i % 701))
    )
    with wave.open(str(dub), "wb") as w:
        w.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
        w.writeframes(samples.tobytes())
    t["dubs"][0].pop("tone_hz")
    t["dubs"][0]["audio_sha512"] = sha512(dub)
    work = tmp_path / "render"
    work.mkdir()
    seen = []

    def overlay(frame, index, fps):
        seen.append(index)
        # Distinct output-time pixels even when the source frame is held.
        return bytes([index % 256, 0, 0]) + frame[3:] + bytes(64 * footer * 3)

    render(
        normalized / "canonical.mkv",
        normalized / "canonical.wav",
        t,
        work,
        dub_audio={sha512(dub): dub},
        overlay=overlay,
        output_height=48 + footer,
    )
    assert seen == list(range(188))
    with wave.open(str(work / "master.wav")) as w:
        result = w.readframes(w.getnframes())
        assert w.getnframes() == 360960
    with wave.open(str(normalized / "canonical.wav")) as w:
        original = w.readframes(w.getnframes())
    assert result[: 240000 * 4] == original
    assert result[240000 * 4 : 288000 * 4] == bytes(48000 * 4)
    assert result[288000 * 4 : 312000 * 4] == samples.tobytes()
    assert result[312000 * 4 :] == bytes((360960 - 312000) * 4)
    raw = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(work / "master.mkv"),
            "-map",
            "0:v:0",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ]
    )
    size = 64 * (48 + footer) * 3
    assert len(raw) == 188 * size
    assert [raw[i * size] for i in range(188)] == list(range(188))


def test_real_dub_rejects_wrong_sample_count(tmp_path):
    source = ROOT / "tests/fixtures/generated/cfr.mkv"
    normalized = tmp_path / "normalized"
    normalized.mkdir()
    normalize(source, probe(source, normalized), normalized)
    c = read_json(normalized / "canonical.json")
    t = plan(
        c,
        [
            {
                "id": "u",
                "start_sample": 0,
                "end_sample": 48000,
                "safe_cut_frame": 125,
                "dub_samples": 24000,
            }
        ],
    )
    audio = normalized / "canonical.wav"
    t["dubs"][0]["audio_sha512"] = sha512(audio)
    work = tmp_path / "render"
    work.mkdir()
    with pytest.raises(ValueError, match="Dub PCM"):
        render(
            normalized / "canonical.mkv",
            audio,
            t,
            work,
            dub_audio={sha512(audio): audio},
        )


def test_excerpt_keeps_source_binding_and_exact_pcm_interval(tmp_path):
    from mlvideo.contracts import validate
    from mlvideo.phase2 import run
    from mlvideo.util import atomic_json

    source = ROOT / "tests/fixtures/generated/cfr.mkv"
    info = probe(source, tmp_path)
    atomic_json(tmp_path / "probe.json", info)
    outputs = []
    request = {
        "node": "N04",
        "strategy_id": "excerpt",
        "params": {"start_seconds": 1, "end_seconds": 2, "height": 144},
        "inputs": {
            "source": [
                {
                    "path": str(source),
                    "artifact_id": "fixed-full-source",
                    "sha512": sha512(source),
                }
            ]
        },
    }
    run(request, tmp_path, lambda *args: outputs.append(args))
    receipt = read_json(tmp_path / "excerpt.json")
    assert receipt["source_artifact_id"] == "fixed-full-source" and receipt[
        "source_sha512"
    ] == sha512(source)
    c = read_json(tmp_path / "canonical.json")
    validate("CanonicalMedia.v1", c)
    assert c["audio_payload_samples"] == 48000
    assert c["source_samples"] == c["source_frames"] * 1920
    with wave.open(str(tmp_path / "canonical.wav")) as w:
        actual = array.array("h", w.readframes(w.getnframes()))
    expected = array.array(
        "h", (v for i in range(48000, 96000) for v in (i % 997 - 498, 498 - i % 997))
    )
    offset = c["audio_start_sample"] * 2
    assert actual[offset : offset + len(expected)] == expected
    assert all(v == 0 for v in actual[:offset])
    assert all(v == 0 for v in actual[offset + len(expected) :])
