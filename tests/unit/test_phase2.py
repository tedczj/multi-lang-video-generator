from unittest.mock import Mock

import pytest

from mlvideo.engine import Engine
from mlvideo.phase2 import group_utterances, parse_srt
from mlvideo.subtitles import review
from mlvideo.translation import batches, validate_translation
from mlvideo.util import atomic_json, sha512


def items(count, chars=1):
    return [{"unit_id": str(i), "text": "文" * chars} for i in range(count)]


@pytest.mark.parametrize("count,expected", [(8, [8]), (9, [8, 1])])
def test_translation_batch_unit_limit(count, expected):
    assert list(map(len, batches(items(count)))) == expected


def test_translation_unicode_character_limit():
    assert len(batches(items(1, 6000))) == 1
    assert list(map(len, batches(items(2, 3001)))) == [1, 1]
    with pytest.raises(ValueError, match="6000"):
        batches(items(1, 6001))
    with pytest.raises(ValueError, match="duplicate"):
        batches(items(1) + items(1))


@pytest.mark.parametrize(
    "returned",
    [
        [{"unit_id": "0", "text": "甲"}],
        [{"unit_id": "0", "text": "甲"}, {"unit_id": "0", "text": "乙"}],
        [{"unit_id": "0", "text": "甲"}, {"unit_id": "extra", "text": "乙"}],
        [{"unit_id": "0", "text": "甲"}, {"unit_id": "1", "text": " "}],
    ],
)
def test_translation_rejects_bad_ids_and_empty_content(returned):
    with pytest.raises(ValueError):
        validate_translation(items(2), {"items": returned})


def test_translation_order_restored_by_id():
    result = validate_translation(
        items(2),
        {"items": [{"unit_id": "1", "text": "乙"}, {"unit_id": "0", "text": "甲"}]},
    )
    assert [r["text"] for r in result] == ["甲", "乙"]


def speech():
    return {
        "coverage_status": "PASS",
        "segments": [
            {
                "id": "s1",
                "start_sample": 1000,
                "end_sample": 48000,
                "speaker_id": "a",
                "text": "One.",
            },
            {
                "id": "s2",
                "start_sample": 96000,
                "end_sample": 144000,
                "speaker_id": "a",
                "text": "Two.",
            },
        ],
        "protected_intervals": [
            {"start_sample": 1000, "end_sample": 48000},
            {"start_sample": 96000, "end_sample": 144000},
        ],
    }


def group(s):
    return group_utterances(
        s,
        {"cues": [{"id": "c", "start_sample": 1000, "end_sample": 140000}]},
        {
            "sample_rate": 48000,
            "fps": "25",
            "source_samples": 192000,
            "source_frames": 100,
        },
        {"speech": "sp", "captions": "ca", "canonical": "cm"},
    )["items"]


def test_group_many_to_many_caption_lineage_last_cut():
    result = group(speech())
    assert len(result) == 2 and result[0]["safe_cut_frame"] == 50
    assert result[0]["caption_ids"] == result[1]["caption_ids"] == ["c"]


def test_unknown_coverage_protects_uncaptioned_speech_and_tail():
    s = speech()
    s["coverage_status"] = "REVIEW"
    s["protected_intervals"] = [{"start_sample": 0, "end_sample": 192000}]
    result = group(s)
    assert len(result) == 1
    assert (
        result[0]["start_sample"],
        result[0]["end_sample"],
        result[0]["safe_cut_frame"],
    ) == (0, 192000, 100)
    assert result[0]["speech_ids"] == ["s1", "s2"]


def test_unsafe_multispeaker_merge_requires_review():
    s = speech()
    s["segments"][1]["start_sample"] = 47000
    s["segments"][1]["speaker_id"] = "b"
    with pytest.raises(ValueError, match="different speakers"):
        group(s)


