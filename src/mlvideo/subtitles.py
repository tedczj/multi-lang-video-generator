"""Measured bilingual raster subtitles, real dub rendering, fail-closed review."""

import io
import wave
import zipfile
from pathlib import Path

from . import media
from .contracts import validate
from .util import atomic_json, read_json, sha512


def wrap(text, font, max_width):
    lines, line = [], ""
    for char in text:
        if char == "\n":
            if line:
                lines.append(line)
            line = ""
        elif font.getlength(line + char) <= max_width:
            line += char
        else:
            if not line or font.getlength(char) > max_width:
                raise ValueError("Glyph wider than subtitle area")
            lines.append(line.rstrip())
            line = char.lstrip()
    if line:
        lines.append(line.rstrip())
    if not lines:
        raise ValueError("Empty subtitle text")
    return lines


def caption_output_sample(timeline, source_frame):
    from fractions import Fraction

    from .timeline import quantize

    if source_frame == timeline["source_frames"]:
        return timeline["output_samples"]
    for piece in timeline["pieces"]:
        if (
            piece["kind"] == "source"
            and piece["source_start_frame"] <= source_frame < piece["source_end_frame"]
        ):
            frame = (
                piece["output_start_frame"] + source_frame - piece["source_start_frame"]
            )
            if "frame_samples" in timeline:
                return timeline["frame_samples"][frame]
            return quantize(Fraction(frame) / Fraction(timeline["fps"]))
    raise ValueError("Source caption frame has no output mapping")


def subtitle_display_end(timeline, index, preserve_source_english):
    dub = timeline["dubs"][index]
    if not preserve_source_english:
        return dub["start_sample"] + dub["samples"]
    unit = timeline["utterances"][index]
    if "caption_end_frame" in unit:
        return caption_output_sample(timeline, unit["caption_end_frame"])
    # Single-page source English remains in the held image until clip end.
    if index + 1 < len(timeline["utterances"]):
        return timeline["utterances"][index + 1]["output_start_sample"]
    return timeline["output_samples"]


def held_subtitle(cues, sample):
    """Keep the current held-page translation through pre/inter/post speech gaps."""
    from bisect import bisect_right

    if not cues:
        return None
    index = max(0, bisect_right([cue["start_sample"] for cue in cues], sample) - 1)
    return cues[index]


def chinese_baseline(anchor, frame_height, first_top, last_bottom):
    """Prefer below the English block; use above when the lower edge has no room."""
    block_height = last_bottom - first_top
    ink_top = anchor[3] + 8
    if ink_top + block_height + 3 > frame_height - 8:
        ink_top = anchor[1] - 8 - block_height
    if ink_top - 3 < 8:
        raise ValueError("No safe in-picture space above or below English subtitles")
    return ink_top - first_top


def overlay_frame(frame, index, fps, layout, overlays):
    from PIL import Image

    width, height = layout["width"], layout["height"]
    canvas_height = layout.get("canvas_height", height)
    if canvas_height != height:
        raise ValueError("Subtitle canvas must preserve source dimensions; no footer or crop")
    sample = round(index / fps * 48000)
    active = [
        i
        for i, event in enumerate(layout["events"])
        if event["start_sample"] <= sample < event["end_sample"]
    ]
    if len(active) > 1:
        raise ValueError("Overlapping subtitle events")
    if not active:
        return frame
    image = Image.frombytes("RGB", (width, height), frame).convert("RGBA")
    return Image.alpha_composite(image, overlays[active[0]]).convert("RGB").tobytes()


