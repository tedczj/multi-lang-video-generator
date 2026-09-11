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


# Ports and output schemas are fixed; strategy parameters cannot replace worker argv.
STRATEGIES = {
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


def strategy(node, name):
    try:
        return STRATEGIES[(node, name)]
    except KeyError:
        raise ValueError(f"Unknown strategy {node}/{name}") from None
