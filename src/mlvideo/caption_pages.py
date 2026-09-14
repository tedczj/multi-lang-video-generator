"""Preview units grounded in source caption pages, with ASR/VAD cut protection."""

from .timeline import cut_frame, frame_sample
from .util import digest


def build_pages(speech, captions, vad, canonical, refs, pages):
    total = canonical["source_frames"]
    frames = {}
    for cue in captions["cues"]:
        frames.setdefault(cue["frame"], []).append(cue)
    voiced = [
        {
            "start_sample": round(v["start"] * 48000 / vad["sample_rate"]),
            "end_sample": round(v["end"] * 48000 / vad["sample_rate"]),
        }
        for v in vad["intervals"]
    ]
    units = []
    previous = 0
    for page in pages:
        start, end = page["start_frame"], page["end_frame"]
        if (
            type(start) is not int
            or type(end) is not int
            or not previous <= start < end <= total
        ):
            raise ValueError("Caption pages overlap, reorder or exceed the video")
        a, b = frame_sample(canonical, start), frame_sample(canonical, end)
        matched = []
        boxes = []
        for frame, cues in frames.items():
            if start <= frame < end:
                ordered = sorted(
                    cues, key=lambda c: (c["boxes"][0][1], c["boxes"][0][0])
                )
                text = " ".join(c["text"] for c in ordered)
                if "".join(text.split()) == "".join(page["text"].split()):
                    matched.extend(c["id"] for c in ordered)
                    boxes.extend(box for c in ordered for box in c["boxes"])
        if not matched:
            raise ValueError("Page text is not corroborated by source-frame OCR")
        words = [
            w for w in speech["words"] if w["end_sample"] > a and w["start_sample"] < b
        ]
        voice = [v for v in voiced if v["start_sample"] < b and v["end_sample"] > a]
        if not words or not voice:
            raise ValueError("Caption page lacks aligned speech/VAD evidence")
        first = min(
            [max(a, w["start_sample"]) for w in words]
            + [v["start_sample"] for v in voice]
        )
        last = max(w["end_sample"] for w in words + voice)
        if first < a or last > b:
            raise ValueError("Speech crosses caption page boundary: regroup/review")
        units.append(
            {
                "unit_id": "unit_" + digest([refs["captions"], page])[:24],
                "start_sample": first,
                "end_sample": last,
                "safe_cut_frame": end,
                "text": page["text"],
                "speaker_id": None,
                "speech_ids": [
                    s["id"]
                    for s in speech["segments"]
                    if s["start_sample"] < last and s["end_sample"] > first
                ],
                "caption_ids": matched,
                "caption_start_frame": start,
                "caption_end_frame": end,
                "source_subtitle_box": [
                    int(min(x[0] for x in boxes)),
                    int(min(x[1] for x in boxes)),
                    int(max(x[2] for x in boxes)),
                    int(max(x[3] for x in boxes)),
                ],
                "merge_reason": "Source-frame OCR with visually checked page boundaries; ASR/VAD estimates protect speech; REVIEW",
            }
        )
        previous = end
    if any(
        not any(
            u["start_sample"] <= v["start_sample"]
            and u["end_sample"] >= v["end_sample"]
            for u in units
        )
        for v in voiced
    ):
        raise ValueError("VAD found speech outside the selected caption pages")
    for i, u in enumerate(units):
        next_frame = (
            cut_frame(canonical, units[i + 1]["start_sample"])
            if i + 1 < len(units)
            else total
        )
        u["safe_cut_frame"] = min(u["caption_end_frame"], next_frame)
        if u["safe_cut_frame"] < 0 or u["end_sample"] > frame_sample(canonical, u["safe_cut_frame"]):
            raise ValueError(
                "No safe integral cut before the source caption disappears"
            )
    return {
        "speech_artifact_id": refs["speech"],
        "caption_artifact_id": refs["captions"],
        "canonical_artifact_id": refs["canonical"],
        "items": units,
        "quality_status": "REVIEW",
    }
