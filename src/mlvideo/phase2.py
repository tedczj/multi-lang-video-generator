"""Phase 2 workers. Unconfirmed content and coverage remain REVIEW."""

import base64
import json
import re
import subprocess
import time
import urllib.request
import wave
import zipfile
from collections import Counter
from fractions import Fraction as F
from itertools import pairwise
from pathlib import Path

from . import media
from .contracts import validate
from .timeline import plan
from .util import atomic_json, digest, read_json, sha512


def group_utterances(speech, captions, canonical, refs):
    segments = speech["segments"]
    if not segments:
        raise ValueError("No recognized speech: REVIEW")
    total, sr, fps = canonical["source_samples"], 48000, F(canonical["fps"])
    if any(not 0 <= s["start_sample"] < s["end_sample"] <= total for s in segments):
        raise ValueError("Speech interval outside canonical media")
    if any(b["start_sample"] < a["start_sample"] for a, b in pairwise(segments)):
        raise ValueError("Speech segments out of order")
    # Merge any protected interval crossing a proposed cut. Unknown coverage is whole-source.
    groups = []
    for s in segments:
        merge = bool(groups) and (
            s["start_sample"] <= groups[-1]["end_sample"]
            or any(
                v["start_sample"] < s["start_sample"]
                and v["end_sample"] > groups[-1]["end_sample"]
                for v in speech["protected_intervals"]
            )
            or F(int(F(s["start_sample"], sr) * fps), 1) / fps
            < F(groups[-1]["end_sample"], sr)
        )
        if merge:
            g = groups[-1]
            g["segments"].append(s)
            g["end_sample"] = max(g["end_sample"], s["end_sample"])
        else:
            groups.append(
                {
                    "start_sample": s["start_sample"],
                    "end_sample": s["end_sample"],
                    "segments": [s],
                }
            )
    units = []
    for index, g in enumerate(groups):
        speakers = {s["speaker_id"] for s in g["segments"]}
        if len(speakers) > 1:
            raise ValueError(
                "Overlapping/unsafe different speakers: manual regroup REVIEW"
            )
        if speech["coverage_status"] != "PASS":
            g["start_sample"], g["end_sample"] = 0, total
        next_start = (
            groups[index + 1]["start_sample"] if index + 1 < len(groups) else total
        )
        cut = (
            int(F(next_start, sr) * fps)
            if index + 1 < len(groups)
            else canonical["source_frames"]
        )
        text = " ".join(s["text"] for s in g["segments"])
        ids = [s["id"] for s in g["segments"]]
        units.append(
            {
                "unit_id": "unit_" + digest([refs["speech"], ids])[:24],
                "start_sample": g["start_sample"],
                "end_sample": g["end_sample"],
                "safe_cut_frame": cut,
                "text": text,
                "speaker_id": next(iter(speakers)),
                "speech_ids": ids,
                "caption_ids": [
                    c["id"]
                    for c in captions["cues"]
                    if c["start_sample"] < g["end_sample"]
                    and c["end_sample"] > g["start_sample"]
                ],
                "merge_reason": "Protect all unverified source speech"
                if speech["coverage_status"] != "PASS"
                else "Merge overlaps and intervals without safe integral cut",
            }
        )
    return {
        "speech_artifact_id": refs["speech"],
        "caption_artifact_id": refs["captions"],
        "canonical_artifact_id": refs["canonical"],
        "items": units,
        "quality_status": "REVIEW",
    }


