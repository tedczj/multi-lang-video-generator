"""Optional explicit speaker/boundary annotations, bound to exact source PCM."""

from .util import digest


def apply_annotation(annotation, ref, count, segments, vad):
    if annotation is None:
        return [{"start_sample": 0, "end_sample": count}], "REVIEW", None
    if (
        annotation.get("audio_sha512") != ref["sha512"]
        or not annotation.get("reviewer", "").strip()
    ):
        raise ValueError("Speech annotation needs exact PCM hash and reviewer")
    rows = annotation["segments"]
    if not rows:
        raise ValueError("Empty human speech annotation")
    previous = 0
    for row in rows:
        a, b = row["start_sample"], row["end_sample"]
        if (
            type(a) is not int
            or type(b) is not int
            or not previous <= a < b <= count
            or not row["speaker_id"].strip()
            or not row["text"].strip()
        ):
            raise ValueError("Invalid or overlapping annotated speech: REVIEW")
        previous = b
    # Fail closed if ASR or independent VAD finds speech outside human protection.
    detected = [(s["start_sample"], s["end_sample"]) for s in segments]
    detected.extend((v["start"] * 3, v["end"] * 3) for v in vad)
    for start, end in detected:
        cursor = start
        for row in rows:
            if row["start_sample"] <= cursor < row["end_sample"]:
                cursor = max(cursor, row["end_sample"])
        if cursor < end:
            raise ValueError("Annotation leaves detected speech unprotected: REVIEW")
    segments[:] = [
        {"id": "speech_" + digest([ref["artifact_id"], i, row])[:20], **row}
        for i, row in enumerate(rows)
    ]
    return (
        [
            {"start_sample": r["start_sample"], "end_sample": r["end_sample"]}
            for r in rows
        ],
        "PASS",
        annotation,
    )
