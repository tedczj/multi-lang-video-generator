import jsonschema
from .config import ROOT
from .util import read_json, confined


def validate(schema_id, value):
    schema = ROOT / "schemas" / (schema_id + ".json")
    if not schema.exists():
        raise ValueError(f"Unknown schema {schema_id}")
    jsonschema.Draft202012Validator(read_json(schema)).validate(value)


def validate_output(work, item):
    path = confined(work, item["path"])
    if not path.is_file():
        raise ValueError("Artifact is not a regular file")
    if item["schema_id"] not in {"Video.v1", "Audio.v1", "Binary.v1"}:
        validate(item["schema_id"], read_json(path))
    return path


MULTI_INPUT_PORTS = {
    ("N16", "dub_gap_first", "clips"),
    ("N16", "dub_gap_first", "raw_clips"),
    ("N16", "dub_gap_first", "translations"),
    ("N17", "bilingual", "translations"),
    ("N18", "dub_ffmpeg", "dubs"),
    ("N19", "review", "audio_qa"),
}


# Ports and output schemas are fixed; strategy parameters cannot replace worker argv.
STRATEGIES = {
    ("N08", "reviewed_speech"): (
        {"audio": "Audio.v1", "speech": "SpeechTrack.v1", "speakers": "SpeakerTrack.v1", "vad": "Binary.v1"},
        {"speech": "SpeechTrack.v1", "speakers": "SpeakerTrack.v1", "review": "Binary.v1"},
        {"decision": None},
    ),
    ("N08", "caption_pages"): (
        {
            "speech": "SpeechTrack.v1",
            "captions": "CaptionTrack.v1",
            "canonical": "CanonicalMedia.v1",
            "vad": "Binary.v1",
        },
        {"utterances": "UtteranceSet.v1"},
        {"pages": []},
    ),
    ("N10", "speaker_bank"): (
        {
            "audio": "Audio.v1",
            "speech": "SpeechTrack.v1",
            "speakers": "SpeakerTrack.v1",
        },
        {"bank": "SpeakerBank.v1", "references": "Binary.v1"},
        {},
    ),
    ("N10", "from_speaker_bank"): (
        {"audio": "Audio.v1", "speech": "SpeechTrack.v1", "bank": "SpeakerBank.v1"},
        {"reference": "VoiceReference.v2", "audio": "Audio.v1"},
        {"speaker_id": "", "candidate_id": "", "review": None},
    ),
    ("N07", "speaker_diarization"): (
        {"audio": "Audio.v1"},
        {"speakers": "SpeakerTrack.v1", "raw": "Binary.v1", "receipt": "Binary.v1"},
        {"cluster_threshold": 0.5},
    ),
    ("N04", "audio_source"): (
        {"source": "Binary.v1", "probe": "MediaProbe.v1"},
        {"audio": "Audio.v1", "raw": "Audio.v1", "receipt": "Binary.v1"},
        {},
    ),
    ("N04", "excerpt"): (
        {"source": "Binary.v1", "probe": "MediaProbe.v1"},
        {
            "video": "Video.v1",
            "audio": "Audio.v1",
            "canonical": "CanonicalMedia.v1",
            "excerpt": "Binary.v1",
        },
        {"start_seconds": 0, "end_seconds": 0, "height": 720},
    ),
    ("N05", "inventory"): (
        {"video": "Video.v1", "probe": "MediaProbe.v1"},
        {"inventory": "SubtitleInventory.v1", "frame": "Binary.v1"},
        {"roi": [0, 0.5, 1, 1]},
    ),
    ("N06", "captions"): (
        {
            "source": "Binary.v1",
            "video": "Video.v1",
            "canonical": "CanonicalMedia.v1",
            "inventory": "SubtitleInventory.v1",
        },
        {"captions": "CaptionTrack.v1", "raw": "Binary.v1"},
        {"mode": "ocr", "stride_frames": 25},
    ),
    ("N07", "whisper"): (
        {"audio": "Audio.v1"},
        {
            "speech": "SpeechTrack.v1",
            "raw": "Binary.v1",
            "vad": "Binary.v1",
            "receipt": "Binary.v1",
        },
        {"annotation": None},
    ),
    ("N08", "group"): (
        {
            "speech": "SpeechTrack.v1",
            "captions": "CaptionTrack.v1",
            "canonical": "CanonicalMedia.v1",
        },
        {"utterances": "UtteranceSet.v1"},
        {"text_source": "speech"},
    ),
    ("N09", "codex"): (
        {"utterances": "UtteranceSet.v1"},
        {
            "translation": "TranslationSet.v1",
            "batch": "TranslationBatch.v1",
            "receipt": "TranslationReceipt.v1",
            "events": "Binary.v1",
            "response": "Binary.v1",
            "prompt": "Binary.v1",
        },
        {"batch_index": 0},
    ),
    ("N10", "reference"): (
        {"audio": "Audio.v1", "speech": "SpeechTrack.v1"},
        {"reference": "VoiceReference.v1", "audio": "Audio.v1"},
        {"speaker_id": "", "start_sample": 0, "end_sample": 0, "transcript": ""},
    ),
    ("N16", "dub_gap_first"): (
        {
            "canonical": "CanonicalMedia.v1",
            "utterances": "UtteranceSet.v1",
            "clips": "DubClip.v1",
            "raw_clips": "RawDubClip.v1",
            "translations": "TranslationSet.v1",
        },
        {"timeline": "TimelinePlan.v1"},
        {},
    ),
    ("N17", "bilingual"): (
        {
            "timeline": "TimelinePlan.v1",
            "utterances": "UtteranceSet.v1",
            "translations": "TranslationSet.v1",
            "video": "Video.v1",
        },
        {"layout": "SubtitleLayout.v1", "overlays": "Binary.v1", "font": "Binary.v1"},
        {
            "font_size": 24,
            "preserve_source_english": False,
            "footer_height": 0,
            "source_subtitle_box": None,
        },
    ),
    ("N18", "dub_ffmpeg"): (
        {
            "video": "Video.v1",
            "audio": "Audio.v1",
            "timeline": "TimelinePlan.v1",
            "dubs": "Audio.v1",
            "layout": "SubtitleLayout.v1",
            "overlays": "Binary.v1",
        },
        {
            "master": "Video.v1",
            "audio": "Audio.v1",
            "preview": "Video.v1",
            "qa": "QAReport.v1",
        },
        {},
    ),
    ("N19", "review"): (
        {
            "render": "Video.v1",
            "render_qa": "QAReport.v1",
            "layout": "SubtitleLayout.v1",
            "audio_qa": "AudioQA.v1",
        },
        {"qa": "QAReport.v1", "decision": "ReviewDecision.v1"},
        {"human_decision": None},
    ),
    ("N11", "cosyvoice3_from_bank"): (
        {
            "translation": "TranslationSet.v1",
            "reference": "VoiceReference.v2",
            "audio": "Audio.v1",
        },
        {
            "audio": "Audio.v1",
            "clip": "RawDubClip.v1",
            "receipt": "ModelReceipt.v1",
            "worker_source": "Binary.v1",
        },
        {"unit_id": "", "seed": 42},
    ),
    ("N11", "cosyvoice3_zero_shot"): (
        {
            "translation": "TranslationSet.v1",
            "reference": "VoiceReference.v1",
            "audio": "Audio.v1",
        },
        {
            "audio": "Audio.v1",
            "clip": "RawDubClip.v1",
            "receipt": "ModelReceipt.v1",
            "worker_source": "Binary.v1",
        },
        {"unit_id": "", "seed": 42},
    ),
    ("N12", "audio_qa_asr"): (
        {"audio": "Audio.v1", "clip": "RawDubClip.v1"},
        {
            "audio": "Audio.v1",
            "clip": "DubClip.v1",
            "qa": "AudioQA.v1",
            "asr": "Binary.v1",
        },
        {"leading_review_seconds": 1.0},
    ),
    ("N12", "audio_qa"): (
        {"audio": "Audio.v1", "clip": "RawDubClip.v1"},
        {"audio": "Audio.v1", "clip": "DubClip.v1", "qa": "AudioQA.v1"},
        {"leading_review_seconds": 1.0},
    ),
    ("N02", "source"): (
        {},
        {"source": "Binary.v1", "receipt": "AcquisitionReceipt.v1"},
        {"source_path": "", "receipt_path": ""},
    ),
    ("N03", "ffprobe"): ({"source": "Binary.v1"}, {"probe": "MediaProbe.v1"}, {}),
    ("N04", "ffmpeg"): (
        {"source": "Binary.v1", "probe": "MediaProbe.v1"},
        {"video": "Video.v1", "audio": "Audio.v1", "canonical": "CanonicalMedia.v1"},
        {"fps": None},
    ),
    ("N16", "gap_first"): (
        {"canonical": "CanonicalMedia.v1"},
        {"timeline": "TimelinePlan.v1"},
        {"utterances": []},
    ),
    ("N18", "ffmpeg"): (
        {"video": "Video.v1", "audio": "Audio.v1", "timeline": "TimelinePlan.v1"},
        {
            "master": "Video.v1",
            "audio": "Audio.v1",
            "preview": "Video.v1",
            "qa": "QAReport.v1",
        },
        {},
    ),
    ("TEST", "fixture_a"): (
        {},
        {"result": "Fixture.v1"},
        {"text": "fixture", "sleep": 0, "fail": False, "fill_bytes": 0},
    ),
    ("TEST", "fixture_b"): (
        {},
        {"result": "Fixture.v1"},
        {"text": "fixture", "sleep": 0, "fail": False, "fill_bytes": 0},
    ),
}


