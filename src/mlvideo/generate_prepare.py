"""Turn script candidates into safe audio groups and source-bound voice references."""

import bisect
import subprocess
import wave
from pathlib import Path

from .util import atomic_json, sha512


def safe_groups(script, canonical, vad):
    turns = [(x["start"] * 3, x["end"] * 3) for x in vad["intervals"]]
    units = []
    for original in script["items"]:
        u = dict(original)
        matched = [
            (a, b) for a, b in turns if a < u["end_sample"] and b > u["start_sample"]
        ]
        if matched:
            u["start_sample"] = min(u["start_sample"], min(a for a, b in matched))
            u["end_sample"] = max(u["end_sample"], max(b for a, b in matched))
        if units and u["start_sample"] <= units[-1]["end_sample"]:
            prior = units[-1]
            prior["parts"] = prior["parts"] + u["parts"]
            prior["text"] += u["text"]
            prior["source_text"] += " " + u["source_text"]
            prior["end_sample"] = max(prior["end_sample"], u["end_sample"])
        else:
            units.append(u)
    pts = canonical["frame_samples"]
    cursor = 0
    groups = []
    for i, u in enumerate(units):
        end = (
            bisect.bisect_right(pts, units[i + 1]["start_sample"]) - 1
            if i + 1 < len(units)
            else len(pts) - 1
        )
        if end <= cursor or pts[end] < u["end_sample"]:
            raise ValueError("No safe full-frame speech boundary; review/regroup")
        groups.append(
            u
            | {
                "start_frame": cursor,
                "end_frame": end,
                "hold_frame": end - 1,
                "speech_end_sample": u["end_sample"],
            }
        )
        cursor = end
    if not groups or cursor != len(pts) - 1:
        raise ValueError("Incomplete source grouping")
    return groups


def references(script, audio, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    parts = [p for u in script["items"] for p in u["parts"]]
    roles = sorted({p["speaker"] for p in parts})
    result = {}
    with wave.open(str(audio)) as src:
        total = src.getnframes()
        for number, role in enumerate(roles):
            candidates = [
                p
                for p in parts
                if p["speaker"] == role and p["end_sample"] - p["start_sample"] >= 14400
            ]
            if not candidates:
                raise ValueError(
                    f"No source reference for {role}; no other speaker may substitute"
                )
            part = max(
                candidates,
                key=lambda p: min(p["end_sample"] - p["start_sample"], 480000),
            )
            words = script["words"][part["start_word"] : part["end_word"]]
            selected = []
            for w in words:
                if (
                    selected
                    and w["end_sample"] - selected[0]["start_sample"] > 12 * 48000
                ):
                    break
                selected.append(w)
            a = max(0, selected[0]["start_sample"] - 4800)
            b = min(total, selected[-1]["end_sample"] + 7200)
            # Padding may include room tone, but never another explicitly assigned part.
            for other in parts:
                if other is part:
                    continue
                if other["end_sample"] <= selected[0]["start_sample"]:
                    a = max(a, other["end_sample"])
                if other["start_sample"] >= selected[-1]["end_sample"]:
                    b = min(b, other["start_sample"])
            if b - a < 14400:
                raise ValueError(
                    "Reference shorter than 0.3s; human source selection required"
                )
            raw = directory / f"role-{number:03d}.pcm.wav"
            dst = directory / f"role-{number:03d}.wav"
            src.setpos(a)
            with wave.open(str(raw), "wb") as out:
                out.setparams((2, 2, 48000, 0, "NONE", ""))
                out.writeframes(src.readframes(b - a))
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-i",
                    str(raw),
                    "-ar",
                    "24000",
                    "-ac",
                    "1",
                    str(dst),
                ],
                check=True,
            )
            result[role] = {
                "audio": str(dst.resolve()),
                "audio_sha512": sha512(dst),
                "source_audio_sha512": sha512(audio),
                "source_start_sample": a,
                "source_end_sample": b,
                "text": "".join(w["text"] for w in selected).strip(),
                "role_evidence_part": part["id"],
                "short_reference": b - a < 3 * 48000,
                "quality_status": "REVIEW",
            }
    atomic_json(directory / "references.json", result)
    return result


