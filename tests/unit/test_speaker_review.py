from copy import deepcopy

import pytest

from mlvideo.speaker_review import bank_readiness, review_reference, reviewed_speech


def evidence():
    refs = {
        k: {"artifact_id": k, "sha512": k * 128}
        for k in ("audio", "speech", "speakers", "vad")
    }
    speech = {
        "audio_artifact_id": "audio",
        "segments": [
            {
                "id": "s1",
                "start_sample": 0,
                "end_sample": 48000,
                "text": "Hello.",
                "speaker_id": None,
            }
        ],
        "words": [],
    }
    track = {
        "source_audio_artifact_id": "audio",
        "turns": [{"start_sample": 0, "end_sample": 96000, "speaker_id": "estimate"}],
        "estimated_speaker_count": 1,
        "quality_status": "REVIEW",
        "confidence": None,
    }
    vad = {"sample_rate": 16000, "intervals": [{"start": 0, "end": 16000}]}
    decision = {k + "_sha512": r["sha512"] for k, r in refs.items()}
    decision.update(
        reviewer="test reviewer",
        checks=[
            {"id": k, "status": "PASS", "reason": "Synthetic test only"}
            for k in ("speaker_identity", "speech_boundaries", "transcript")
        ],
        segments=[
            {
                "start_sample": 0,
                "end_sample": 96000,
                "text": "Hello there.",
                "speaker_id": "one",
            }
        ],
    )
    return speech, track, vad, refs, 96000, decision


def test_review_preserves_original_estimates_and_applies_complete_annotation():
    args = evidence()
    before = deepcopy(args)
    speech, track = reviewed_speech(*args)
    assert args == before
    assert speech["coverage_status"] == "PASS"
    assert speech["segments"][0]["speaker_id"] == "one"
    assert speech["segments"][0]["text"] == "Hello there."
    assert track["turns"][0]["speaker_id"] == "one"
    assert track["quality_status"] == "REVIEW"


@pytest.mark.parametrize("field", ["audio", "speech", "speakers", "vad"])
def test_review_rejects_stale_analysis(field):
    args = evidence()
    args[-1][field + "_sha512"] = "changed"
    with pytest.raises(ValueError, match="exact analysis"):
        reviewed_speech(*args)


def test_review_cannot_drop_diarization_only_speech():
    args = evidence()
    args[-1]["segments"][0]["end_sample"] = 48000
    with pytest.raises(ValueError, match="unprotected"):
        reviewed_speech(*args)


@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate", "review", "empty_reason", "anonymous"]
)
def test_review_requires_complete_named_human_decision(mutation):
    args = evidence()
    d = args[-1]
    if mutation == "missing":
        d["checks"].pop()
    elif mutation == "duplicate":
        d["checks"][1] = d["checks"][0]
    elif mutation == "review":
        d["checks"][0]["status"] = "REVIEW"
    elif mutation == "empty_reason":
        d["checks"][0]["reason"] = ""
    else:
        d["reviewer"] = " "
    with pytest.raises(ValueError):
        reviewed_speech(*args)


def test_reference_review_binds_voice_and_exact_candidate_bytes():
    candidate = {"candidate_id": "c1", "speaker_id": "one", "audio_sha512": "a" * 128}
    bank = {"sha512": "b" * 128}
    decision = dict(
        candidate,
        bank_sha512=bank["sha512"],
        reviewer="test reviewer",
        checks=[
            {"id": k, "status": "PASS", "reason": "Synthetic test only"}
            for k in (
                "speaker_identity",
                "transcript",
                "clean_reference",
                "complete_words",
            )
        ],
    )
    review_reference(candidate, bank, decision)
    for key in ("candidate_id", "speaker_id", "audio_sha512", "bank_sha512"):
        with pytest.raises(ValueError, match="exact speaker"):
            review_reference(candidate, bank, decision | {key: "changed"})


def test_bank_readiness_keeps_all_missing_speakers_visible():
    report = bank_readiness(
        {
            "speakers": [
                {
                    "speaker_id": "one",
                    "candidates": [{"candidate_id": "r1", "eligible": True}],
                },
                {"speaker_id": "two", "candidates": []},
            ]
        }
    )
    assert report["status"] == "INCOMPLETE"
    assert report["missing_reference_speakers"] == ["two"]
    assert bank_readiness({"speakers": []})["status"] == "INCOMPLETE"


def test_voice_acceptance_covers_each_clip_and_cannot_omit_one():
    from mlvideo.subtitles import review

    render = {"artifact_id": "render", "sha512": "a" * 128}
    args = (render, {"checks": []}, [{"overall": "REVIEW"}] * 2, {"events": [{}]})
    _, pending = review(*args)
    ids = {c["id"] for c in pending["checks"]}
    for i in range(2):
        for key in (
            "voice_identity",
            "naturalness",
            "leading_noise",
            "tail_integrity",
            "spoken_content",
        ):
            assert f"{key}_{i}" in ids
    human = pending | {
        "reviewer": "synthetic test only",
        "decision": "PASS",
        "checks": [
            c | {"status": "PASS"}
            for c in pending["checks"]
            if c["id"] != "tail_integrity_1"
        ],
    }
    with pytest.raises(ValueError, match="each unresolved"):
        review(*args, human)


@pytest.mark.parametrize("channels", [1, 2])
def test_reviewed_speech_worker_outputs_validate(tmp_path, channels):
    import wave

    from mlvideo.contracts import validate_output
    from mlvideo.phase2 import run
    from mlvideo.util import atomic_json, read_json, sha512

    speech, track, vad, refs, count, decision = evidence()
    speech.update(
        sample_rate=48000,
        protected_intervals=[{"start_sample": 0, "end_sample": count}],
        coverage_status="REVIEW",
        warnings=[],
    )
    track.update(sample_rate=48000, unknown_reason="Synthetic estimates")
    paths = {}
    for key, value in (("speech", speech), ("speakers", track), ("vad", vad)):
        paths[key] = tmp_path / (key + ".json")
        atomic_json(paths[key], value)
    paths["audio"] = tmp_path / "audio.wav"
    with wave.open(str(paths["audio"]), "wb") as audio:
        audio.setparams((channels, 2, 48000, 0, "NONE", "not compressed"))
        audio.writeframes(bytes(count * channels * 2))
    for key, ref in refs.items():
        ref.update(
            path=str(paths[key]),
            sha512=sha512(paths[key]),
            schema_id={
                "audio": "Audio.v1",
                "speech": "SpeechTrack.v1",
                "speakers": "SpeakerTrack.v1",
                "vad": "Binary.v1",
            }[key],
        )
        decision[key + "_sha512"] = ref["sha512"]
    work = tmp_path / "work"
    work.mkdir()
    request = {
        "node": "N08",
        "strategy_id": "reviewed_speech",
        "inputs": {k: [r] for k, r in refs.items()},
        "params": {"decision": decision},
    }
    outputs = []
    if channels != 2:
        with pytest.raises(ValueError, match="48 kHz stereo PCM16"):
            run(request, work, lambda *args: None)
        return
    run(
        request,
        work,
        lambda port, path, schema, kind: outputs.append(
            {"port": port, "path": path, "schema_id": schema}
        ),
    )
    assert {o["port"] for o in outputs} == {"speech", "speakers", "review"}
    for output in outputs:
        validate_output(work, output)
    assert read_json(work / "speech.json")["coverage_status"] == "PASS"
    assert read_json(work / "review.json") == decision
