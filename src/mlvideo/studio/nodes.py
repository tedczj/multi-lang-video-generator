"""Explicit studio worker strategies. No database or model credentials in workers."""
from __future__ import annotations

import shutil
import wave
from pathlib import Path

from ..contracts import validate
from ..speech import apply_annotation
from ..translation import batches
from ..timeline import cut_frame, frame_sample
from ..util import atomic_json, read_json, sha512, digest


def run(request, work, output):
    refs, p, name = request["inputs"], request["params"], request["strategy_id"]

    def ref(port):
        return refs[port][0]

    def value(port):
        r = ref(port)
        path = Path(r["path"])
        if sha512(path) != r["sha512"]:
            raise ValueError("Studio input hash changed")
        v = read_json(path)
        if r["schema_id"] != "Binary.v1":
            validate(r["schema_id"], v)
        return v

    def save(port, filename, schema, v):
        if schema != "Binary.v1":
            validate(schema, v)
        atomic_json(work / filename, v)
        output(port, filename, schema, port)

    if name == "studio_captions":
        speech = value("speech")
        # Reuse the exact CaptionTrack schema, explicitly marking ASR as a candidate.
        captions = {
            "inventory_artifact_id": ref("inventory")["artifact_id"],
            "cues": [{"id": "cue_" + digest(s)[:20], "start_sample": s["start_sample"],
                      "end_sample": s["end_sample"], "text": s["text"], "boxes": [], "frame": 0}
                     for s in speech["segments"] if s["text"].strip()],
            "method": "asr_candidate_not_original_subtitles",
            "quality_status": "REVIEW",
        }
        save("captions", "captions.json", "CaptionTrack.v1", captions)
    elif name == "studio_utterances":
        clock, speech, captions, vad = value("canonical"), value("speech"), value("captions"), value("vad")
        if speech["audio_artifact_id"] != ref("audio")["artifact_id"]:
            raise ValueError("Reviewed speech is not bound to this source audio")
        rows = p["items"]
        if not rows or len({u["unit_id"] for u in rows}) != len(rows):
            raise ValueError("Empty/duplicate reviewed unit IDs")
        count = clock["source_samples"]
        if any(type(u[k]) is not int for u in rows for k in ("start_sample", "end_sample")):
            raise ValueError("Review boundaries must be integer PCM samples")
        if any(not 0 <= u["start_sample"] < u["end_sample"] <= count or not u["text"].strip() for u in rows):
            raise ValueError("Reviewed units are empty or outside canonical PCM")
        if p["require_coverage"]:
            if not p["reviewer"].strip() or not p["reason"].strip():
                raise ValueError("Named source boundary/transcript review is required")
            if vad.get("sample_rate") != 16000:
                raise ValueError("Expected original 16 kHz VAD evidence")
            annotation = {"audio_sha512": ref("audio")["sha512"], "reviewer": p["reviewer"],
                          "segments": [{k: u[k] for k in ("start_sample", "end_sample", "speaker_id", "text")} for u in rows]}
            detected = [dict(s) for s in speech["segments"]]
            apply_annotation(annotation, ref("audio"), count, detected, vad["intervals"])
        units = []
        for i, u in enumerate(rows):
            next_start = rows[i+1]["start_sample"] if i+1 < len(rows) else count
            cut = cut_frame(clock, next_start) if i+1 < len(rows) else clock["source_frames"]
            if p["require_coverage"] and (cut < 0 or frame_sample(clock, cut) < u["end_sample"]):
                raise ValueError(f"片段 {u['unit_id']} 后没有安全整帧切点。请合并同角色相邻片段或重新审核边界")
            units.append({"unit_id": u["unit_id"], "start_sample": u["start_sample"], "end_sample": u["end_sample"],
                "safe_cut_frame": cut, "text": u["text"], "speaker_id": u.get("speaker_id"),
                "speech_ids": [s["id"] for s in speech["segments"] if s["start_sample"] < u["end_sample"] and s["end_sample"] > u["start_sample"]],
                "caption_ids": [c["id"] for c in captions["cues"] if c["start_sample"] < u["end_sample"] and c["end_sample"] > u["start_sample"]],
                "merge_reason": "Studio versioned manual review" if p["require_coverage"] else "Studio translation-only candidate; NOT coverage-approved"})
        save("utterances", "utterances.json", "UtteranceSet.v1", {
            "speech_artifact_id": ref("speech")["artifact_id"], "caption_artifact_id": ref("captions")["artifact_id"],
            "canonical_artifact_id": ref("canonical")["artifact_id"], "items": units, "quality_status": "REVIEW"})
        save("review", "review.json", "Binary.v1", p)
    elif name == "studio_translation":
        units = value("utterances")["items"]
        groups = batches(units)
        index = p["batch_index"]
        if type(index) is not int or not 0 <= index < len(groups):
            raise ValueError("Invalid manual translation batch")
        rows = p["items"]
        if len(rows) != len(groups[index]) or len({u["unit_id"] for u in rows}) != len(rows):
            raise ValueError("Manual translation ID count mismatch")
        expected = {u["unit_id"]: u for u in groups[index]}
        if set(expected) != {u["unit_id"] for u in rows}:
            raise ValueError("Manual translation unit ID mismatch")
        translated = []
        for u in rows:
            if u["source_text"] != expected[u["unit_id"]]["text"] or not u["text"].strip():
                raise ValueError("Manual translation source binding/empty target")
            translated.append({k: u[k] for k in ("unit_id", "source_text", "text")})
        save("translation", "translation.json", "TranslationSet.v1", {
            "batch_id": request["execution_id"], "source_artifact_id": ref("utterances")["artifact_id"], "items": translated})
        save("review", "translation-review.json", "Binary.v1", {"source": "human_reviewed", "reviewer": p["reviewer"], "reason": p["reason"]})
    elif name == "studio_sample":
        reference = value("reference")
        if not p["text"].strip() or not p["unit_id"].strip():
            raise ValueError("Sample text/unit required")
        save("translation", "translation.json", "TranslationSet.v1", {
            "batch_id": request["execution_id"], "source_artifact_id": ref("reference")["artifact_id"],
            "items": [{"unit_id": p["unit_id"], "source_text": reference["text"], "text": p["text"]}]})
    elif name == "studio_reference":
        value("speech")
        source = Path(ref("audio")["path"])
        if sha512(source) != ref("audio")["sha512"]:
            raise ValueError("Reference source audio changed")
        a, b = p["start_sample"], p["end_sample"]
        if type(a) is not int or type(b) is not int or not 48000 <= b-a <= 30*48000 or not p["transcript"].strip():
            raise ValueError("Reference requires 1–30 seconds and exact transcript")
        if not p["reviewer"].strip() or not p["character_id"].strip() or not p["annotations"]:
            raise ValueError("Reference requires a reviewed character attribution")
        if any(x["status"] != "CONFIRMED" or x["character_id"] != p["character_id"] for x in p["annotations"]):
            raise ValueError("Reference includes unconfirmed or conflicting identities")
        with wave.open(str(source), "rb") as wav:
            if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (48000, 2, 2) or not 0 <= a < b <= wav.getnframes():
                raise ValueError("Expected bounded 48 kHz stereo PCM16")
            wav.setpos(a)
            with wave.open(str(work / "reference.wav"), "wb") as out:
                out.setparams(wav.getparams()); out.writeframes(wav.readframes(b-a))
        save("reference", "reference.json", "VoiceReference.v1", {
            "speaker_id": p["character_id"], "text": p["transcript"], "audio_sha512": sha512(work / "reference.wav"),
            "source_audio_artifact_id": ref("audio")["artifact_id"], "start_sample": a, "end_sample": b, "sample_rate": 48000})
        save("review", "reference-selection.json", "Binary.v1", p)
        output("audio", "reference.wav", "Audio.v1", "reference_audio")
    elif name == "studio_import_reference":
        reference = value("reference")
        audio = ref("audio")
        if sha512(Path(audio["path"])) != audio["sha512"] or reference["audio_sha512"] != audio["sha512"]:
            raise ValueError("Cross-episode reference text/audio hash binding mismatch")
        shutil.copyfile(Path(audio["path"]), work / "reference.wav")
        save("reference", "reference.json", "VoiceReference.v1", reference)
        save("receipt", "import-receipt.json", "Binary.v1", {
            "profile_id": p["profile_id"], "profile_sha512": p["profile_sha512"], "purpose": p["purpose"],
            "origin_asset_sha512": ref("reference")["asset_sha512"],
            "origin_reference_artifact_id": ref("reference")["artifact_id"],
            "origin_audio_artifact_id": audio["artifact_id"], "origin_audio_sha512": audio["sha512"],
            "target_asset_sha512": request["asset_sha512"]})
        output("audio", "reference.wav", "Audio.v1", "imported_reference_audio")
    else:
        raise ValueError("Unknown studio strategy")
