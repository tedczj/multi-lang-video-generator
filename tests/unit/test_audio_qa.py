import array
import math
import wave
from unittest.mock import Mock

import pytest

from mlvideo.audio_qa import inspect_audio, normalize_and_check
from mlvideo.contracts import validate
from mlvideo.engine import Engine
from mlvideo.util import atomic_json, sha512
from workers.cosyvoice_worker import select_text


def audio(tmp_path, leading=0, level=5000):
    path = tmp_path / "input.wav"
    values = array.array(
        "h", [int(20 * math.sin(i)) for i in range(round(24000 * leading))]
    )
    values.extend(
        int(level * math.sin(2 * math.pi * 440 * i / 24000)) for i in range(24000)
    )
    with wave.open(str(path), "wb") as f:
        f.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        f.writeframes(values.tobytes())
    return path, len(values)


@pytest.mark.parametrize("leading,expected", [(2.6, "REVIEW"), (0.34, "PASS")])
def test_leading_noise_requires_review_without_trimming(tmp_path, leading, expected):
    path, count = audio(tmp_path, leading)
    original_hash = sha512(path)
    clip = {
        "audio_sha512": original_hash,
        "sample_rate": 24000,
        "channels": 1,
        "frames": count,
    }
    report, frames, _ = normalize_and_check(path, clip, tmp_path)
    validate("AudioQA.v1", report)
    assert report["overall"] == "REVIEW"  # No inferred human approval.
    assert (
        next(c for c in report["checks"] if c["id"] == "leading_audio")["status"]
        == expected
    )
    assert report["energy_onset_seconds"] == pytest.approx(leading, abs=0.02)
    assert frames == count * 2 and report["trimmed_samples"] == 0
    assert sha512(path) == original_hash


def test_silence_is_failed_quality(tmp_path):
    path, _ = audio(tmp_path, level=0)
    assert inspect_audio(path, tmp_path)["overall"] == "FAIL"


def test_audio_metadata_binding_cannot_drift(tmp_path):
    path, count = audio(tmp_path)
    with pytest.raises(ValueError, match="does not match"):
        normalize_and_check(
            path,
            {
                "audio_sha512": "0" * 128,
                "sample_rate": 24000,
                "channels": 1,
                "frames": count,
            },
            tmp_path,
        )


def test_clipping_is_failed_quality(tmp_path):
    path = tmp_path / "clipped.wav"
    with wave.open(str(path), "wb") as f:
        f.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        f.writeframes(array.array("h", [-32768] * 24000).tobytes())
    assert inspect_audio(path, tmp_path)["overall"] == "FAIL"


def test_failed_quality_cannot_be_consumed(tmp_path):
    db = Mock()
    db.one.return_value = {
        "asset_sha512": "a",
        "state": "SUCCEEDED",
        "quality_status": "FAIL",
    }
    with pytest.raises(ValueError, match="quality"):
        Engine(db, {"data_root": str(tmp_path)}).artifact("artifact", "a")


def test_translation_is_selected_by_id_not_position():
    translation = {
        "items": [{"unit_id": "b", "text": "乙"}, {"unit_id": "a", "text": "甲"}]
    }
    assert select_text(translation, "a") == "甲"
    with pytest.raises(ValueError, match="Missing"):
        select_text(translation, "missing")
    with pytest.raises(ValueError, match="Missing"):
        select_text(translation, "")
    translation["items"].append({"unit_id": "a", "text": "重复"})
    with pytest.raises(ValueError, match="duplicate"):
        select_text(translation, "a")


def test_retry_keeps_original_model_deployment(tmp_path):
    original = [{"model_dir": "/original"}]
    request = tmp_path / "request.json"
    atomic_json(request, {"inputs": {}, "params": {}, "models": original})
    db = Mock()
    db.one.return_value = {
        "request_path": request.name,
        "request_hash": sha512(request),
        "node": "N11",
        "strategy_id": "cosyvoice3_zero_shot",
        "scope": "u1",
    }
    engine = Engine(
        db,
        {
            "data_root": str(tmp_path),
            "models": {"N11/cosyvoice3_zero_shot": [{"model_dir": "/changed"}]},
        },
    )
    engine.run = Mock()
    engine.retry("asset", "previous")
    assert engine.run.call_args.kwargs["model_deployment"] == original


def test_reference_conversion_handles_float_overshoot_without_clipping(tmp_path):
    import struct

    from mlvideo.audio_qa import safe_reference_pcm

    payload = array.array("f", [1.2, -1.2, 0.4, -0.4] * 24000).tobytes()
    raw = tmp_path / "raw.wav"
    raw.write_bytes(
        b"RIFF"
        + struct.pack("<I", 36 + len(payload))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 3, 2, 48000, 384000, 8, 32)
        + b"data"
        + struct.pack("<I", len(payload))
        + payload
    )
    before = sha512(raw)
    result = safe_reference_pcm(raw, tmp_path / "reference.wav", tmp_path)
    assert 0 < result["applied_gain"] < 1 and sha512(raw) == before
    with wave.open(str(tmp_path / "reference.wav")) as w:
        samples = array.array("h", w.readframes(w.getnframes()))
        assert w.getnframes() == 48000
    assert max(abs(v) for v in samples) < 32767
