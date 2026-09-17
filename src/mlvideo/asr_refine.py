"""Find subtitle-supported ASR omissions without replacing audio evidence with OCR."""

import re
from difflib import SequenceMatcher


def normalized(text):
    return "".join(re.findall("[a-z0-9]+", text.lower()))


def coverage(text, context):
    a, b = normalized(text), normalized(context)
    if not a:
        return 0.0
    return sum(x.size for x in SequenceMatcher(None, a, b).get_matching_blocks()) / len(
        a
    )


def candidates(asr, scan):
    segments = asr["segments"]
    requests = {}
    for row in scan["rows"]:
        lines = [
            l
            for l in row["lines"]
            if l["box"][1] >= scan["height"] * 0.5 and normalized(l["text"])
        ]
        if not lines:
            continue
        text = " ".join(
            l["text"] for l in sorted(lines, key=lambda l: (l["box"][1], l["box"][0]))
        )
        time = row["seconds"]
        near = [
            (i, s)
            for i, s in enumerate(segments)
            if s["start"] - 4 <= time <= s["end"] + 4
        ]
        if not near:
            continue
        context = " ".join(s["text"] for _, s in near)
        score = coverage(text, context)
        if score >= 0.88 or score < 0.35:
            continue
        index, segment = max(
            near,
            key=lambda pair: (
                coverage(pair[1]["text"], text)
                - 0.15 * max(pair[1]["start"] - time, time - pair[1]["end"], 0)
            ),
        )
        if coverage(segment["text"], text) < 0.75:
            continue
        end_index = index
        if (
            index + 1 < len(segments)
            and re.match(
                r"(?i)^\s*(said|asked|ask|cried|replied)\b", segments[index + 1]["text"]
            )
            and re.search(r"(?i)(said|asked|cried|replied)", text)
        ):
            end_index = index + 1
        start = max(
            0, (segments[index - 1]["end"] + segment["start"]) / 2 if index else 0
        )
        end = (
            (segments[end_index]["end"] + segments[end_index + 1]["start"]) / 2
            if end_index + 1 < len(segments)
            else segments[end_index]["end"] + 0.5
        )
        item = {
            "index": index,
            "end_index": end_index,
            "start": start,
            "end": end,
            "ocr_text": text,
            "original_text": " ".join(
                x["text"] for x in segments[index : end_index + 1]
            ),
            "old_ocr_coverage": score,
            "evidence": row["evidence"],
        }
        if index not in requests or score < requests[index]["old_ocr_coverage"]:
            requests[index] = item
    return list(requests.values())


def protected_content(text):
    negations = re.findall(
        r"\b(?:no|not|never|nobody|none|neither|without|cannot)\b|n't", text.lower()
    )
    numbers = re.findall(r"\d+(?:\.\d+)?", text)
    return len(negations), sorted(numbers)


def apply(asr, patches):
    revised = []
    audit = []
    skip_until = -1
    for index, original in enumerate(asr["segments"]):
        if index <= skip_until:
            continue
        patch = next((p for p in patches if p["index"] == index), None)
        if patch is None:
            revised.append(dict(original))
            continue
        result = patch["result"]
        new = result["text"]
        improved = (
            coverage(patch["ocr_text"], new) > patch["old_ocr_coverage"] + 0.05
            and coverage(patch["original_text"], new) >= 0.8
            and protected_content(patch["original_text"]) == protected_content(new)
        )
        if improved:
            skip_until = patch.get("end_index", index)
            for segment in result["segments"]:
                row = dict(segment)
                row["start"] += patch["start"]
                row["end"] += patch["start"]
                row["words"] = [
                    w
                    | {
                        "start": w["start"] + patch["start"],
                        "end": w["end"] + patch["start"],
                    }
                    for w in row["words"]
                ]
                if (
                    not patch["start"]
                    <= row["start"]
                    < row["end"]
                    <= patch["end"] + 0.05
                ):
                    raise ValueError("Refined ASR outside source window")
                revised.append(row)
        else:
            revised.append(dict(original))
        audit.append(
            {k: v for k, v in patch.items() if k != "result"}
            | {"new_text": new, "accepted": improved, "quality_status": "REVIEW"}
        )
    for index, row in enumerate(revised):
        row["id"] = index
    return asr | {
        "segments": revised,
        "text": " ".join(x["text"] for x in revised),
    }, audit