def review(render_ref, render_qa, audio_reports, layout, human=None):
    checks = [dict(c) for c in render_qa["checks"]]
    checks.extend(
        {
            "id": f"audio_{i}",
            "status": r["overall"],
            "required": True,
            "reason": "See bound AudioQA artifact",
        }
        for i, r in enumerate(audio_reports)
    )
    checks.extend(
        {
            "id": f"{key}_{i}",
            "status": "REVIEW",
            "required": True,
            "reason": "Listen to this exact raw clip and its bound speaker reference",
        }
        for i in range(len(audio_reports))
        for key in ("voice_identity", "naturalness", "leading_noise", "tail_integrity", "spoken_content")
    )
    checks.extend(
        {
            "id": key,
            "status": "REVIEW",
            "required": True,
            "reason": "Human assessment of this exact rendered artifact is pending",
        }
        for key in (
            "translation_semantics",
            "voice_identity",
            "tail_integrity",
            "visual_layout",
            "human_decision",
        )
    )
    if not layout["events"]:
        checks.append({"id": "subtitle_presence", "status": "FAIL", "required": True})
    reviewer = None
    if human is not None:
        validate("ReviewDecision.v1", human)
        if (
            human["render_artifact_id"] != render_ref["artifact_id"]
            or human["render_sha512"] != render_ref["sha512"]
            or not human["reviewer"]
            or not human["reviewer"].strip()
        ):
            raise ValueError(
                "Human decision must identify reviewer and exact rendered artifact/hash"
            )
        required = {c["id"] for c in checks if c["status"] not in ("PASS", "FAIL")}
        supplied = {c["id"]: c for c in human["checks"]}
        if len(supplied) != len(human["checks"]) or set(supplied) != required:
            raise ValueError(
                "Human decision must cover each unresolved check exactly once"
            )
        expected = (
            "FAIL"
            if any(c["status"] == "FAIL" for c in supplied.values())
            else "PASS"
            if all(c["status"] == "PASS" for c in supplied.values())
            else "REVIEW"
        )
        if human["decision"] != expected:
            raise ValueError("Human decision contradicts its individual checks")
        reviewer = human["reviewer"]
        for check in checks:
            if check["id"] in supplied:
                check.update(
                    status=supplied[check["id"]]["status"],
                    reason=supplied[check["id"]]["reason"],
                )
    overall = (
        "FAIL"
        if any(c["status"] == "FAIL" for c in checks)
        else "PASS"
        if all(c["status"] == "PASS" for c in checks)
        else "REVIEW"
    )
    decision = {
        "render_artifact_id": render_ref["artifact_id"],
        "render_sha512": render_ref["sha512"],
        "reviewer": reviewer,
        "decision": overall,
        "checks": [
            {
                "id": c["id"],
                "status": c["status"] if c["status"] in ("PASS", "FAIL") else "REVIEW",
                "reason": c.get("reason", "Automatic technical check"),
            }
            for c in checks
        ],
    }
    return {"overall": overall, "checks": checks}, decision


