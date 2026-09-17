"""Bind OCR subtitle pages to script groups and refine appearance at native frames."""

import re
from difflib import SequenceMatcher
from itertools import pairwise
from pathlib import Path

from .util import atomic_json


def tokens(text):
    return re.findall(r"[a-z0-9]+", text.lower())


def reading_order(lines):
    rows = []
    for line in sorted(lines, key=lambda x: x["box"][1]):
        y0, y1 = line["box"][1], line["box"][3]
        row = next(
            (
                r
                for r in rows
                if any(
                    min(y1, x["box"][3]) - max(y0, x["box"][1])
                    > 0.5 * min(y1 - y0, x["box"][3] - x["box"][1])
                    for x in r
                )
            ),
            None,
        )
        if row is None:
            rows.append([line])
        else:
            row.append(line)
    return [line for row in rows for line in sorted(row, key=lambda x: x["box"][0])]


def page_translation(group, english):
    # Audio may safely join several sentences. Display only the sentence(s)
    # represented by this actual English page, retaining dialogue + attribution.
    if "parts" not in group:
        return group["text"]
    chunks = []
    for part in group["parts"]:
        unit = part["id"].rsplit("-p", 1)[0]
        new = not chunks or chunks[-1]["unit"] != unit
        if not new and part["speaker"] != "narrator":
            roles = {
                p["speaker"] for p in chunks[-1]["parts"] if p["speaker"] != "narrator"
            }
            new = bool(roles and part["speaker"] not in roles)
        if new:
            chunks.append({"unit": unit, "parts": []})
        chunks[-1]["parts"].append(part)
    actual = "".join(tokens(english))
    selected = []
    for chunk in chunks:
        expected = "".join(tokens(" ".join(p["source_text"] for p in chunk["parts"])))
        matched = sum(
            b.size
            for b in SequenceMatcher(None, actual, expected).get_matching_blocks()
        )
        if matched / max(1, min(len(actual), len(expected))) >= 0.9:
            selected.extend(chunk["parts"])
    if not selected:
        raise ValueError(
            "English page lacks a corresponding translated sentence; review required"
        )
    return "".join(p["translation"] for p in selected)


def match_pages(scan, groups):
    from collections import Counter

    counts = Counter(
        "".join(tokens(l["text"]))
        for r in scan["rows"]
        for l in r["lines"]
        if l["box"][1] < scan["height"] * 0.35
        and l["box"][3] - l["box"][1] < scan["height"] * 0.08
    )
    marks = [
        key
        for key, count in counts.items()
        if count >= max(10, len(scan["rows"]) * 0.3)
    ]
    candidates = []
    for row in scan["rows"]:
        row = dict(row)
        row["lines"] = [
            l
            for l in row["lines"]
            if not (
                l["box"][1] < scan["height"] * 0.35
                and l["box"][3] - l["box"][1] < scan["height"] * 0.08
                and any(
                    SequenceMatcher(None, "".join(tokens(l["text"])), m).ratio() > 0.7
                    for m in marks
                )
            )
        ]
        sample = round(row["seconds"] * 48000)
        best = None
        for index, g in enumerate(groups):
            distance = (
                max(g["start_sample"] - sample, sample - g["end_sample"], 0) / 48000
            )
            if distance > 3:
                continue
            expected = tokens(g["source_text"])
            expected_chars = "".join(expected)
            lines = []
            for line in row["lines"]:
                actual_chars = "".join(tokens(line["text"]))
                match = sum(
                    b.size
                    for b in SequenceMatcher(
                        None, actual_chars, expected_chars
                    ).get_matching_blocks()
                )
                if (
                    actual_chars
                    and match >= min(4, len(actual_chars), len(expected_chars))
                    and match / max(1, min(len(actual_chars), len(expected_chars)))
                    >= 0.6
                ):
                    lines.append(line)
            if not lines:
                continue
            lines = reading_order(lines)
            # Include every adjacent English line of this actual on-screen block,
            # even when only one line matches the currently spoken clause.
            for extra in row["lines"]:
                if extra in lines or not tokens(extra["text"]):
                    continue
                x0, y0, x1, y1 = extra["box"]
                if any(
                    max(0, min(x1, l["box"][2]) - max(x0, l["box"][0])) > 0
                    and min(abs(y0 - l["box"][3]), abs(l["box"][1] - y1))
                    < 2 * max(y1 - y0, l["box"][3] - l["box"][1])
                    for l in lines
                ):
                    lines.append(extra)
            lines = reading_order(lines)
            recognized = " ".join(l["text"] for l in lines)
            actual = tokens(recognized)
            actual_chars = "".join(actual)
            matched = sum(
                b.size
                for b in SequenceMatcher(
                    None, actual_chars, expected_chars
                ).get_matching_blocks()
            )
            precision = matched / max(1, len(actual_chars))
            recall = matched / max(1, len(expected_chars))
            if max(precision, recall) < 0.6 or matched < min(4, len(actual_chars)):
                continue
            score = precision + recall - distance * 0.15
            if best is None or score > best[0]:
                best = (score, index, lines, recognized)
        if best is None:
            continue
        _, index, lines, recognized = best
        box = [
            min(l["box"][0] for l in lines),
            min(l["box"][1] for l in lines),
            max(l["box"][2] for l in lines),
            max(l["box"][3] for l in lines),
        ]
        key = " ".join(tokens(recognized))
        previous = next(
            (
                c
                for c in reversed(candidates)
                if c["group"] == index
                and SequenceMatcher(None, c["key"], key).ratio() >= 0.94
                and max(abs(a - b) for a, b in zip(c["english_box"], box)) < 24
            ),
            None,
        )
        if previous:
            previous["observed_frames"].append(row["frame"])
            # Keep a single measured page box; never union unrelated pages.
        else:
            candidates.append(
                {
                    "group": index,
                    "key": key,
                    "source_text": recognized,
                    "english_box": box,
                    "observed_frames": [row["frame"]],
                    "reference_frame": row["frame"],
                    "evidence": row["evidence"],
                    "text": page_translation(groups[index], recognized),
                }
            )
    covered = {x["group"] for x in candidates}
    missing = [g["id"] for i, g in enumerate(groups) if i not in covered]
    if missing:
        raise ValueError(
            f"No reliable source English subtitle page for units: {missing}"
        )
    return sorted(candidates, key=lambda c: c["reference_frame"])


