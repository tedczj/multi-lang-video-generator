"""Collect source-bound, single-speaker reference candidates before synthesis."""

import array
import math
import re
import wave
import zipfile

from .util import atomic_json, digest, sha512


def collect_candidates(speech, track, min_seconds=6, max_seconds=20):
    turns = track["turns"]
    groups = []
    discarded = []
    for segment in speech["segments"]:
        start, end = segment["start_sample"], segment["end_sample"]
        matched = [
            t for t in turns if t["start_sample"] < end and t["end_sample"] > start
        ]
        labels = {t["speaker_id"] for t in matched}
        if len(labels) != 1 or segment["text"].strip().startswith("["):
            discarded.append(
                {
                    "speech_id": segment["id"],
                    "reason": "Unassigned, overlapping/multiple speakers, or non-speech transcript",
                }
            )
            continue
        # Union of intersections prevents duplicate turns from overstating coverage.
        cursor = start
        covered = 0
        for t in sorted(matched, key=lambda t: t["start_sample"]):
            a, b = max(cursor, start, t["start_sample"]), min(end, t["end_sample"])
            if a < b:
                covered += b - a
                cursor = b
        if covered / (end - start) < 0.8:
            discarded.append(
                {
                    "speech_id": segment["id"],
                    "reason": "Diarized speech covers less than 80 percent of ASR segment",
                }
            )
            continue
        speaker = next(iter(labels))
        text = segment["text"].strip()
        merge = (
            bool(groups)
            and groups[-1]["speaker_id"] == speaker
            and 0 <= start - groups[-1]["end_sample"] <= 72000
            and end - groups[-1]["start_sample"] <= max_seconds * 48000
        )
        if merge:
            prior = groups[-1]
            if any(
                t["speaker_id"] != speaker
                and t["start_sample"] < end
                and t["end_sample"] > prior["start_sample"]
                for t in turns
            ):
                merge = False
        if merge:
            groups[-1]["end_sample"] = end
            groups[-1]["text"] += " " + text
        else:
            groups.append(
                {
                    "speaker_id": speaker,
                    "start_sample": start,
                    "end_sample": end,
                    "text": text,
                }
            )
    return [
        g
        for g in groups
        if min_seconds * 48000
        <= g["end_sample"] - g["start_sample"]
        <= max_seconds * 48000
    ], discarded