def test_no_integral_cut_merges_same_speaker():
    s = speech()
    s["segments"][0]["end_sample"] = 48001
    s["segments"][1]["start_sample"] = 48002
    assert len(group(s)) == 1


def test_soft_caption_time_offset_and_multiline():
    c = parse_srt("1\n00:00:01,000 --> 00:00:02,500\nFirst\nsecond\n", 4800)[0]
    assert (c["start_sample"], c["end_sample"], c["text"]) == (
        52800,
        124800,
        "First\nsecond",
    )


def test_retry_retains_every_input_ordinal(tmp_path):
    request = tmp_path / "request.json"
    atomic_json(
        request,
        {
            "inputs": {"dubs": [{"artifact_id": "a"}, {"artifact_id": "b"}]},
            "params": {},
            "models": [],
        },
    )
    db = Mock()
    db.one.return_value = {
        "request_path": "request.json",
        "request_hash": sha512(request),
        "node": "N18",
        "strategy_id": "dub_ffmpeg",
        "scope": "render",
    }
    engine = Engine(db, {"data_root": str(tmp_path)})
    engine.run = Mock()
    engine.retry("asset", "old")
    assert engine.run.call_args.args[3] == {"dubs": ["a", "b"]}


def test_review_never_passes_missing_human_or_failed_audio():
    ref = {"artifact_id": "render", "sha512": "a" * 128}
    qa = {"checks": [{"id": "decode", "status": "PASS", "required": True}]}
    result, decision = review(ref, qa, [{"overall": "REVIEW"}], {"events": [{}]})
    assert result["overall"] == decision["decision"] == "REVIEW"
    result, decision = review(ref, qa, [{"overall": "FAIL"}], {"events": [{}]})
    assert result["overall"] == decision["decision"] == "FAIL"
    assert (
        decision["render_artifact_id"] == "render"
        and decision["render_sha512"] == "a" * 128
    )


def test_layout_overflow_rejected():
    from PIL import ImageFont

    from mlvideo.subtitles import wrap

    with pytest.raises(ValueError, match="Glyph"):
        wrap("W", ImageFont.load_default(), 1)


def test_human_wrong_translation_or_wrong_render_cannot_pass():
    ref = {"artifact_id": "render", "sha512": "a" * 128}
    qa = {"checks": [{"id": "decode", "status": "PASS", "required": True}]}
    _, pending = review(ref, qa, [{"overall": "REVIEW"}], {"events": [{}]})
    human = {
        **pending,
        "reviewer": "test-only reviewer",
        "decision": "FAIL",
        "checks": [
            {
                **c,
                "status": "FAIL" if c["id"] == "translation_semantics" else "PASS",
                "reason": "Injected wrong translation"
                if c["id"] == "translation_semantics"
                else "Test assertion",
            }
            for c in pending["checks"]
            if c["status"] == "REVIEW"
        ],
    }
    result, _ = review(ref, qa, [{"overall": "REVIEW"}], {"events": [{}]}, human)
    assert result["overall"] == "FAIL"
    human["render_sha512"] = "b" * 128
    with pytest.raises(ValueError, match="exact rendered"):
        review(ref, qa, [{"overall": "REVIEW"}], {"events": [{}]}, human)


def test_human_decision_missing_check_rejected():
    ref = {"artifact_id": "render", "sha512": "a" * 128}
    with pytest.raises(ValueError, match="each unresolved"):
        review(
            ref,
            {"checks": []},
            [],
            {"events": [{}]},
            {
                "render_artifact_id": "render",
                "render_sha512": "a" * 128,
                "reviewer": "test reviewer",
                "decision": "PASS",
                "checks": [],
            }
            | {},
        )


