import pytest

from mlvideo.asr_refine import apply, candidates
from mlvideo.auto_script import validate_script
from mlvideo.caption_tracking import match_pages, reading_order
from mlvideo.generate import Job
from mlvideo.generate_prepare import safe_groups


def test_word_coverage_and_attribution_parts():
    words = [
        {"text": t, "start_sample": i * 10000, "end_sample": (i + 1) * 10000}
        for i, t in enumerate(["Hi,", " said", " Monkey."])
    ]
    raw = {
        "items": [
            {
                "parts": [
                    {
                        "start_word": 0,
                        "end_word": 1,
                        "speaker": "monkey",
                        "translation": "你好，",
                    },
                    {
                        "start_word": 1,
                        "end_word": 3,
                        "speaker": "narrator",
                        "translation": "猴子说道。",
                    },
                ]
            }
        ]
    }
    result = validate_script(words, raw)
    assert len(result) == 1 and len(result[0]["parts"]) == 2
    assert result[0]["text"] == "你好，猴子说道。"
    for bad in [
        raw | {"items": []},
        {"items": [{"parts": [raw["items"][0]["parts"][1]]}]},
    ]:
        with pytest.raises(ValueError):
            validate_script(words, bad)


def test_vad_crossing_requires_group_merge():
    script = {
        "items": [
            {
                "id": "a",
                "parts": [1],
                "text": "甲",
                "source_text": "A",
                "start_sample": 10000,
                "end_sample": 40000,
            },
            {
                "id": "b",
                "parts": [2],
                "text": "乙",
                "source_text": "B",
                "start_sample": 50000,
                "end_sample": 80000,
            },
        ]
    }
    vad = {"intervals": [{"start": 3000, "end": 30000}]}
    groups = safe_groups(script, {"frame_samples": list(range(0, 96001, 1600))}, vad)
    assert len(groups) == 1 and groups[0]["parts"] == [1, 2]
    assert groups[0]["speech_end_sample"] == 90000


def test_resume_rejects_input_and_artifact_changes(tmp_path):
    config = {"models": {}}
    job = Job(tmp_path, "https://www.youtube.com/watch?v=abcdefghijk", config)
    output = tmp_path / "result.txt"
    output.write_text("one")
    calls = []

    def action():
        calls.append(1)
        return {"done": True}, [output]

    assert job.stage("step", {"input": 1}, action) == {"done": True}
    assert job.stage("step", {"input": 1}, action) == {"done": True}
    assert len(calls) == 1
    with pytest.raises(ValueError, match="inputs changed"):
        job.stage("step", {"input": 2}, action)
    output.write_text("tampered")
    with pytest.raises(ValueError, match="output changed"):
        job.stage("step", {"input": 1}, action)
    with pytest.raises(ValueError, match="source/models changed"):
        Job(tmp_path, "another", config, resume=True)


def test_ocr_reading_order_handles_stylized_title():
    lines = [
        {"text": "WEST", "box": [574, 243, 1141, 380]},
        {"text": "the", "box": [352, 265, 545, 370]},
        {"text": "to", "box": [204, 283, 334, 370]},
    ]
    assert [l["text"] for l in reading_order(lines)] == ["to", "the", "WEST"]


def test_caption_missing_spaces_and_adjacent_line_geometry():
    scan = {
        "height": 1080,
        "rows": [
            {
                "seconds": 2,
                "frame": 60,
                "evidence": "f.png",
                "lines": [
                    {"text": "Weall lovethisstream,", "box": [300, 840, 1500, 900]},
                    {"text": "she said.", "box": [300, 920, 700, 980]},
                ],
            }
        ],
    }
    groups = [
        {
            "id": "u",
            "source_text": "We all love this stream, she said.",
            "text": "我们喜欢这条溪流，她说。",
            "start_sample": 48000,
            "end_sample": 144000,
        }
    ]
    c = match_pages(scan, groups)
    assert c[0]["english_box"] == [300, 840, 1500, 980]


def test_subtitle_detects_missing_attribution_and_refines_from_audio():
    asr = {
        "segments": [
            {
                "id": 0,
                "start": 1,
                "end": 2,
                "text": "I'm not going to try.",
                "words": [],
            },
            {"id": 1, "start": 5, "end": 6, "text": "Me neither.", "words": []},
        ]
    }
    scan = {
        "height": 1080,
        "rows": [
            {
                "seconds": 1.5,
                "evidence": "f.png",
                "lines": [
                    {
                        "text": "I'm not going to try, said a tall monkey.",
                        "box": [300, 900, 1500, 980],
                    }
                ],
            }
        ],
    }
    requests = candidates(asr, scan)
    assert requests[0]["index"] == 0
    patch = requests[0] | {
        "result": {
            "text": "I'm not going to try. Said a tall monkey.",
            "segments": [
                {
                    "start": 0.2,
                    "end": 2.8,
                    "text": "I'm not going to try. Said a tall monkey.",
                    "words": [{"word": " monkey.", "start": 2.5, "end": 2.8}],
                }
            ],
        }
    }
    revised, audit = apply(asr, [patch])
    assert audit[0]["accepted"]
    assert "tall monkey" in revised["segments"][0]["text"]
    assert revised["segments"][1]["text"] == "Me neither."


def test_page_translation_does_not_show_next_dialogue_or_sentence():
    from mlvideo.caption_tracking import page_translation

    parts = [
        {
            "id": "u0-p00",
            "speaker": "a",
            "source_text": "I wonder what's behind that waterfall?",
            "translation": "真想知道瀑布后面有什么。",
        },
        {
            "id": "u0-p01",
            "speaker": "narrator",
            "source_text": "said one monkey.",
            "translation": "一只猴子说道。",
        },
        {
            "id": "u0-p02",
            "speaker": "b",
            "source_text": "Me too!",
            "translation": "我也想知道！",
        },
        {
            "id": "u0-p03",
            "speaker": "narrator",
            "source_text": "said a second monkey.",
            "translation": "第二只猴子说道。",
        },
        {
            "id": "u1-p00",
            "speaker": "narrator",
            "source_text": "But nobody could jump through it.",
            "translation": "可是，谁也跳不过去。",
        },
    ]
    group = {"parts": parts}
    assert (
        page_translation(
            group, "I wonder what's behind that waterfall? said one monkey."
        )
        == "真想知道瀑布后面有什么。一只猴子说道。"
    )
    assert (
        page_translation(group, "Me too! said a second monkey.")
        == "我也想知道！第二只猴子说道。"
    )
    assert (
        page_translation(group, "But nobody could jump through it.")
        == "可是，谁也跳不过去。"
    )


def test_ocr_hints_cannot_silently_drop_negation():
    original = {
        "id": 0,
        "start": 0.0,
        "end": 3.0,
        "text": "We will not jump into the waterfall.",
        "words": [],
    }
    new = "We will jump into the waterfall."
    patch = {
        "index": 0,
        "start": 0.0,
        "end": 3.0,
        "original_text": original["text"],
        "ocr_text": new,
        "old_ocr_coverage": 0.5,
        "result": {"text": new, "segments": [original | {"text": new}]},
    }
    revised, audit = apply({"segments": [original]}, [patch])
    assert not audit[0]["accepted"]
    assert revised["segments"][0]["text"] == original["text"]
