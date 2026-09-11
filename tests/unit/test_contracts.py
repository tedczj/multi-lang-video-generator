import pytest
from mlvideo.contracts import validate, validate_output
from mlvideo.util import confined


def test_paths_and_schemas(tmp_path):
    (tmp_path / "good").write_text("x")
    (tmp_path / "link").symlink_to(tmp_path / "good")
    for name in ("../x", "/tmp/x", "link", ""):
        with pytest.raises(ValueError):
            confined(tmp_path, name)
    with pytest.raises(Exception):
        validate("Fixture.v1", {"text": "x", "fixture": True})
    with pytest.raises(ValueError):
        validate("unknown", {})
    with pytest.raises(Exception):
        validate_output(tmp_path, {"path": "good", "schema_id": "Fixture.v1"})