def reuse_voices(previous, directory, model, items, refs, worker):
    """Resume a revised job only when all actual synthesis inputs are identical."""
    import json
    import shutil

    from .util import digest

    previous, directory = Path(previous), Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if not (previous / "environment.json").exists():
        return 0
    old_request = json.loads((previous / "request.json").read_text())
    environment = json.loads((previous / "environment.json").read_text())
    worker_hash = sha512(worker)
    if old_request["model"] != model or environment["worker_sha512"] != worker_hash:
        return 0
    prior = [json.loads(p.read_text()) for p in previous.glob("u*.json")]
    count = 0
    for item in items:
        ref = refs[item["speaker"]]
        old_ref = old_request["references"].get(item["speaker"])
        if (
            not old_ref
            or old_ref["text"] != ref["text"]
            or old_ref["audio_sha512"] != ref["audio_sha512"]
        ):
            continue
        found = next(
            (
                r
                for r in prior
                if r["speaker"] == item["speaker"]
                and r["text"] == item["translation"]
                and r["reference_sha512"] == ref["audio_sha512"]
            ),
            None,
        )
        if not found:
            continue
        source = previous / f"{found['id']}.raw.wav"
        if sha512(source) != found["audio_sha512"]:
            raise ValueError("Reusable voice changed")
        target = directory / f"{item['id']}.raw.wav"
        shutil.copyfile(source, target)
        record = found | {
            "id": item["id"],
            "binding": digest([model, item, ref, worker_hash]),
            "reused_from": str(source),
            "prior_receipt_sha512": sha512(previous / f"{found['id']}.json"),
        }
        atomic_json(directory / f"{item['id']}.json", record)
        count += 1
    return count


def review_summary(work, render_result):
    from .util import read_json

    work = Path(work)
    timeline = read_json(Path(render_result["work"]) / "timeline.json")
    qa = read_json(work / "joined/audio-qa.json")
    asr = read_json(work / "check-zh/report.json")
    refs = read_json(work / "references-final/references.json")
    units = timeline["units"]
    positions = {}
    for unit in units:
        cursor = unit["chinese_start_sample"]
        for part in unit["parts"]:
            positions[part["id"]] = cursor / 48000
            with wave.open(str(work / "joined" / f"{part['id']}.wav")) as wav:
                cursor += wav.getnframes()
    report = {
        "status": "REVIEW",
        "automated_checks": render_result["automated_checks"],
        "output": render_result["output"],
        "output_seconds": render_result["seconds"],
        "units": len(units),
        "voice_parts": len(asr["items"]),
        "subtitle_pages": len(timeline["pages"]),
        "added_english_chinese_gap_samples": 0,
        "slow_motion": [
            {
                "unit": u["id"],
                "source_text": u["source_text"],
                "output_seconds": u["output_start_sample"] / 48000,
                "speed": u["video_speed"],
            }
            for u in units
            if u["id"] in timeline["motion_review_units"]
        ],
        "leading_energy_review": [
            {
                "part": r["id"],
                "output_seconds": positions[r["id"]],
                "energy_onset_seconds": r["qa"]["energy_onset_seconds"],
            }
            for r in qa
            if r["qa"]["energy_onset_seconds"] is None
            or r["qa"]["energy_onset_seconds"] > 0.25
        ],
        "asr_empty_review": [
            {
                "part": r["id"],
                "expected": r["expected"],
                "output_seconds": positions[r["id"]],
            }
            for r in asr["items"]
            if not r["recognized"].strip()
        ],
        "short_reference_roles": [
            role for role, ref in refs.items() if ref["short_reference"]
        ],
        "discarded_asr_candidates": read_json(work / "script-final/script.json")[
            "discarded"
        ],
        "speaker_identity": "Automatic semantic roles and references; not human-approved",
        "human_listening": "REVIEW",
        "formal_acceptance": "REVIEW",
    }
    atomic_json(work / "review.json", report)
    return report
