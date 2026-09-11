from fractions import Fraction as F
import random
import pytest
from mlvideo.timeline import plan, quantize


def canonical(fps="25", total=125):
    return {
        "fps": fps,
        "sample_rate": 48000,
        "source_frames": total,
        "source_samples": quantize(F(total) / F(fps)),
    }


@pytest.mark.parametrize(
    "end,cut,total,expected",
    [
        (48000, 125, 125, 0),
        (48000, 50, 50, 38),
        (48000, 25, 25, 63),
        (48000, 75, 75, 13),
    ],
)
def test_independent_hold_counts(end, cut, total, expected):
    t = plan(
        canonical(total=total),
        [
            {
                "id": "u",
                "start_sample": 0,
                "end_sample": end,
                "safe_cut_frame": cut,
                "dub_samples": 24000,
            }
        ],
    )
    assert t["utterances"][0]["hold_frames"] == expected
    assert t["dubs"][0]["start_sample"] == 96000


def test_reject_unsafe_overlap_duplicate():
    u = {
        "id": "u",
        "start_sample": 0,
        "end_sample": 48000,
        "safe_cut_frame": 25,
        "dub_samples": 24000,
    }
    for units in (
        [u],
        [u, u],
        [u | {"safe_cut_frame": 125}, u | {"id": "v", "start_sample": 24000}],
    ):
        with pytest.raises(ValueError):
            plan(canonical(), units)


def test_rational_quantization():
    assert quantize(F(1, 96000)) == 1
    rng = random.Random(42)
    for fps in ["30000/1001", "24000/1001", "25"]:
        for _ in range(100):
            total = rng.randrange(50, 500)
            c = canonical(fps, total)
            end = min(48000, c["source_samples"] // 2)
            t = plan(
                c,
                [
                    {
                        "id": "u",
                        "start_sample": 0,
                        "end_sample": end,
                        "safe_cut_frame": total,
                        "dub_samples": rng.randrange(1, 200000),
                    }
                ],
            )
            h = t["output_frames"] - total
            assert (
                t["output_samples"]
                >= t["dubs"][0]["start_sample"] + t["dubs"][0]["samples"] + 48000
            )
            if h:
                assert (
                    quantize(F(total + h - 1) / F(fps))
                    < t["dubs"][0]["start_sample"] + t["dubs"][0]["samples"] + 48000
                )


def test_fractional_hold_long_tail_preserves_every_source_sample():
    for total in range(200, 220):
        t = plan(
            canonical("30000/1001", total),
            [
                {
                    "id": "a",
                    "start_sample": 0,
                    "end_sample": 48000,
                    "safe_cut_frame": 59,
                    "dub_samples": 24000,
                },
                {
                    "id": "b",
                    "start_sample": 96000,
                    "end_sample": 100800,
                    "safe_cut_frame": total,
                    "dub_samples": 1,
                },
            ],
        )
        assert t["pieces"][-1]["output_end_sample"] == t["output_samples"]
        for p in t["pieces"]:
            if p["kind"] == "source":
                expected = int(
                    F(p["source_end_frame"] * 48000 * 1001, 30000) + F(1, 2)
                ) - int(F(p["source_start_frame"] * 48000 * 1001, 30000) + F(1, 2))
                assert p["output_end_sample"] - p["output_start_sample"] == expected
