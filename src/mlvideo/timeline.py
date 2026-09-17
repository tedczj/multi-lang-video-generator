from fractions import Fraction as F
from math import ceil
from bisect import bisect_right


def quantize(t, sr=48000):
    x = F(t) * sr
    return (2 * x.numerator + x.denominator) // (2 * x.denominator)


def frame_sample(clock, frame):
    if "frame_samples" in clock:
        return clock["frame_samples"][frame]
    return quantize(F(frame) / F(clock["fps"]))


def cut_frame(clock, sample):
    if "frame_samples" in clock:
        return bisect_right(clock["frame_samples"], sample) - 1
    return int(F(sample, 48000) * F(clock["fps"]))


def plan(canonical, utterances):
    fps = F(canonical["fps"])
    sr = canonical["sample_rate"]
    total = canonical["source_frames"]
    samples = canonical["source_samples"]
    native = "frame_samples" in canonical

    def time_at(frame):
        return F(frame_sample(canonical, frame), sr) if native else F(frame) / fps

    def added(delay):
        return quantize(F(delay) / fps) if native else quantize(F(total + delay) / fps) - samples

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
            else time_at(total)
        )
        cut = u["safe_cut_frame"]
        # The supplied cut must be the LAST safe integral boundary.
        if (
            type(cut) is not int
            or cut != min(cut_frame(canonical, quantize(next_start)) if native else int(next_start * fps), u.get("visual_cut_limit_frame", total))
            or not 0 <= cut <= total
            or not previous_end <= start < end <= time_at(cut) <= next_start
        ):
            raise ValueError("Overlap or no last safe whole-frame cut; regroup/review")
        n = u["dub_samples"]
        if type(n) is not int or n <= 0:
            raise ValueError("Dub must contain positive sample frames")
        added_samples = added(delay)
        ds = u["end_sample"] + added_samples
        cut_time = time_at(cut) + (F(added_samples, sr) if native else F(delay) / fps)
        hold = max(0, ceil((F(ds + n, sr) - cut_time) * fps))
        # Absolute boundaries keep rounding errors bounded; never round segments independently.
        while (
            quantize(time_at(cut)) + added(delay + hold)
            < ds + n
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
            sample_cursor = quantize(time_at(p["source_end_frame"])) + added(added_frames)
        else:
            added_frames += p["output_end_frame"] - p["output_start_frame"]
            sample_cursor = (
                quantize(time_at(p["at_source_frame"])) + added(added_frames)
            )
        p["output_end_sample"] = sample_cursor
    result = {
        "fps": str(fps),
        "sample_rate": sr,
        "source_frames": total,
        "source_samples": samples,
        "output_frames": total + delay,
        "output_samples": samples + added(delay),
        "pieces": pieces,
        "dubs": dubs,
        "utterances": units,
        "quality_status": "REVIEW",
        "audio_gap_policy": "immediate_en_zh_v1",
    }
    if native:
        output_pts = []
        held = 0
        for p in pieces:
            if p["kind"] == "source":
                output_pts.extend(frame_sample(canonical, i) + added(held)
                                  for i in range(p["source_start_frame"], p["source_end_frame"]))
            else:
                count = p["output_end_frame"] - p["output_start_frame"]
                output_pts.extend(frame_sample(canonical, p["at_source_frame"]) + added(held + i)
                                  for i in range(count))
                held += count
        result["frame_samples"] = output_pts + [result["output_samples"]]
        result["source_frame_samples"] = canonical["frame_samples"]
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
        or t["output_samples"] != (t["frame_samples"][-1] if "frame_samples" in t else quantize(F(out) / fps))
    ):
        raise ValueError("Incomplete media coverage")
    if "frame_samples" in t:
        for key, count, end in (("frame_samples", out, t["output_samples"]),
                                ("source_frame_samples", src, t["source_samples"])):
            pts = t[key]
            if len(pts) != count + 1 or pts[0] < 0 or pts[-1] != end or any(b <= a for a, b in zip(pts, pts[1:])):
                raise ValueError("Invalid original frame timestamps")
    immediate = t.get("audio_gap_policy") == "immediate_en_zh_v1"
    minimum_gap = 0 if immediate else 48000
    for i, (u, d) in enumerate(zip(t["utterances"], t["dubs"], strict=True)):
        following = (
            t["utterances"][i + 1]["output_start_sample"]
            if i + 1 < len(t["utterances"])
            else t["output_samples"]
        )
        if (
            d["start_sample"] - u["output_end_sample"] < minimum_gap
            or (immediate and d["start_sample"] != u["output_end_sample"])
            or following - d["start_sample"] - d["samples"] < minimum_gap
        ):
            raise ValueError("Pre/post gap too short")