# Versioned manual-review studio strategies. Existing node meanings stay unchanged.
STRATEGIES.update({
    ("N06", "studio_captions"): (
        {"speech": "SpeechTrack.v1", "inventory": "SubtitleInventory.v1"},
        {"captions": "CaptionTrack.v1"}, {},
    ),
    ("N08", "studio_utterances"): (
        {"audio": "Audio.v1", "speech": "SpeechTrack.v1", "canonical": "CanonicalMedia.v1",
         "captions": "CaptionTrack.v1", "vad": "Binary.v1"},
        {"utterances": "UtteranceSet.v1", "review": "Binary.v1"},
        {"items": [], "reviewer": "", "reason": "", "require_coverage": True},
    ),
    ("N09", "studio_translation"): (
        {"utterances": "UtteranceSet.v1"},
        {"translation": "TranslationSet.v1", "review": "Binary.v1"},
        {"batch_index": 0, "items": [], "reviewer": "", "reason": ""},
    ),
    ("N09", "studio_sample"): (
        {"reference": "VoiceReference.v1"}, {"translation": "TranslationSet.v1"},
        {"unit_id": "", "text": ""},
    ),
    ("N10", "studio_reference"): (
        {"audio": "Audio.v1", "speech": "SpeechTrack.v1"},
        {"reference": "VoiceReference.v1", "audio": "Audio.v1", "review": "Binary.v1"},
        {"start_sample": 0, "end_sample": 0, "transcript": "", "character_id": "",
         "annotations": [], "reviewer": "", "reason": ""},
    ),
    ("N10", "studio_import_reference"): (
        {"reference": "VoiceReference.v1", "audio": "Audio.v1"},
        {"reference": "VoiceReference.v1", "audio": "Audio.v1", "receipt": "Binary.v1"},
        {"profile_id": "", "profile_sha512": "", "purpose": "dubbing"},
    ),
})


def strategy(node, name):
    try:
        return STRATEGIES[(node, name)]
    except KeyError:
        raise ValueError(f"Unknown strategy {node}/{name}") from None