def choose_text(utterances, captions, mode):
    if mode == "speech":
        return utterances
    if mode != "caption_consensus" or len(utterances["items"]) != 1:
        raise ValueError(
            "Caption consensus requires one fully protected preview utterance"
        )
    frames = {}
    for cue in captions["cues"]:
        frames.setdefault(cue["frame"], []).append(cue)
    readings = []
    for cues in frames.values():
        cues.sort(key=lambda c: (c["boxes"][0][1], c["boxes"][0][0]))
        readings.append(" ".join(" ".join(c["text"].split()) for c in cues))
    if not readings or len({"".join(text.split()) for text in readings}) != 1:
        raise ValueError(
            "Hard subtitle changes or OCR disagrees inside preview: select a complete stable caption window"
        )
    variants = Counter(readings).most_common()
    if len(variants) > 1 and variants[0][1] == variants[1][1]:
        raise ValueError("Ambiguous OCR spacing: REVIEW")
    utterances["items"][0]["text"] = variants[0][0]
    utterances["items"][0]["merge_reason"] += (
        f"; unanimous non-space OCR characters; {len(variants)} spacing variants, "
        f"selected {variants[0][1]}/{len(readings)} modal spacing; ASR only protects speech; REVIEW"
    )
    return utterances


def parse_srt(text, offset_samples=0):
    cues = []
    pattern = r"(\d\d):(\d\d):(\d\d)[,.](\d{3})"

    def sample(parts):
        h, m, s, ms = map(int, parts)
        return ((h * 60 + m) * 60 + s) * 48000 + ms * 48 + offset_samples

    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.splitlines()
        timing = next((i for i, s in enumerate(lines) if "-->" in s), None)
        if timing is None:
            continue
        times = re.findall(pattern, lines[timing])
        if len(times) != 2:
            raise ValueError("Invalid subtitle timestamp")
        a, b = map(sample, times)
        body = "\n".join(lines[timing + 1 :]).strip()
        if not 0 <= a < b or not body:
            raise ValueError("Invalid subtitle cue")
        cues.append(
            {
                "id": "cue_" + digest([len(cues), a, b, body])[:20],
                "start_sample": a,
                "end_sample": b,
                "text": body,
                "boxes": [],
                "frame": 0,
            }
        )
    return cues


