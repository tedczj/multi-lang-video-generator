import pytest

from mlvideo.speaker_bank import collect_candidates, select_reference


def segment(identity, start, end, text="A complete sentence."):
    return {
        "id": identity,
        "start_sample": round(start * 48000),
        "end_sample": round(end * 48000),
        "text": text,
    }


def turn(speaker, start, end):
    return {
        "speaker_id": speaker,
        "start_sample": round(start * 48000),
        "end_sample": round(end * 48000),
    }


def test_bank_collects_contiguous_same_speaker_but_not_others_in_gap():
    speech = {"segments": [segment("a", 0, 4), segment("b", 4.5, 9)]}
    track = {"turns": [turn("one", 0, 4), turn("one", 4.5, 9)]}
    candidates, _ = collect_candidates(speech, track)
    assert len(candidates) == 1 and candidates[0]["end_sample"] == 9 * 48000
    track["turns"].append(turn("other", 4.1, 4.4))
    assert collect_candidates(speech, track)[0] == []


def test_bank_rejects_overlap_and_uncovered_asr():
    speech = {"segments": [segment("a", 0, 10)]}
    track = {"turns": [turn("one", 0, 10), turn("other", 3, 4)]}
    candidates, discarded = collect_candidates(speech, track)
    assert candidates == [] and discarded[0]["speech_id"] == "a"
    assert collect_candidates(speech, {"turns": [turn("one", 0, 2)]})[0] == []


def test_bank_does_not_clone_from_an_insufficient_reference():
    bank = {
        "speakers": [
            {"speaker_id": "one", "selected_candidate_id": None, "candidates": []}
        ]
    }
    with pytest.raises(ValueError, match="do not clone"):
        select_reference(bank, "one")
    with pytest.raises(ValueError, match="not uniquely"):
        select_reference(bank, "missing")


def test_bank_selects_only_the_requested_speaker_candidate():
    bank = {
        "speakers": [
            {
                "speaker_id": "one",
                "selected_candidate_id": "r1",
                "candidates": [
                    {
                        "candidate_id": "r1",
                        "eligible": True,
                        "start_sample": 0,
                        "end_sample": 480000,
                        "text": "Exact reference text.",
                    }
                ],
            }
        ]
    }
    result = select_reference(bank, "one")
    assert result["candidate_id"] == "r1" and result["text"] == "Exact reference text."
    with pytest.raises(ValueError, match="do not clone"):
        select_reference(bank, "one", "another_speaker_reference")


def test_pooled_reference_keeps_source_intervals_and_explicit_gaps(tmp_path):
    import array
    import wave

    from mlvideo.speaker_bank import write_reference

    source = tmp_path / "source.wav"
    with wave.open(str(source), "wb") as w:
        w.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
        w.writeframes(
            array.array("h", [100, -100, 200, -200, 300, -300, 400, -400]).tobytes()
        )
    segments = [
        {"start_sample": 0, "end_sample": 1, "text": "First."},
        {"start_sample": 2, "end_sample": 4, "text": "Second."},
    ]
    with wave.open(str(source)) as w:
        write_reference(w, segments, 1, tmp_path / "pooled.wav")
    with wave.open(str(tmp_path / "pooled.wav")) as w:
        assert w.getnframes() == 4
        assert array.array("h", w.readframes(4)).tolist() == [
            100,
            -100,
            0,
            0,
            300,
            -300,
            400,
            -400,
        ]


def test_pooled_metadata_cannot_hide_missing_or_extra_audio_frames():
    from workers.cosyvoice_worker import reference_frames

    reference = {
        "segments": [
            {"start_sample": 10, "end_sample": 20},
            {"start_sample": 40, "end_sample": 60},
        ],
        "gap_samples": 5,
        "frames": 35,
    }
    assert reference_frames(reference) == 35
    reference["frames"] = 30
    with pytest.raises(ValueError, match="frame count"):
        reference_frames(reference)