def merge_shared_pages(scan, groups):
    candidates = match_pages(scan, groups)
    edges = set()
    for a, b in pairwise(candidates):
        if (
            b["group"] == a["group"] + 1
            and SequenceMatcher(None, a["key"], b["key"]).ratio() >= 0.94
        ):
            edges.add(b["group"])
    merged = []
    for i, g in enumerate(groups):
        if i in edges:
            prev = merged[-1]
            prev["parts"] = prev["parts"] + g["parts"]
            prev["text"] += g["text"]
            prev["source_text"] += " " + g["source_text"]
            for key in ("end_frame", "end_sample", "speech_end_sample", "hold_frame"):
                prev[key] = g[key]
        else:
            merged.append(dict(g))
    return merged


def track(source, scan, groups, directory):
    import av
    import numpy as np
    from PIL import Image, ImageFilter

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    candidates = match_pages(scan, groups)
    atomic_json(directory / "candidates.json", candidates)
    # Templates are decoded original pixels, not JPEG OCR pixels.
    by_frame = {}
    for c in candidates:
        by_frame.setdefault(c["reference_frame"], []).append(c)
    with av.open(str(source)) as container:
        for index, frame in enumerate(container.decode(video=0)):
            if index not in by_frame:
                continue
            image = frame.to_image().convert("L")
            for c in by_frame[index]:
                box = c["english_box"]
                crop = image.crop(box)
                data = np.asarray(crop)
                dark = data < 70
                light = data > 190
                left = np.zeros_like(dark)
                right = np.zeros_like(dark)
                up = np.zeros_like(dark)
                down = np.zeros_like(dark)
                for n in range(1, 10):
                    left[:, n:] |= dark[:, :-n]
                    right[:, :-n] |= dark[:, n:]
                    up[n:, :] |= dark[:-n, :]
                    down[:-n, :] |= dark[n:, :]
                glyph_light = light & ((left & right) | (up & down))
                near_light = (
                    np.asarray(
                        Image.fromarray(glyph_light.astype("uint8") * 255).filter(
                            ImageFilter.MaxFilter(7)
                        )
                    )
                    > 0
                )
                c["_light"] = glyph_light
                c["_dark"] = dark & near_light
                if min(c["_light"].sum(), c["_dark"].sum()) < 20:
                    crop.save(directory / f"untrackable-{index}.png")
                    raise ValueError(
                        f"Subtitle template lacks contrasting glyph evidence at frame {index}: {c['source_text']} counts={int(c['_light'].sum())}/{int(c['_dark'].sum())}"
                    )
    records = []
    with av.open(str(source)) as container:
        for index, frame in enumerate(container.decode(video=0)):
            picture = None
            matches = []
            for k, c in enumerate(candidates):
                lo = candidates[k - 1]["reference_frame"] if k else 0
                hi = (
                    candidates[k + 1]["reference_frame"]
                    if k + 1 < len(candidates)
                    else groups[-1]["end_frame"]
                )
                if not lo <= index <= hi:
                    continue
                if picture is None:
                    picture = frame.to_image().convert("L")
                data = np.asarray(picture.crop(c["english_box"]))
                score = min(
                    float((data[c["_light"]] > 165).mean()),
                    float((data[c["_dark"]] < 100).mean()),
                )
                if score >= 0.88:
                    matches.append((score, k))
            records.append(max(matches)[1] if matches else None)
    pages = []
    start = 0
    for end in range(1, len(records) + 1):
        if end < len(records) and records[end] == records[start]:
            continue
        key = records[start]
        if key is not None and end - start >= 2:
            c = candidates[key]
            pages.append(
                {
                    k: v
                    for k, v in c.items()
                    if not k.startswith("_") and k not in ("key", "observed_frames")
                }
                | {"start_frame": start, "end_frame": end}
            )
        start = end
    # Every OCR observation should be within the measured visible interval.
    missed = []
    for c in candidates:
        for frame in c["observed_frames"]:
            if not any(
                p["group"] == c["group"] and p["start_frame"] <= frame < p["end_frame"]
                for p in pages
            ):
                missed.append(frame)
    if missed:
        atomic_json(directory / "missed-observations.json", missed)
        raise ValueError(
            f"Frame-level subtitle tracking missed {len(missed)} OCR observations; review required"
        )
    if any(not any(p["group"] == i for p in pages) for i in range(len(groups))):
        raise ValueError("Subtitle page coverage missing after refinement")
    atomic_json(directory / "pages.json", pages)
    atomic_json(
        directory / "tracking.json",
        {
            "source_frames": len(records),
            "pages": len(pages),
            "method": "OCR-word match plus source-pixel contrasting glyph templates",
            "quality_status": "REVIEW",
        },
    )
    return pages
