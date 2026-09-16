import pytest

np = pytest.importorskip("numpy")
from mlvideo.reference_motion import compare


def test_known_slowdown_with_blank_and_ambiguous_frames():
    rng = np.random.default_rng(8)
    source = rng.normal(size=(500, 32)).astype(np.float32)
    source /= np.linalg.norm(source, axis=1, keepdims=True)
    reference = source[np.arange(500)]
    # Source at 10 fps, reference at 5 fps: visual speed exactly 0.5x.
    reference = reference.copy()
    reference[150:190] = 0
    rows, result = compare(source, reference, start=0)
    assert result["source_seconds_per_recording_second"] == pytest.approx(0.5)
    assert result["residual_p95_seconds"] < 1e-8
    assert rows[150]["correlation"] == 0
    assert all(np.isfinite(r["correlation"]) for r in rows)


def test_ambiguous_still_does_not_prove_motion():
    source = np.ones((300, 8), dtype=np.float32) / np.sqrt(8)
    with pytest.raises(ValueError, match="unambiguous"):
        compare(source, source, start=0)