def run(request, work, output):
    refs = request["inputs"]
    node = request["node"]

    def src(p):
        return Path(refs[p][0]["path"])

    def val(p):
        r = read_json(src(p))
        validate(refs[p][0]["schema_id"], r)
        return r

    if node == "N17":
        from PIL import Image, ImageDraw, ImageFont

        timeline, utterances = val("timeline"), val("utterances")
        translations = {}
        for ref in refs["translations"]:
            t = read_json(Path(ref["path"]))
            validate("TranslationSet.v1", t)
            if t["source_artifact_id"] != refs["utterances"][0]["artifact_id"]:
                raise ValueError("Translation belongs to different utterances")
            for item in t["items"]:
                if item["unit_id"] in translations:
                    raise ValueError("Duplicate translated ID")
                translations[item["unit_id"]] = item
        units = {u["unit_id"]: u for u in utterances["items"]}
        if set(units) != set(translations) or set(units) != {
            d["id"] for d in timeline["dubs"]
        }:
            raise ValueError("Subtitle unit ID mismatch")
        deployment = request["models"][0]
        font_path = Path(deployment["font_path"])
        if sha512(font_path) != deployment["sha512"]:
            raise ValueError("Font identity changed")
        import shutil

        shutil.copyfile(font_path, work / "font.bin")
        size = request["params"]["font_size"]
        if type(size) is not int or size < 10:
            raise ValueError("Invalid subtitle font size")
        font = ImageFont.truetype(str(work / "font.bin"), size)
        # Verify glyph coverage, rather than accepting tofu from the renderer.
        from fontTools.ttLib import TTFont

        table = TTFont(str(work / "font.bin"), fontNumber=0).getBestCmap()
        info = next(s for s in media.probe(src("video"), work, False)["streams"] if s["codec_type"] == "video")
        width, height = media.video_geometry(info)
        preserve_english = request["params"].get("preserve_source_english", False)
        footer = request["params"].get("footer_height", 0)
        if type(footer) is not int or footer != 0:
            raise ValueError("Subtitle footer is forbidden; preserve source dimensions (spec.md)")
        canvas_height = height + footer
        inline = preserve_english and footer == 0
        anchor = request["params"].get("source_subtitle_box") or next(
            iter(units.values()), {}
        ).get("source_subtitle_box")
        if inline and (
            not isinstance(anchor, list)
            or len(anchor) != 4
            or not all(type(v) is int for v in anchor)
            or not 0 <= anchor[0] < anchor[2] <= width
            or not 0 <= anchor[1] < anchor[3] < height
        ):
            raise ValueError(
                "In-picture Chinese requires the source subtitle bounding box"
            )
        margin = max(12, width // 30)
        line_height = sum(font.getmetrics()) + 4
        events = []
        with zipfile.ZipFile(work / "overlays.zip", "x", zipfile.ZIP_DEFLATED) as z:
            for u, d in zip(timeline["utterances"], timeline["dubs"], strict=True):
                unit = units[u["id"]]
                if inline and "source_subtitle_box" in unit:
                    anchor = unit["source_subtitle_box"]
                t = translations[u["id"]]
                if t["source_text"] != unit["text"]:
                    raise ValueError("Subtitle source text mismatch")
                if any(
                    ord(c) not in table
                    for c in unit["text"] + t["text"]
                    if not c.isspace()
                ):
                    raise ValueError("Font lacks subtitle glyphs: REVIEW")
                en, zh = (
                    wrap(unit["text"], font, width - 2 * margin),
                    wrap(t["text"], font, width - 2 * margin),
                )
                drawn_lines = zh if preserve_english else en + zh
                available_height = footer if preserve_english else height // 2
                if (
                    not inline
                    and len(drawn_lines) * line_height + 2 * margin > available_height
                ):
                    raise ValueError("Subtitle exceeds lower-half layout area: REVIEW")
                image = Image.new("RGBA", (width, canvas_height))
                draw = ImageDraw.Draw(image)
                y = canvas_height - margin - len(drawn_lines) * line_height
                stroke = 2 if inline else 1
                if inline:
                    y = chinese_baseline(
                        anchor,
                        height,
                        font.getbbox(drawn_lines[0], stroke_width=stroke)[1],
                        (len(drawn_lines) - 1) * line_height
                        + font.getbbox(drawn_lines[-1], stroke_width=stroke)[3],
                    )
                boxes = []
                for line in drawn_lines:
                    x = (width - font.getlength(line)) / 2
                    box = draw.textbbox((x, y), line, font=font, stroke_width=stroke)
                    box = [
                        int(box[0]) - 4,
                        int(box[1]) - 3,
                        int(box[2]) + 4,
                        int(box[3]) + 3,
                    ]
                    if (
                        not 0 <= box[0] < box[2] <= width
                        or not 0 <= box[1] < box[3] <= canvas_height
                    ):
                        raise ValueError("Subtitle pixel bounds outside frame")
                    if inline and not (box[3] < anchor[1] or box[1] > anchor[3]):
                        raise ValueError("Chinese overlaps original English subtitle")
                    if not inline:
                        draw.rectangle(box, fill=(0, 0, 0, 200))
                    draw.text(
                        (x, y),
                        line,
                        font=font,
                        fill="white",
                        stroke_width=stroke,
                        stroke_fill="black",
                    )
                    boxes.append(box)
                    y += line_height
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                z.writestr(f"{len(events):06d}.png", buffer.getvalue())
                events.append(
                    {
                        "unit_id": u["id"],
                        "start_sample": caption_output_sample(
                            timeline, u["caption_start_frame"]
                        )
                        if preserve_english and "caption_start_frame" in u
                        else u["output_start_sample"],
                        "end_sample": subtitle_display_end(
                            timeline, len(events), preserve_english
                        ),
                        "en": en,
                        "zh": zh,
                        "boxes": boxes,
                    }
                )
        atomic_json(
            work / "layout.json",
            {
                "timeline_artifact_id": refs["timeline"][0]["artifact_id"],
                "font_sha512": deployment["sha512"],
                "font_source": deployment["source"],
                "width": width,
                "height": height,
                "canvas_height": canvas_height,
                "source_english_preserved": preserve_english,
                "events": events,
                "quality_status": "REVIEW",
                "overlays_sha512": sha512(work / "overlays.zip"),
            },
        )
        for port, name, schema in [
            ("layout", "layout.json", "SubtitleLayout.v1"),
            ("overlays", "overlays.zip", "Binary.v1"),
            ("font", "font.bin", "Binary.v1"),
        ]:
            output(port, name, schema, port)
    elif node == "N18":
        from PIL import Image

        timeline, layout = val("timeline"), val("layout")
        if (
            layout["timeline_artifact_id"] != refs["timeline"][0]["artifact_id"]
            or layout["overlays_sha512"] != refs["overlays"][0]["sha512"]
        ):
            raise ValueError("Layout timeline/overlay binding mismatch")
        dubs = {r["sha512"]: Path(r["path"]) for r in refs["dubs"]}
        if set(dubs) != {d["audio_sha512"] for d in timeline["dubs"]}:
            raise ValueError("Dub audio hashes do not match timeline")
        info = next(s for s in media.probe(src("video"), work, False)["streams"] if s["codec_type"] == "video")
        if media.video_geometry(info) != (layout["width"], layout["height"]):
            raise ValueError("Layout/video geometry mismatch")
        with zipfile.ZipFile(src("overlays")) as z:
            overlays = [
                Image.open(io.BytesIO(z.read(f"{i:06d}.png"))).convert("RGBA")
                for i in range(len(layout["events"]))
            ]

        def overlay(frame, index, fps):
            return overlay_frame(frame, index, fps, layout, overlays)

        media.render(
            src("video"),
            src("audio"),
            timeline,
            work,
            dub_audio=dubs,
            overlay=overlay,
            output_height=layout.get("canvas_height"),
        )
        # Decode actual output and check clocks, not just the timeline metadata.
        media.probe(work / "master.mkv", work)
        with wave.open(str(work / "master.wav")) as wav:
            if wav.getnframes() != timeline["output_samples"]:
                raise ValueError("Rendered sample count mismatch")
        frames = media.command(
            [
                "ffprobe",
                "-v",
                "error",
                "-count_frames",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=nb_read_frames",
                "-of",
                "csv=p=0",
                str(work / "master.mkv"),
            ],
            work,
        )
        if int(frames.strip()) != timeline["output_frames"]:
            raise ValueError("Rendered frame count mismatch")
        atomic_json(
            work / "qa.json",
            {
                "overall": "REVIEW",
                "render_sha512": sha512(work / "master.mkv"),
                "layout_sha512": refs["layout"][0]["sha512"],
                "raw_audio_sha512": [d["raw_audio_sha512"] for d in timeline["dubs"]],
                "checks": [
                    {"id": "full_decode", "status": "PASS", "required": True},
                    {"id": "frame_sample_counts", "status": "PASS", "required": True},
                    {
                        "id": "human_semantics_voice_layout",
                        "status": "REVIEW",
                        "required": True,
                        "reason": "Candidate render requires human assessment",
                    },
                ],
            },
        )
        for port, name, schema in [
            ("master", "master.mkv", "Video.v1"),
            ("audio", "master.wav", "Audio.v1"),
            ("preview", "preview.mp4", "Video.v1"),
            ("qa", "qa.json", "QAReport.v1"),
        ]:
            output(port, name, schema, port)
    else:
        render_qa = val("render_qa")
        if (
            refs["render_qa"][0]["execution_id"] != refs["render"][0]["execution_id"]
            or render_qa.get("render_sha512") != refs["render"][0]["sha512"]
            or render_qa.get("layout_sha512") != refs["layout"][0]["sha512"]
        ):
            raise ValueError("Review evidence does not belong to this render/layout")
        reports = [read_json(Path(r["path"])) for r in refs["audio_qa"]]
        if sorted(r["source_sha512"] for r in reports) != sorted(
            render_qa["raw_audio_sha512"]
        ):
            raise ValueError("Audio QA does not cover exactly the rendered raw clips")
        for report in reports:
            validate("AudioQA.v1", report)
        qa, decision = review(
            refs["render"][0],
            val("render_qa"),
            reports,
            val("layout"),
            request["params"].get("human_decision"),
        )
        for port, name, schema, value in [
            ("qa", "qa.json", "QAReport.v1", qa),
            ("decision", "decision.json", "ReviewDecision.v1", decision),
        ]:
            atomic_json(work / name, value)
            output(port, name, schema, port)
