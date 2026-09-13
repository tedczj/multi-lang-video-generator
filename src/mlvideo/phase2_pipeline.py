"""Serial candidate workflow; each batch/unit is an independent immutable execution."""

import json
from pathlib import Path

from .translation import batches
from .util import atomic_json, read_json, sha512, uid


def ports(result):
    return {
        json.loads(a["metadata_json"])["port"]: a["artifact_id"]
        for a in result["artifacts"]
    }


def run_candidate(engine, asset, source_id, settings):
    identity = uid("run")
    directory = engine.root / "videos" / asset / "runs" / identity
    atomic_json(directory / "plan.json", settings)
    atomic_json(directory / "bindings.json", {"source": source_id})
    engine.db.insert(
        "runs",
        {
            "id": identity,
            "asset_sha512": asset,
            "recipe_path": str((directory / "plan.json").relative_to(engine.root)),
            "recipe_hash": sha512(directory / "plan.json"),
            "bindings_path": str(
                (directory / "bindings.json").relative_to(engine.root)
            ),
            "bindings_hash": sha512(directory / "bindings.json"),
            "state": "RUNNING",
        },
    )
    executions = {}

    def run(key, node, strategy, inputs, params=None):
        result = engine.run(
            asset, node, strategy, inputs, params or {}, scope=key, run_id=identity
        )
        executions[key] = result
        atomic_json(directory / "executions.json", executions)
        return ports(result)

    def value(artifact):
        return read_json(Path(engine.artifact(artifact, asset)["path"]))

    try:
        probe = run("probe", "N03", "ffprobe", {"source": source_id})
        c = run(
            "canonical",
            "N04",
            "excerpt" if "excerpt" in settings else "ffmpeg",
            {"source": source_id, "probe": probe["probe"]},
            settings.get("excerpt", {}),
        )
        inventory = run(
            "inventory",
            "N05",
            "inventory",
            {"video": c["video"], "probe": probe["probe"]},
            {"roi": settings.get("roi", [0, 0.5, 1, 1])},
        )
        captions = run(
            "captions",
            "N06",
            "captions",
            {
                "source": source_id,
                "video": c["video"],
                "canonical": c["canonical"],
                "inventory": inventory["inventory"],
            },
            {
                "mode": settings.get("caption_mode", "ocr"),
                "stride_frames": settings.get("stride_frames", 25),
            },
        )
        bank_settings = settings.get("speaker_bank")
        if bank_settings:
            bank = value(bank_settings["bank"])
            reviewed = value(bank_settings["speech"])
            bank_speech = engine.artifact(bank_settings["speech"], asset)
            bank_track = engine.artifact(bank["speaker_track_artifact_id"], asset)
            review_ref = engine.artifact(bank_settings["review"], asset)
            review_request = read_json(
                engine.root
                / engine.db.one(
                    "SELECT request_path FROM executions WHERE id=%s",
                    (review_ref["execution_id"],),
                )["request_path"]
            )
            if (
                review_request["node"] != "N08"
                or review_request["strategy_id"] != "reviewed_speech"
                or bank_speech["execution_id"] != review_ref["execution_id"]
                or bank_track["execution_id"] != review_ref["execution_id"]
                or bank["speech_artifact_id"] != bank_settings["speech"]
                or reviewed["coverage_status"] != "PASS"
                or reviewed["audio_artifact_id"] != bank["source_audio_artifact_id"]
                or engine.artifact(bank["source_audio_artifact_id"], asset)["sha512"]
                != engine.artifact(c["audio"], asset)["sha512"]
            ):
                raise ValueError(
                    "Speaker bank requires reviewed grouping bound to this exact canonical PCM"
                )
            speech = {"speech": bank_settings["speech"]}
        else:
            speech = run(
                "speech",
                "N07",
                "whisper",
                {"audio": c["audio"]},
                {"annotation": settings.get("speech_annotation")},
            )
        utterances = run(
            "utterances",
            "N08",
            "group",
            {
                "speech": speech["speech"],
                "captions": captions["captions"],
                "canonical": c["canonical"],
            },
            {"text_source": settings.get("text_source", "speech")},
        )
        units = value(utterances["utterances"])["items"]
        references = {}
        reference_settings = settings.get("references", [])
        if bank_settings:
            reference_settings = bank_settings["references"]
            if {r["speaker_id"] for r in reference_settings} != {
                u["speaker_id"] for u in units
            }:
                raise ValueError(
                    "Reviewed references must cover exactly the utterance speakers"
                )
        for i, reference in enumerate(reference_settings):
            speaker = reference["speaker_id"]
            if speaker in references:
                raise ValueError("Duplicate speaker reference")
            if bank_settings and reference.get("review") is None:
                raise ValueError(
                    "Speaker bank cloning requires reference listening review"
                )
            references[speaker] = run(
                f"reference_{i}",
                "N10",
                "from_speaker_bank" if bank_settings else "reference",
                {
                    "audio": bank["source_audio_artifact_id"],
                    "speech": speech["speech"],
                    "bank": bank_settings["bank"],
                }
                if bank_settings
                else {"audio": c["audio"], "speech": speech["speech"]},
                reference,
            )
        translated = []
        by_unit = {}
        for i, group in enumerate(batches(units)):
            tr = run(
                f"translate_{i}",
                "N09",
                "codex",
                {"utterances": utterances["utterances"]},
                {"batch_index": i},
            )
            translated.append(tr["translation"])
            by_unit.update({u["unit_id"]: tr["translation"] for u in group})
        raw_clips = []
        clips = []
        audios = []
        qas = []
        unit_evidence = []
        for i, u in enumerate(units):
            speaker = u["speaker_id"]
            if speaker is None:
                # Explicit sample declaration; never infer single speaker from lack of diarization.
                speaker = settings.get("declared_single_speaker")
            if speaker not in references:
                raise ValueError("Unknown speaker: supply reviewed speaker mapping")
            reference = references[speaker]
            raw = run(
                f"tts_{i}",
                "N11",
                "cosyvoice3_from_bank" if bank_settings else "cosyvoice3_zero_shot",
                {
                    "translation": by_unit[u["unit_id"]],
                    "reference": reference["reference"],
                    "audio": reference["audio"],
                },
                {"unit_id": u["unit_id"]},
            )
            dub = run(
                f"audio_qa_{i}",
                "N12",
                "audio_qa_asr",
                {"audio": raw["audio"], "clip": raw["clip"]},
            )
            raw_clips.append(raw["clip"])
            clips.append(dub["clip"])
            audios.append(dub["audio"])
            qas.append(dub["qa"])
            unit_evidence.append(
                {
                    "unit_id": u["unit_id"],
                    "speaker_id": speaker,
                    "source_text": u["text"],
                    "translation": by_unit[u["unit_id"]],
                    "reference": reference["reference"],
                    "raw_clip": raw["clip"],
                    "dub_clip": dub["clip"],
                    "audio": dub["audio"],
                    "audio_qa": dub["qa"],
                    "content_asr": dub["asr"],
                }
            )
        timeline = run(
            "timeline",
            "N16",
            "dub_gap_first",
            {
                "canonical": c["canonical"],
                "utterances": utterances["utterances"],
                "clips": clips,
                "raw_clips": raw_clips,
                "translations": translated,
            },
        )
        anchor = settings.get("source_subtitle_box")
        if (
            settings.get("preserve_source_english")
            and settings.get("footer_height", 0) == 0
            and anchor is None
        ):
            boxes = [
                box
                for cue in value(captions["captions"])["cues"]
                for box in cue["boxes"]
            ]
            if not boxes:
                raise ValueError(
                    "Cannot place Chinese relative to English without subtitle boxes"
                )
            anchor = [
                int(min(b[0] for b in boxes)),
                int(min(b[1] for b in boxes)),
                int(max(b[2] for b in boxes)),
                int(max(b[3] for b in boxes)),
            ]
        layout = run(
            "layout",
            "N17",
            "bilingual",
            {
                "timeline": timeline["timeline"],
                "utterances": utterances["utterances"],
                "translations": translated,
                "video": c["video"],
            },
            {
                "font_size": settings.get("font_size", 24),
                "preserve_source_english": settings.get(
                    "preserve_source_english", False
                ),
                "footer_height": settings.get("footer_height", 0),
                "source_subtitle_box": anchor,
            },
        )
        render = run(
            "render",
            "N18",
            "dub_ffmpeg",
            {
                "video": c["video"],
                "audio": c["audio"],
                "timeline": timeline["timeline"],
                "dubs": audios,
                "layout": layout["layout"],
                "overlays": layout["overlays"],
            },
        )
        review = run(
            "review",
            "N19",
            "review",
            {
                "render": render["master"],
                "render_qa": render["qa"],
                "layout": layout["layout"],
                "audio_qa": qas,
            },
        )
        result = {
            "run_id": identity,
            "state": "REVIEW",
            "executions": executions,
            "units": unit_evidence,
            "timeline": timeline,
            "layout": layout,
            "render": render,
            "review": review,
        }
        atomic_json(directory / "result.json", result)
        engine.db.query(
            "UPDATE runs SET state='REVIEW',finished_at=UTC_TIMESTAMP(6) WHERE id=%s",
            (identity,),
        )
        return result
    except BaseException as error:
        atomic_json(
            directory / "failure.json",
            {"error": str(error), "executions": list(executions)},
        )
        engine.db.query(
            "UPDATE runs SET state='FAILED',finished_at=UTC_TIMESTAMP(6) WHERE id=%s",
            (identity,),
        )
        raise
