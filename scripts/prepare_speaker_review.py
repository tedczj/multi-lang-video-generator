"""Export an auditable speaker/reference listening packet without approving it."""

import argparse
import html
import shutil
import wave
from pathlib import Path

from mlvideo.config import load
from mlvideo.db import DB
from mlvideo.engine import Engine
from mlvideo.phase2_pipeline import ports
from mlvideo.speaker_review import bank_readiness
from mlvideo.util import atomic_json, code_provenance, read_json, sha512


def prepare(engine, asset, identities, out):
    out.mkdir(parents=True, exist_ok=False)
    refs = {k: engine.artifact(v, asset) for k, v in identities.items()}
    values = {k: read_json(Path(r["path"])) for k, r in refs.items() if k != "audio"}
    speech, track, bank = (values[k] for k in ("speech", "speakers", "bank"))
    if (
        speech["audio_artifact_id"] != identities["audio"]
        or track["source_audio_artifact_id"] != identities["audio"]
        or bank["source_audio_artifact_id"] != identities["audio"]
        or bank["speech_artifact_id"] != identities["speech"]
        or bank["speaker_track_artifact_id"] != identities["speakers"]
    ):
        raise ValueError("Review packet analysis/bank source mismatch")
    atomic_json(out / "source.json", code_provenance(out / "source.snapshot.zip"))
    atomic_json(out / "bindings.json", refs)
    for key, value in values.items():
        atomic_json(out / (key + ".json"), value)
    status = bank_readiness(bank)
    status["storage_free_bytes"] = shutil.disk_usage(engine.root).free
    status["grouping_review"] = "PENDING"
    status["voice_acceptance"] = "NOT_RUN"
    atomic_json(out / "readiness.json", status)
    checks = lambda names: [{"id": k, "status": "REVIEW", "reason": ""} for k in names]
    decision = {
        k + "_sha512": refs[k]["sha512"] for k in ("audio", "speech", "speakers", "vad")
    }
    decision.update(
        reviewer=None,
        checks=checks(("speaker_identity", "speech_boundaries", "transcript")),
        segments=[
            {k: row[k] for k in ("start_sample", "end_sample", "speaker_id", "text")}
            for row in speech["segments"]
        ],
    )
    atomic_json(out / "speaker-decision.pending.json", decision)
    excerpts = out / "audio"
    excerpts.mkdir()
    body = [
        "<!doctype html><meta charset='utf-8'><title>说话人及参考音复核</title>",
        "<style>body{font:16px system-ui;max-width:1100px;margin:32px auto;line-height:1.6}td,th{padding:8px;text-align:left;vertical-align:top}table{border-collapse:collapse}tr{border-bottom:1px solid #ddd}audio{width:320px}</style>",
        "<h1>说话人及参考音复核 · 待确认</h1><p>以下是声学分组估计，不代表实际角色人数。参考候选尚未通过人声纯净度、转录或音色验收。待填写的 JSON 不含自动批准。</p>",
    ]
    media = []
    from mlvideo.speaker_bank import write_reference

    with wave.open(refs["audio"]["path"]) as source:
        for speaker in bank["speakers"]:
            sid = speaker["speaker_id"]
            body.append(
                f"<h2>{html.escape(sid)} · {html.escape(speaker['status'])}</h2>"
            )
            turns = [t for t in track["turns"] if t["speaker_id"] == sid]
            body.append(
                "<p>全片出现位置（秒）："
                + ", ".join(
                    f"{t['start_sample'] / 48000:.2f}–{t['end_sample'] / 48000:.2f}"
                    for t in turns
                )
                + "</p>"
            )
            body.append(
                "<table><tr><th>来源 / 时间</th><th>试听</th><th>转录估计</th></tr>"
            )
            for i, turn in enumerate(
                sorted(
                    turns,
                    key=lambda t: t["end_sample"] - t["start_sample"],
                    reverse=True,
                )[:3]
            ):
                name = f"group-{len(media):04d}.wav"
                end = min(turn["end_sample"], turn["start_sample"] + 6 * 48000)
                write_reference(
                    source, [turn | {"end_sample": end}], 0, excerpts / name
                )
                text = " ".join(
                    s["text"]
                    for s in speech["segments"]
                    if s["start_sample"] < end
                    and s["end_sample"] > turn["start_sample"]
                )
                media.append(
                    {
                        "file": "audio/" + name,
                        "sha512": sha512(excerpts / name),
                        "speaker_id": sid,
                        "start_sample": turn["start_sample"],
                        "end_sample": end,
                        "kind": "group_excerpt",
                    }
                )
                body.append(
                    f"<tr><td>分组片段 {i + 1}<br>{turn['start_sample'] / 48000:.2f}–{end / 48000:.2f}s</td><td><audio controls preload='none' src='audio/{name}'></audio></td><td>{html.escape(text)}</td></tr>"
                )
            for candidate in speaker["candidates"]:
                name = f"reference-{len(media):04d}.wav"
                write_reference(
                    source,
                    candidate["segments"],
                    candidate["gap_samples"],
                    excerpts / name,
                )
                if sha512(excerpts / name) != candidate["audio_sha512"]:
                    raise ValueError("Reconstructed listening reference hash mismatch")
                media.append(
                    {
                        "file": "audio/" + name,
                        "sha512": candidate["audio_sha512"],
                        "speaker_id": sid,
                        "candidate_id": candidate["candidate_id"],
                        "kind": "reference",
                    }
                )
                pending = {
                    k: candidate[k]
                    for k in ("speaker_id", "candidate_id", "audio_sha512")
                }
                pending.update(
                    bank_sha512=refs["bank"]["sha512"],
                    reviewer=None,
                    checks=checks(
                        (
                            "speaker_identity",
                            "transcript",
                            "clean_reference",
                            "complete_words",
                        )
                    ),
                )
                atomic_json(out / f"reference-{len(media):04d}.pending.json", pending)
                body.append(
                    f"<tr><td>参考候选<br>{candidate['duration_seconds']:.2f}s</td><td><audio controls preload='none' src='audio/{name}'></audio></td><td>{html.escape(candidate['text'])}</td></tr>"
                )
            body.append("</table>")
    (out / "index.html").write_text("\n".join(body), encoding="utf-8")
    atomic_json(out / "media-manifest.json", media)
    return status


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--asset", required=True)
    p.add_argument("--analysis", required=True)
    p.add_argument("--diarization", required=True)
    p.add_argument("--bank", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    analysis = read_json(Path(args.analysis))
    identities = {
        "audio": ports(analysis["audio"])["audio"],
        "speech": ports(analysis["speech"])["speech"],
        "vad": ports(analysis["speech"])["vad"],
        "speakers": ports(read_json(Path(args.diarization)))["speakers"],
        "bank": ports(read_json(Path(args.bank)))["bank"],
    }
    config = load(args.config)
    db = DB(config)
    try:
        print(
            prepare(
                Engine(db, config), args.asset, identities, Path(args.out).resolve()
            )
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
