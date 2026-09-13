"""Human speaker/reference decisions bound to immutable analysis artifacts."""

from copy import deepcopy

from .speech import apply_annotation


def require_checks(decision, expected):
    if not isinstance(decision, dict) or not isinstance(decision.get("reviewer"), str):
        raise ValueError("A named human reviewer is required")  # noqa: TRY004 - invalid decision, not an API type contract
    if not decision["reviewer"].strip():
        raise ValueError("A named human reviewer is required")
    checks = decision.get("checks", [])
    if (
        not isinstance(checks, list)
        or any(not isinstance(c, dict) for c in checks)
        or len(checks) != len(expected)
        or {c.get("id") for c in checks} != set(expected)
        or any(
            c.get("status") != "PASS"
            or not isinstance(c.get("reason"), str)
            or not c["reason"].strip()
            for c in checks
        )
    ):
        raise ValueError(
            "Human review must pass every required check exactly once with reasons"
        )


def reviewed_speech(speech, track, vad, refs, count, decision):
    require_checks(decision, {"speaker_identity", "speech_boundaries", "transcript"})
    for key in ("audio", "speech", "speakers", "vad"):
        if decision.get(key + "_sha512") != refs[key]["sha512"]:
            raise ValueError("Speaker review does not bind the exact analysis inputs")
    audio_id = refs["audio"]["artifact_id"]
    if (
        speech["audio_artifact_id"] != audio_id
        or track["source_audio_artifact_id"] != audio_id
    ):
        raise ValueError("Speaker analysis belongs to another source audio")
    if vad["sample_rate"] != 16000:
        raise ValueError("Expected original 16 kHz VAD evidence")
    if any(
        not 0 <= s["start_sample"] < s["end_sample"] <= count
        for s in speech["segments"] + track["turns"]
    ):
        raise ValueError("Speaker/ASR detections exceed the source audio")
    result = deepcopy(speech)
    # Include diarization-only detections in coverage validation, too.
    detected = result["segments"] + track["turns"]
    annotation = {
        "audio_sha512": refs["audio"]["sha512"],
        "reviewer": decision["reviewer"],
        "segments": decision["segments"],
    }
    protected, coverage, _ = apply_annotation(
        annotation, refs["audio"], count, detected, vad["intervals"]
    )
    result.update(
        segments=detected,
        protected_intervals=protected,
        coverage_status=coverage,
        warnings=[
            "Human-reviewed speaker/boundary annotation; original ASR, VAD and clustering retained upstream"
        ],
    )
    reviewed_track = deepcopy(track)
    reviewed_track["turns"] = [
        {k: row[k] for k in ("start_sample", "end_sample", "speaker_id")}
        for row in decision["segments"]
    ]
    reviewed_track["estimated_speaker_count"] = len(
        {r["speaker_id"] for r in reviewed_track["turns"]}
    )
    reviewed_track["unknown_reason"] = (
        "Human-reviewed grouping; no calibrated model confidence; reference and clone listening remain separate"
    )
    return result, reviewed_track


def review_reference(candidate, bank_ref, decision):
    require_checks(
        decision,
        {"speaker_identity", "transcript", "clean_reference", "complete_words"},
    )
    if (
        decision.get("bank_sha512") != bank_ref["sha512"]
        or decision.get("candidate_id") != candidate["candidate_id"]
        or decision.get("audio_sha512") != candidate["audio_sha512"]
        or decision.get("speaker_id") != candidate["speaker_id"]
    ):
        raise ValueError(
            "Reference review does not bind this exact speaker/candidate/bank"
        )


def bank_readiness(bank):
    """Report every group; never treat a technically eligible clip as voice approval."""
    rows = []
    for speaker in bank["speakers"]:
        eligible = [c for c in speaker["candidates"] if c["eligible"]]
        rows.append(
            {
                "speaker_id": speaker["speaker_id"],
                "eligible_candidates": [c["candidate_id"] for c in eligible],
                "status": "REVIEW" if eligible else "INSUFFICIENT_REFERENCE",
            }
        )
    return {
        "status": "REVIEW"
        if rows and all(r["eligible_candidates"] for r in rows)
        else "INCOMPLETE",
        "speakers": rows,
        "missing_reference_speakers": [
            r["speaker_id"] for r in rows if not r["eligible_candidates"]
        ],
        "required_reviews": [
            "speaker_grouping",
            "reference_transcript_and_purity",
            "generated_voice_identity_naturalness_and_tail",
        ],
    }