def run(request, work, output):
    node, p = request["node"], request["params"]
    refs = request["inputs"]

    def src(port):
        return Path(refs[port][0]["path"])

    def val(port):
        value = read_json(src(port))
        validate(refs[port][0]["schema_id"], value)
        return value

    def save(port, name, schema, value):
        atomic_json(work / name, value)
        output(port, name, schema, port)

    if node == "N04" and request["strategy_id"] == "audio_source":
        info = val("probe")
        if len([s for s in info["streams"] if s["codec_type"] == "audio"]) != 1:
            raise ValueError("Ambiguous source audio track")
        media.command(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-xerror",
                "-i",
                str(src("source")),
                "-map",
                "0:a:0",
                "-vn",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-c:a",
                "pcm_f32le",
                str(work / "source.float.wav"),
            ],
            work,
        )
        from .audio_qa import safe_reference_pcm

        conversion = safe_reference_pcm(
            work / "source.float.wav", work / "source.wav", work
        )
        first = json.loads(
            media.command(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "a:0",
                    "-read_intervals",
                    "%+1",
                    "-show_frames",
                    "-of",
                    "json",
                    str(src("source")),
                ],
                work,
            )
        )["frames"][0]
        save(
            "receipt",
            "audio-source.json",
            "Binary.v1",
            {
                "source_artifact_id": refs["source"][0]["artifact_id"],
                "source_sha512": refs["source"][0]["sha512"],
                "sample_rate": 48000,
                "channels": 2,
                "first_decoded_audio_time": first.get("best_effort_timestamp_time"),
                "conversion": conversion,
                "processing": "Complete original audio preserved as FLOAT with a separately levelled PCM reference copy",
            },
        )
        output("audio", "source.wav", "Audio.v1", "full_source_audio")
        output("raw", "source.float.wav", "Audio.v1", "full_source_float_audio")
    elif node == "N04":
        start, end, height = p["start_seconds"], p["end_seconds"], p["height"]
        if (
            not 0 <= start < end
            or type(height) is not int
            or height < 144
            or height % 2
        ):
            raise ValueError("Invalid preview excerpt bounds/height")
        excerpt = work / "excerpt.mkv"
        # Decode a short preroll, then trim at the output. Input-only seeking can
        # skip the PCM packet containing the requested first sample.
        seek = max(0, start - 1)
        media.command(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-ss",
                str(seek),
                "-i",
                str(src("source")),
                "-ss",
                str(start - seek),
                "-t",
                str(end - start),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0",
                "-vf",
                f"scale=-2:{height}",
                "-c:v",
                "ffv1",
                "-c:a",
                "pcm_s16le",
                "-ar",
                "48000",
                "-ac",
                "2",
                str(excerpt),
            ],
            work,
        )
        media.normalize(excerpt, media.probe(excerpt, work), work)
        save(
            "excerpt",
            "excerpt.json",
            "Binary.v1",
            {
                "source_artifact_id": refs["source"][0]["artifact_id"],
                "source_sha512": refs["source"][0]["sha512"],
                "start_seconds": start,
                "end_seconds": end,
                "seek_preroll_seconds": start - seek,
                "height": height,
                "purpose": "Explicit preview excerpt; canonical clock is relative to this window",
            },
        )
        for port, name, schema in [
            ("video", "canonical.mkv", "Video.v1"),
            ("audio", "canonical.wav", "Audio.v1"),
            ("canonical", "canonical.json", "CanonicalMedia.v1"),
        ]:
            output(port, name, schema, port)
    elif node == "N05":
        roi = p["roi"]
        if (
            len(roi) != 4
            or not 0 <= roi[0] < roi[2] <= 1
            or not 0 <= roi[1] < roi[3] <= 1
        ):
            raise ValueError("ROI must be normalized x0,y0,x1,y1")
        info = val("probe")
        media.command(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(src("video")),
                "-frames:v",
                "1",
                str(work / "inventory-frame.png"),
            ],
            work,
        )
        save(
            "inventory",
            "inventory.json",
            "SubtitleInventory.v1",
            {
                "video_artifact_id": refs["video"][0]["artifact_id"],
                "subtitle_streams": [
                    s for s in info["streams"] if s["codec_type"] == "subtitle"
                ],
                "hard_subtitles": "unknown",
                "roi": roi,
                "evidence": ["inventory-frame.png"],
                "quality_status": "REVIEW",
            },
        )
        output("frame", "inventory-frame.png", "Binary.v1", "inventory_frame")
    elif node == "N06":
        inventory, c = val("inventory"), val("canonical")
        cues = []
        if p["mode"] == "soft":
            streams = inventory["subtitle_streams"]
            if len(streams) != 1:
                raise ValueError("Select an unambiguous soft subtitle track: REVIEW")
            media.command(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    str(src("source")),
                    "-map",
                    "0:" + str(streams[0]["index"]),
                    "-f",
                    "srt",
                    str(work / "source.srt"),
                ],
                work,
            )
            offset = round(
                (F(c["video_lead_frames"]) / F(c["fps"]) - F(c["source_pts"][0]))
                * 48000
            )
            cues = parse_srt((work / "source.srt").read_text(), offset)
        elif p["mode"] == "ocr":
            deployment = request["models"][0]
            stride = p["stride_frames"]
            if type(stride) is not int or stride < 1:
                raise ValueError("Invalid OCR frame stride")
            folder = work / "frames"
            folder.mkdir()
            media.command(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    str(src("video")),
                    "-vf",
                    f"select=not(mod(n\\,{stride}))",
                    "-fps_mode",
                    "vfr",
                    str(folder / "%06d.png"),
                ],
                work,
            )
            info = media.probe(src("video"), work, False)["streams"][0]
            roi = inventory["roi"]
            width, height = info["width"], info["height"]
            for i, frame in enumerate(sorted(folder.glob("*.png"))):
                body = {
                    "file": base64.b64encode(frame.read_bytes()).decode(),
                    "fileType": 1,
                    "visualize": False,
                    "useDocOrientationClassify": False,
                    "useDocUnwarping": False,
                    "useTextlineOrientation": False,
                }
                options = deployment.get("request_options", {})
                if set(options) - {
                    "textDetLimitSideLen",
                    "textDetLimitType",
                    "textDetUnclipRatio",
                }:
                    raise ValueError("Unsupported OCR deployment request option")
                body.update(options)
                atomic_json(work / f"ocr-{i}.request.json", body)
                start = time.monotonic()
                req = urllib.request.Request(
                    deployment["endpoint"],
                    json.dumps(body).encode(),
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=180) as response:
                    raw = response.read()
                (work / f"ocr-{i}.response.json").write_bytes(raw)
                atomic_json(
                    work / f"ocr-{i}.timing.json",
                    {
                        "seconds": time.monotonic() - start,
                        "frame": i * stride,
                        "image_sha512": sha512(frame),
                        "deployment": deployment,
                    },
                )
                result = json.loads(raw)
                if result.get("errorCode") != 0:
                    raise ValueError("PaddleOCR business error; raw response retained")
                for page in result["result"]["ocrResults"]:
                    res = page["prunedResult"]
                    for text, box in zip(
                        res["rec_texts"], res["rec_boxes"], strict=True
                    ):
                        x0, y0, x1, y1 = box
                        if (
                            text.strip()
                            and roi[0] * width <= (x0 + x1) / 2 <= roi[2] * width
                            and roi[1] * height <= (y0 + y1) / 2 <= roi[3] * height
                        ):
                            a = round(F(i * stride, 1) / F(c["fps"]) * 48000)
                            b = min(
                                c["source_samples"],
                                round(F((i + 1) * stride, 1) / F(c["fps"]) * 48000),
                            )
                            cues.append(
                                {
                                    "id": "cue_"
                                    + digest(
                                        [
                                            refs["inventory"][0]["artifact_id"],
                                            i,
                                            len(cues),
                                        ]
                                    )[:20],
                                    "start_sample": a,
                                    "end_sample": b,
                                    "text": text,
                                    "boxes": [box],
                                    "frame": i * stride,
                                }
                            )
        else:
            raise ValueError(
                "Caption mode must be soft or ocr; empty tracks require observed evidence"
            )
        save(
            "captions",
            "captions.json",
            "CaptionTrack.v1",
            {
                "inventory_artifact_id": refs["inventory"][0]["artifact_id"],
                "cues": cues,
                "method": p["mode"],
                "quality_status": "REVIEW",
            },
        )
        with zipfile.ZipFile(work / "raw.zip", "x", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(work.rglob("*")):
                if f.is_file() and f.name not in ("raw.zip", "captions.json"):
                    z.write(f, str(f.relative_to(work)))
        output("raw", "raw.zip", "Binary.v1", "caption_evidence")
    elif node == "N07":
        from .config import ROOT

        subprocess.run(
            [
                request["models"][0]["python"],
                str(
                    ROOT
                    / (
                        "workers/speaker_worker.py"
                        if request["strategy_id"] == "speaker_diarization"
                        else "workers/speech_worker.py"
                    )
                ),
                "--request",
                str(work.parent / "request.json"),
                "--result",
                str(work / "worker-result.json"),
            ],
            check=True,
        )
        return read_json(work / "worker-result.json")["artifacts"]
    elif node == "N08" and request["strategy_id"] == "caption_pages":
        from .caption_pages import build_pages

        save(
            "utterances",
            "utterances.json",
            "UtteranceSet.v1",
            build_pages(
                val("speech"),
                val("captions"),
                read_json(src("vad")),
                val("canonical"),
                {key: refs[key][0]["artifact_id"] for key in refs},
                p["pages"],
            ),
        )
    elif node == "N08":
        save(
            "utterances",
            "utterances.json",
            "UtteranceSet.v1",
            choose_text(
                group_utterances(
                    val("speech"),
                    val("captions"),
                    val("canonical"),
                    {k: refs[k][0]["artifact_id"] for k in refs},
                ),
                val("captions"),
                p["text_source"],
            ),
        )
    elif node == "N09":
        from .translation import translate

        translate(request, work)
        for port, name, schema in [
            ("translation", "translation.json", "TranslationSet.v1"),
            ("batch", "batch.json", "TranslationBatch.v1"),
            ("receipt", "receipt.json", "TranslationReceipt.v1"),
            ("events", "events.jsonl", "Binary.v1"),
            ("response", "response.json", "Binary.v1"),
            ("prompt", "prompt.txt", "Binary.v1"),
        ]:
            output(port, name, schema, port)
    elif node == "N10" and request["strategy_id"] == "speaker_bank":
        from .speaker_bank import build_bank

        build_bank(
            src("audio"),
            val("speech"),
            val("speakers"),
            {k: refs[k][0]["artifact_id"] for k in refs},
            work,
        )
        output("bank", "bank.json", "SpeakerBank.v1", "speaker_bank")
        output(
            "references", "references.zip", "Binary.v1", "speaker_reference_candidates"
        )
    elif node == "N10" and request["strategy_id"] == "from_speaker_bank":
        from .speaker_bank import select_reference, write_reference

        bank = val("bank")
        if (
            bank["source_audio_artifact_id"] != refs["audio"][0]["artifact_id"]
            or bank["speech_artifact_id"] != refs["speech"][0]["artifact_id"]
        ):
            raise ValueError("Selected speaker bank belongs to another source")
        selected = select_reference(bank, p["speaker_id"], p["candidate_id"])
        with wave.open(str(src("audio"))) as audio:
            write_reference(
                audio,
                selected["segments"],
                selected["gap_samples"],
                work / "reference.wav",
            )
        if sha512(work / "reference.wav") != selected["audio_sha512"]:
            raise ValueError("Rebuilt reference differs from immutable bank candidate")
        save(
            "reference",
            "reference.json",
            "VoiceReference.v2",
            {
                "speaker_id": p["speaker_id"],
                "text": selected["text"],
                "audio_sha512": selected["audio_sha512"],
                "source_audio_artifact_id": refs["audio"][0]["artifact_id"],
                "bank_artifact_id": refs["bank"][0]["artifact_id"],
                "candidate_id": selected["candidate_id"],
                "sample_rate": 48000,
                "frames": selected["frames"],
                "gap_samples": selected["gap_samples"],
                "segments": selected["segments"],
            },
        )
        output("audio", "reference.wav", "Audio.v1", "bank_reference_audio")
    elif node == "N10":
        speech = val("speech")
        speaker = p["speaker_id"]
        if not speaker or not p["transcript"].strip():
            raise ValueError(
                "Explicit reference speaker and exact transcript are required: REVIEW"
            )
        a, b = p["start_sample"], p["end_sample"]
        if speech["audio_artifact_id"] != refs["audio"][0]["artifact_id"]:
            raise ValueError("Speech/source PCM mismatch")
        with wave.open(str(src("audio"))) as source:
            if (
                type(a) is not int
                or type(b) is not int
                or not 0 <= a < b <= source.getnframes()
                or not 48000 <= b - a <= 30 * 48000
            ):
                raise ValueError("Reference must be 1–30 seconds within source PCM")
            if any(
                s["speaker_id"] not in (None, speaker)
                and s["start_sample"] < b
                and s["end_sample"] > a
                for s in speech["segments"]
            ):
                raise ValueError("Reference contains another speaker")
            source.setpos(a)
            with wave.open(str(work / "reference.wav"), "wb") as dst:
                dst.setparams(source.getparams())
                dst.writeframes(source.readframes(b - a))
        save(
            "reference",
            "reference.json",
            "VoiceReference.v1",
            {
                "speaker_id": speaker,
                "text": p["transcript"],
                "audio_sha512": sha512(work / "reference.wav"),
                "source_audio_artifact_id": refs["audio"][0]["artifact_id"],
                "start_sample": a,
                "end_sample": b,
                "sample_rate": 48000,
            },
        )
        output("audio", "reference.wav", "Audio.v1", "reference_audio")
    elif node == "N16":
        utterances = val("utterances")
        clips = {}
        for ref in refs["clips"]:
            clip = read_json(Path(ref["path"]))
            validate("DubClip.v1", clip)
            if clip["unit_id"] in clips or clip["quality_status"] == "FAIL":
                raise ValueError("Duplicate/failed dub clip")
            clips[clip["unit_id"]] = (clip, ref)
        if set(clips) != {u["unit_id"] for u in utterances["items"]}:
            raise ValueError("Dub/utterance ID mismatch")
        raws = {
            ref["artifact_id"]: read_json(Path(ref["path"]))
            for ref in refs["raw_clips"]
        }
        translations = {}
        for ref in refs["translations"]:
            tr = read_json(Path(ref["path"]))
            validate("TranslationSet.v1", tr)
            if tr["source_artifact_id"] != refs["utterances"][0]["artifact_id"]:
                raise ValueError("Translation source binding mismatch")
            for item in tr["items"]:
                if item["unit_id"] in translations:
                    raise ValueError("Duplicate translated ID")
                translations[item["unit_id"]] = (item, ref["artifact_id"])
        if set(translations) != set(clips):
            raise ValueError("Translation/dub unit mismatch")
        if set(raws) != {clip["raw_clip_artifact_id"] for clip, _ in clips.values()}:
            raise ValueError("Raw/dub artifact mismatch")
        inputs = []
        for u in utterances["items"]:
            clip, _ = clips[u["unit_id"]]
            raw = raws[clip["raw_clip_artifact_id"]]
            validate("RawDubClip.v1", raw)
            tr, tr_id = translations[u["unit_id"]]
            if (
                raw["unit_id"] != u["unit_id"]
                or raw["translation_artifact_id"] != tr_id
                or raw["text"] != tr["text"]
                or tr["source_text"] != u["text"]
                or raw["speaker_id"] != clip["speaker_id"]
            ):
                raise ValueError("Dub text/speaker/translation binding mismatch")
            if u["speaker_id"] is not None and clip["speaker_id"] != u["speaker_id"]:
                raise ValueError("Dub speaker mismatch")
            inputs.append(
                {
                    "id": u["unit_id"],
                    "start_sample": u["start_sample"],
                    "end_sample": u["end_sample"],
                    "safe_cut_frame": u["safe_cut_frame"],
                    "dub_samples": clip["frames"],
                }
            )
        for source_unit, planned in zip(utterances["items"], inputs, strict=True):
            if "caption_end_frame" in source_unit:
                planned["visual_cut_limit_frame"] = source_unit["caption_end_frame"]
        timeline = plan(val("canonical"), inputs)
        for source_unit, planned in zip(
            utterances["items"], timeline["utterances"], strict=True
        ):
            for key in (
                "caption_start_frame",
                "caption_end_frame",
                "source_subtitle_box",
            ):
                if key in source_unit:
                    planned[key] = source_unit[key]
        for dub in timeline["dubs"]:
            clip, ref = clips[dub["id"]]
            dub.pop("tone_hz")
            dub.update(
                audio_sha512=clip["audio_sha512"],
                raw_audio_sha512=raws[clip["raw_clip_artifact_id"]]["audio_sha512"],
                clip_artifact_id=ref["artifact_id"],
            )
        save("timeline", "timeline.json", "TimelinePlan.v1", timeline)
    elif node in ("N17", "N18", "N19"):
        from .subtitles import run as run_render

        return run_render(request, work, output)
    return None