def build_bank(audio, speech, track, refs, work):
    if (
        speech["audio_artifact_id"] != refs["audio"]
        or track["source_audio_artifact_id"] != refs["audio"]
    ):
        raise ValueError("Speaker bank source audio binding mismatch")
    groups, discarded = collect_candidates(speech, track, min_seconds=1)
    groups = [
        g
        for g in groups
        if not re.search(
            r"\b(said|asked|thought|wondered|yelled|shouted|gasped|cried|exclaimed|whispered|replied|called|muttered|screamed|commanded)\b",
            g["text"],
            re.IGNORECASE,
        )
    ]
    speakers = []
    with wave.open(str(audio)) as src:
        if (src.getframerate(), src.getnchannels(), src.getsampwidth()) != (
            48000,
            2,
            2,
        ):
            raise ValueError("Speaker bank requires 48 kHz stereo PCM16")
        for speaker in sorted({t["speaker_id"] for t in track["turns"]}):
            pool = [g for g in groups if g["speaker_id"] == speaker]
            # Keep short clean turns as a pool, with all original intervals explicit.
            padded = []
            for g in pool:
                item = {k: g[k] for k in ("start_sample", "end_sample", "text")}
                item["start_sample"] = max(0, item["start_sample"] - 9600)
                item["end_sample"] = min(src.getnframes(), item["end_sample"] + 19200)
                if not any(
                    t["speaker_id"] != speaker
                    and t["start_sample"] < item["end_sample"]
                    and t["end_sample"] > item["start_sample"]
                    for t in track["turns"]
                ):
                    padded.append(item)
            alternatives = [
                [g] for g in padded if g["end_sample"] - g["start_sample"] >= 6 * 48000
            ]
            pooled = []
            total = 0
            for g in sorted(
                padded, key=lambda g: g["end_sample"] - g["start_sample"], reverse=True
            ):
                n = g["end_sample"] - g["start_sample"]
                if total + n + 9600 * len(pooled) > 20 * 48000:
                    continue
                if any(
                    g["start_sample"] < x["end_sample"]
                    and g["end_sample"] > x["start_sample"]
                    for x in pooled
                ):
                    continue
                pooled.append(g)
                total += n
                if total >= 10 * 48000:
                    break
            if len(pooled) > 1 and total >= 6 * 48000:
                alternatives.append(sorted(pooled, key=lambda g: g["start_sample"]))

            def length(segments):
                return sum(
                    g["end_sample"] - g["start_sample"] for g in segments
                ) + 9600 * (len(segments) - 1)

            candidates = []
            for segments in sorted(
                alternatives, key=lambda x: (len(x) > 1, abs(length(x) / 48000 - 12))
            )[:3]:
                identity = "ref_" + digest([refs["audio"], speaker, segments])[:24]
                name = identity + ".wav"
                write_reference(src, segments, 9600, work / name)
                with wave.open(str(work / name)) as wav:
                    values = array.array("h", wav.readframes(wav.getnframes()))
                peak = max(abs(v) for v in values) / 32768
                rms = math.sqrt(sum(v * v for v in values) / len(values)) / 32768
                candidates.append(
                    {
                        "speaker_id": speaker,
                        "segments": segments,
                        "frames": length(segments),
                        "gap_samples": 9600,
                        "text": " ".join(g["text"] for g in segments),
                        "candidate_id": identity,
                        "audio_path": name,
                        "audio_sha512": sha512(work / name),
                        "duration_seconds": length(segments) / 48000,
                        "peak": peak,
                        "rms": rms,
                        "eligible": peak < 0.999 and rms > 0.001,
                    }
                )
            selected = next(
                (c["candidate_id"] for c in candidates if c["eligible"]), None
            )
            speakers.append(
                {
                    "speaker_id": speaker,
                    "collected_seconds": sum(
                        (g["end_sample"] - g["start_sample"]) / 48000 for g in pool
                    ),
                    "candidates": candidates,
                    "selected_candidate_id": selected,
                    "status": "REVIEW" if selected else "INSUFFICIENT_REFERENCE",
                }
            )
    bank = {
        "source_audio_artifact_id": refs["audio"],
        "speech_artifact_id": refs["speech"],
        "speaker_track_artifact_id": refs["speakers"],
        "speakers": speakers,
        "discarded": discarded,
        "quality_status": "REVIEW",
        "limitations": [
            "Speaker assignments and ASR transcripts are estimates",
            "RMS/peak checks do not prove absence of music, effects or vocal artifacts; listening required",
        ],
    }
    atomic_json(work / "bank.json", bank)
    with zipfile.ZipFile(work / "references.zip", "x", zipfile.ZIP_DEFLATED) as archive:
        for speaker in speakers:
            for candidate in speaker["candidates"]:
                archive.write(work / candidate["audio_path"], candidate["audio_path"])
    return bank


def select_reference(bank, speaker_id, candidate_id=""):
    rows = [s for s in bank["speakers"] if s["speaker_id"] == speaker_id]
    if len(rows) != 1:
        raise ValueError("Speaker is not uniquely in this bank")
    speaker = rows[0]
    identity = candidate_id or speaker["selected_candidate_id"]
    candidates = [
        c
        for c in speaker["candidates"]
        if c["candidate_id"] == identity and c["eligible"]
    ]
    if len(candidates) != 1:
        raise ValueError("Speaker has no eligible selected reference; do not clone")
    return candidates[0]


def write_reference(src, segments, gap_samples, destination):
    with wave.open(str(destination), "wb") as dst:
        dst.setparams(src.getparams())
        for index, segment in enumerate(segments):
            a, b = segment["start_sample"], segment["end_sample"]
            if not 0 <= a < b <= src.getnframes():
                raise ValueError("Reference source interval out of bounds")
            if index:
                dst.writeframes(
                    bytes(gap_samples * src.getnchannels() * src.getsampwidth())
                )
            src.setpos(a)
            dst.writeframes(src.readframes(b - a))