def test_annotation_cannot_omit_vad_only_speech():
    from mlvideo.speech import apply_annotation

    ref = {"sha512": "a" * 128, "artifact_id": "pcm"}
    a = {
        "audio_sha512": "a" * 128,
        "reviewer": "test reviewer",
        "segments": [
            {"start_sample": 0, "end_sample": 48000, "speaker_id": "a", "text": "One"}
        ],
    }
    with pytest.raises(ValueError, match="unprotected"):
        apply_annotation(a, ref, 96000, [], [{"start": 20000, "end": 25000}])
    a["segments"][0]["end_sample"] = 96000
    rows = []
    protected, status, _ = apply_annotation(
        a, ref, 96000, rows, [{"start": 20000, "end": 25000}]
    )
    assert (
        status == "PASS"
        and rows[0]["speaker_id"] == "a"
        and protected[0]["end_sample"] == 96000
    )
    a["audio_sha512"] = "b" * 128
    with pytest.raises(ValueError, match="exact PCM"):
        apply_annotation(a, ref, 96000, rows, [])


def test_hard_caption_consensus_deduplicates_frames_and_keeps_ocr_text():
    from mlvideo.phase2 import choose_text

    track = {
        "cues": [
            {"frame": frame, "text": text, "boxes": [box]}
            for frame in (0, 25)
            for text, box in [
                ("the wall.", [0, 50, 100, 70]),
                ("She saw something sparkly from", [0, 20, 300, 40]),
            ]
        ]
    }
    utterances = {
        "items": [
            {
                "text": "ASR must not replace picture text",
                "merge_reason": "Protected source",
            }
        ]
    }
    result = choose_text(utterances, track, "caption_consensus")
    assert result["items"][0]["text"] == "She saw something sparkly from the wall."
    track["cues"][0]["text"] = "something else"
    with pytest.raises(ValueError, match="OCR disagrees"):
        choose_text(utterances, track, "caption_consensus")


def test_footer_preserves_all_source_pixels_and_only_draws_chinese_below():
    from PIL import Image

    from mlvideo.subtitles import overlay_frame

    frame = bytes([100, 120, 140]) * 8
    layout = {
        "width": 4,
        "height": 2,
        "canvas_height": 4,
        "events": [{"start_sample": 0, "end_sample": 48000}],
    }
    overlay = Image.new("RGBA", (4, 4))
    overlay.putpixel((1, 3), (255, 255, 255, 255))
    actual = overlay_frame(frame, 0, 25, layout, [overlay])
    assert actual[: len(frame)] == frame
    assert len(actual) == 4 * 4 * 3 and actual[
        (3 * 4 + 1) * 3 : (3 * 4 + 2) * 3
    ] == bytes([255] * 3)
    after = overlay_frame(frame, 30, 25, layout, [overlay])
    assert after == frame + bytes([18, 24, 32]) * 8


def test_caption_spacing_consensus_retains_review_and_rejects_ties():
    from mlvideo.phase2 import choose_text

    def track(texts):
        return {
            "cues": [
                {"frame": i, "text": text, "boxes": [[0, 0, 100, 20]]}
                for i, text in enumerate(texts)
            ]
        }

    def units():
        return {"items": [{"text": "ASR", "merge_reason": "Protected"}]}

    result = choose_text(
        units(), track(["the wall.", "thewall.", "the wall."]), "caption_consensus"
    )
    assert result["items"][0]["text"] == "the wall."
    assert "2 spacing variants" in result["items"][0]["merge_reason"]
    with pytest.raises(ValueError, match="Ambiguous OCR spacing"):
        choose_text(units(), track(["the wall.", "thewall."]), "caption_consensus")


def test_chinese_placement_prefers_below_and_falls_back_above():
    from mlvideo.subtitles import chinese_baseline

    below = chinese_baseline([100, 550, 1100, 650], 720, 5, 35)
    assert below + 5 == 658 and below + 35 < 720
    above = chinese_baseline([100, 670, 1100, 715], 720, 5, 35)
    assert above + 35 == 662 and above + 5 > 0
    with pytest.raises(ValueError, match="No safe"):
        chinese_baseline([100, 15, 1100, 715], 720, 5, 35)


