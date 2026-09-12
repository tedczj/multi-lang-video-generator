from fractions import Fraction as F
from math import ceil


def quantize(t, sr=48000):
    x = F(t) * sr
    return (2 * x.numerator + x.denominator) // (2 * x.denominator)


def plan(canonical, utterances):
    fps = F(canonical["fps"])
    sr = canonical["sample_rate"]
    total = canonical["source_frames"]
    samples = canonical["source_samples"]
    if fps <= 0 or sr != 48000 or total < 1:
        raise ValueError("Invalid canonical clock")
    if len({u["id"] for u in utterances}) != len(utterances):
        raise ValueError("Duplicate utterance ID")
    pieces, dubs, units = [], [], []
    cursor = delay = out = 0
    previous_end = F(0)
    for i, u in enumerate(utterances):
        from .contracts import validate

        validate("UtteranceFixture.v1", u)
        start, end = F(u["start_sample"], sr), F(u["end_sample"], sr)
        next_start = (
            F(utterances[i + 1]["start_sample"], sr)
            if i + 1 < len(utterances)
            else F(total) / fps
        )
        cut = u["safe_cut_frame"]
        # The supplied cut must be the LAST safe integral boundary.
        if (
            type(cut) is not int
            or cut != min(int(next_start * fps), u.get("visual_cut_limit_frame", total))
            or not previous_end <= start < end <= F(cut) / fps <= next_start
            or cut > total
        ):
            raise ValueError("Overlap or no last safe whole-frame cut; regroup/review")
        n = u["dub_samples"]
        if type(n) is not int or n <= 0:
            raise ValueError("Dub must contain positive sample frames")
        added_samples = quantize(F(total + delay) / fps) - samples
        ds = u["end_sample"] + added_samples + sr
        hold = max(0, ceil((F(ds + n + sr, sr) - F(cut + delay) / fps) * fps))
        # Absolute boundaries keep rounding errors bounded; never round segments independently.
        while (
            quantize(F(cut) / fps) + quantize(F(total + delay + hold) / fps) - samples
            < ds + n + sr
        ):
            hold += 1
        if cut > cursor:
            pieces.append(
                {
                    "kind": "source",
                    "source_start_frame": cursor,
                    "source_end_frame": cut,
                    "output_start_frame": out,
                    "output_end_frame": out + cut - cursor,
                }
            )
            out += cut - cursor
        if hold:
            pieces.append(
                {
                    "kind": "hold",
                    "source_hold_frame": cut - 1,
                    "at_source_frame": cut,
                    "output_start_frame": out,
                    "output_end_frame": out + hold,
                }
            )
            out += hold
        units.append(
            u
            | {
                "hold_frames": hold,
                "output_start_sample": u["start_sample"] + added_samples,
                "output_end_sample": u["end_sample"] + added_samples,
            }
        )
        dubs.append(
            {
                "id": u["id"],
                "start_sample": ds,
                "samples": n,
                "tone_hz": u.get("tone_hz", 660),
            }
        )
        cursor, previous_end = cut, end
        delay += hold
    if cursor < total:
        pieces.append(
            {
                "kind": "source",
                "source_start_frame": cursor,
                "source_end_frame": total,
                "output_start_frame": out,
                "output_end_frame": out + total - cursor,
            }
        )
    added_frames = sample_cursor = 0
    for p in pieces:
        p["output_start_sample"] = sample_cursor
        if p["kind"] == "source":
            sample_cursor += quantize(F(p["source_end_frame"]) / fps) - quantize(
                F(p["source_start_frame"]) / fps
            )
        else:
            added_frames += p["output_end_frame"] - p["output_start_frame"]
            sample_cursor = (
                quantize(F(p["at_source_frame"]) / fps)
                + quantize(F(total + added_frames) / fps)
                - samples
            )
        p["output_end_sample"] = sample_cursor
    result = {
        "fps": str(fps),
        "sample_rate": sr,
        "source_frames": total,
        "source_samples": samples,
        "output_frames": total + delay,
        "output_samples": quantize(F(total + delay) / fps),
        "pieces": pieces,
        "dubs": dubs,
        "utterances": units,
        "quality_status": "REVIEW",
    }
    verify(result)
    return result


def verify(t):
    fps = F(t["fps"])
    src = out = 0
    for p in t["pieces"]:
        if p["output_start_frame"] != out:
            raise ValueError("Output gap")
        if p["kind"] == "source":
            if p["source_start_frame"] != src:
                raise ValueError("Source gap/reordering")
            size = p["source_end_frame"] - src
            src = p["source_end_frame"]
        elif p["kind"] == "hold":
            if p["source_hold_frame"] != src - 1 or p["at_source_frame"] != src:
                raise ValueError("Wrong hold frame")
            size = p["output_end_frame"] - out
        else:
            raise ValueError("Unknown piece kind")
        if size <= 0 or p["output_end_frame"] != out + size:
            raise ValueError("Invalid piece")
        out += size
    if (
        src != t["source_frames"]
        or out != t["output_frames"]
        or t["output_samples"] != quantize(F(out) / fps)
    ):
        raise ValueError("Incomplete media coverage")
    for i, (u, d) in enumerate(zip(t["utterances"], t["dubs"], strict=True)):
        following = (
            t["utterances"][i + 1]["output_start_sample"]
            if i + 1 < len(t["utterances"])
            else t["output_samples"]
        )
        if (
            d["start_sample"] - u["output_end_sample"] < 48000
            or following - d["start_sample"] - d["samples"] < 48000
        ):
            raise ValueError("Pre/post gap too short")
