from fractions import Fraction
from pathlib import Path

import pytest

from mlvideo.continuous import layouts, mix, pcm, plan, protect_speech


def case():
    pts = list(range(0, 480001, 1600))
    groups = [
        {"start_frame": 0, "end_frame": 150, "hold_frame": 149},
        {"start_frame": 150, "end_frame": 300, "hold_frame": 299},
    ]
    return pts, groups, [48000, 96000]


def test_every_frame_once_and_complete_pcm():
    pts, groups, dubs = case()
    t = plan(pts, groups, dubs)
    assert [f[0] for f in t["continuous"]] == list(range(300))
    assert t["output_samples"] == 624000
    for mode in ("continuous", "hold"):
        assert t[mode][0][1] == 0
        assert all(a[1] + a[2] == b[1] for a, b in zip(t[mode], t[mode][1:]))
        assert t[mode][-1][1] + t[mode][-1][2] == t["output_samples"]
    source = b"\x01\x02\x03\x04" * 480000
    voices = [b"\x05\x06\x07\x08" * n for n in dubs]
    output = mix(source, voices, t)
    for u, v in zip(t["units"], voices):
        a = u["chinese_start_sample"] * 4
        assert output[a : a + len(v)] == v
        a, n = u["english_start_sample"] * 4, 240000 * 4
        assert (
            output[a : a + n]
            == source[u["source_start_sample"] * 4 : u["source_end_sample"] * 4]
        )
        assert a == u["output_start_sample"] * 4
        zh = u["chinese_start_sample"] * 4
        assert zh == a + n
        assert output[zh : zh + len(v)] == v
    assert t["schema"] == "ContinuousExperiment.v3"
    assert t["audio_order"] == "en-zh"
    assert len(output) == t["output_samples"] * 4
    assert all(Fraction(u["video_speed"]) >= Fraction(2, 5) for u in t["units"])


def test_vfr_absolute_boundaries_do_not_accumulate_rounding():
    pts = [0, 1601, 8001, 16000, 479999, 480000]
    t = plan(pts, [{"start_frame": 0, "end_frame": 5, "hold_frame": 4}], [100001])
    assert t["continuous"][-1][1] + t["continuous"][-1][2] == 580001
    assert all(p[2] > 0 for p in t["continuous"])


@pytest.mark.parametrize(
    "change", ["gap", "overlap", "tail", "hold", "negative", "pts", "empty"]
)
def test_reject_invalid_plan(change):
    pts, groups, dubs = case()
    if change == "gap":
        groups[1]["start_frame"] += 1
    if change == "overlap":
        groups[1]["start_frame"] -= 1
    if change == "tail":
        groups[-1]["end_frame"] -= 1
    if change == "hold":
        groups[0]["hold_frame"] = 150
    if change == "negative":
        dubs[0] = -1
    if change == "pts":
        pts[10] = pts[9]
    if change == "empty":
        groups.clear()
    with pytest.raises(ValueError):
        plan(pts, groups, dubs)


def test_changed_audio_is_rejected(tmp_path):
    pts, groups, dubs = case()
    t = plan(pts, groups, dubs)
    with pytest.raises(ValueError):
        mix(bytes(480000 * 4), [b"", b""], t)
    with pytest.raises(ValueError, match="English-first"):
        mix(
            bytes(480000 * 4),
            [bytes(n * 4) for n in dubs],
            t | {"schema": "ContinuousExperiment.v1"},
        )
    with pytest.raises(ValueError):
        mix(bytes(4), [bytes(n * 4) for n in dubs], t)
    import wave

    path = tmp_path / "wrong.wav"
    with wave.open(str(path), "wb") as w:
        w.setparams((1, 2, 16000, 0, "NONE", ""))
        w.writeframes(bytes(32000))
    with pytest.raises(ValueError):
        pcm(path)


def test_speech_boundary_requires_regrouping():
    pts, groups, dubs = case()
    t = plan(pts, groups, dubs)
    protect_speech(t, [[48000, 200000], [250000, 400000]])
    for intervals in [[], [[200000, 250000]], [[400000, 500000]], [[1, 1]]]:
        with pytest.raises(ValueError):
            protect_speech(t, intervals)


def test_page_layout_and_invalid_boxes():
    font = Path("/System/Library/Fonts/STHeiti Medium.ttc")
    if not font.exists():
        pytest.skip("Requires CJK test font")
    page = {
        "start_frame": 0,
        "end_frame": 10,
        "english_box": [50, 300, 580, 320],
        "text": "中文测试",
        "evidence": "frame.png",
    }
    p, _ = layouts([page], 10, 640, 360, font)[0]
    assert p["chinese_box"][1] >= 328
    above = page | {"english_box": [50, 337, 580, 355]}
    p, _ = layouts([above], 10, 640, 360, font)[0]
    assert p["chinese_box"][3] < 337
    for invalid in [
        page | {"english_box": [0, 0, 700, 350]},
        page | {"evidence": ""},
        page | {"end_frame": 11},
    ]:
        with pytest.raises(ValueError):
            layouts([invalid], 10, 640, 360, font)
    with pytest.raises(ValueError):
        layouts([page, page], 10, 640, 360, font)


def test_chinese_precedes_source_silent_tail_without_losing_pcm():
    pts = [0, 48000, 96000, 144000, 192000]
    t = plan(
        pts,
        [
            {
                "start_frame": 0,
                "end_frame": 4,
                "hold_frame": 3,
                "speech_end_sample": 96000,
            }
        ],
        [48000],
    )
    source = b"\x01\x00\x02\x00" * 96000 + b"\x03\x00\x04\x00" * 96000
    dub = b"\x05\x00\x06\x00" * 48000
    assert t["units"][0]["chinese_start_sample"] == 96000
    assert mix(source, [dub], t) == source[: 96000 * 4] + dub + source[96000 * 4 :]


def test_long_dub_retains_audio_and_reports_slow_motion():
    pts, groups, dubs = case()
    dubs[0] = 480000
    t = plan(pts, groups, dubs)
    assert t["motion_review_units"] == ["0"]
    assert t["units"][0]["dub_samples"] == 480000
    assert t["quality_status"] == "REVIEW"


def test_subtitle_font_must_cover_every_glyph():
    font = Path("/System/Library/Fonts/STHeiti Medium.ttc")
    if not font.exists():
        pytest.skip("CJK font required")
    page = {
        "start_frame": 0,
        "end_frame": 10,
        "english_box": [50, 300, 580, 320],
        "text": chr(0x10FFFF),
        "evidence": "frame.png",
    }
    with pytest.raises(ValueError, match="glyphs"):
        layouts([page], 10, 640, 360, font)