def test_preserved_english_keeps_chinese_through_post_gap_and_final_frame():
    from PIL import Image

    from mlvideo.subtitles import overlay_frame, subtitle_display_end

    t = {
        "utterances": [{"output_start_sample": 0}],
        "dubs": [{"start_sample": 48000, "samples": 48000}],
        "output_samples": 144000,
    }
    end = subtitle_display_end(t, 0, True)
    assert end == 144000 and subtitle_display_end(t, 0, False) == 96000
    layout = {
        "width": 1,
        "height": 1,
        "events": [{"start_sample": 0, "end_sample": end}],
    }
    overlay = Image.new("RGBA", (1, 1), (255, 255, 255, 255))
    assert overlay_frame(bytes(3), 74, 25, layout, [overlay]) == bytes([255] * 3)
    t["utterances"].append({"output_start_sample": 120000})
    assert subtitle_display_end(t, 0, True) == 120000


def test_source_caption_end_maps_after_hold_not_after_dub_audio():
    from mlvideo.subtitles import caption_output_sample, subtitle_display_end
    from mlvideo.timeline import plan

    c = {
        "fps": "25",
        "sample_rate": 48000,
        "source_frames": 250,
        "source_samples": 480000,
    }
    t = plan(
        c,
        [
            {
                "id": "one",
                "start_sample": 0,
                "end_sample": 48000,
                "safe_cut_frame": 40,
                "visual_cut_limit_frame": 40,
                "dub_samples": 24000,
            },
            {
                "id": "two",
                "start_sample": 96000,
                "end_sample": 144000,
                "safe_cut_frame": 250,
                "dub_samples": 24000,
            },
        ],
    )
    t["utterances"][0]["caption_end_frame"] = 40
    source_after_hold = next(
        p
        for p in t["pieces"]
        if p["kind"] == "source" and p["source_start_frame"] == 40
    )
    expected = source_after_hold["output_start_frame"] * 1920
    assert caption_output_sample(t, 40) == expected
    assert subtitle_display_end(t, 0, True) == expected
    assert expected > t["dubs"][0]["start_sample"] + t["dubs"][0]["samples"]
    assert caption_output_sample(t, 250) == t["output_samples"]


def test_caption_page_requires_complete_speech_before_visual_cut():
    from mlvideo.caption_pages import build_pages

    c = {"fps": "25", "source_frames": 100}
    captions = {
        "cues": [
            {"id": "cue", "frame": 0, "text": "One.", "boxes": [[10, 70, 100, 90]]}
        ]
    }
    speech = {
        "words": [{"start_sample": 24000, "end_sample": 48000, "text": "One."}],
        "segments": [{"id": "speech", "start_sample": 24000, "end_sample": 48000}],
    }
    refs = {"speech": "sp", "captions": "ca", "canonical": "cm"}
    page = [{"text": "One.", "start_frame": 0, "end_frame": 50}]
    vad = {"sample_rate": 16000, "intervals": [{"start": 6400, "end": 17600}]}
    result = build_pages(speech, captions, vad, c, refs, page)
    assert result["items"][0]["caption_end_frame"] == 50
    vad["intervals"][0]["end"] = 40000
    with pytest.raises(ValueError, match="crosses caption"):
        build_pages(speech, captions, vad, c, refs, page)


def test_recipe_rejects_multi_artifact_array_on_single_input_before_execution(tmp_path):
    from unittest.mock import Mock

    from mlvideo.pipeline import run_recipe

    engine = Mock()
    engine.root = tmp_path
    recipe = {
        "steps": [
            {
                "id": "probe",
                "node": "N03",
                "strategy": "ffprobe",
                "inputs": {"source": ["a", "b"]},
                "params": {},
            }
        ]
    }
    with pytest.raises(ValueError, match="fixed input array"):
        run_recipe(engine, "asset", recipe)
    engine.run.assert_not_called()
    engine.db.insert.assert_not_called()
