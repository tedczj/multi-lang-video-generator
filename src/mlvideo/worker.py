import argparse
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from . import media
from .contracts import validate
from .timeline import plan
from .util import read_json, atomic_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    r = read_json(Path(args.request))
    validate("WorkerRequest.v1", r)
    work = Path(r["output_dir"])
    p = r["params"]

    def source(port):
        return Path(r["inputs"][port][0]["path"])

    artifacts = []

    def output(port, path, schema, kind):
        artifacts.append(
            {"port": port, "path": path, "schema_id": schema, "kind": kind}
        )

    node = r["node"]
    if r["strategy_id"].startswith("studio_"):
        from .studio.nodes import run as studio_run

        studio_run(r, work, output)
    elif node in {"N05", "N06", "N07", "N08", "N09", "N10", "N17", "N19"} or r[
        "strategy_id"
    ] in {"dub_gap_first", "dub_ffmpeg", "excerpt", "audio_source"}:
        from .phase2 import run

        for values in r["inputs"].values():
            for ref in values:
                from .util import sha512

                if sha512(ref["path"]) != ref["sha512"]:
                    raise ValueError("Input hash changed")
        result_artifacts = run(r, work, output)
        if result_artifacts is not None:
            artifacts = result_artifacts
    elif node == "N11":
        from .config import ROOT

        if len(r["models"]) != 1:
            raise ValueError("Configure the CosyVoice3 model deployment first")
        validate("TranslationSet.v1", read_json(source("translation")))
        validate(
            r["inputs"]["reference"][0]["schema_id"], read_json(source("reference"))
        )
        subprocess.run(
            [
                r["models"][0]["python"],
                str(ROOT / "workers/cosyvoice_worker.py"),
                "--request",
                args.request,
                "--result",
                args.result,
            ],
            check=True,
        )
        return
    elif node == "N12":
        from .audio_qa import normalize_and_check

        clip = read_json(source("clip"))
        validate("RawDubClip.v1", clip)
        report, frames, audio_hash = normalize_and_check(
            source("audio"), clip, work, p["leading_review_seconds"]
        )
        atomic_json(
            work / "clip.json",
            {
                "unit_id": clip["unit_id"],
                "speaker_id": clip["speaker_id"],
                "raw_clip_artifact_id": r["inputs"]["clip"][0]["artifact_id"],
                "raw_audio_artifact_id": r["inputs"]["audio"][0]["artifact_id"],
                "audio_sha512": audio_hash,
                "sample_rate": 48000,
                "channels": 2,
                "frames": frames,
                "quality_status": report["overall"],
                "trimmed_samples": 0,
            },
        )
        output("audio", "normalized.wav", "Audio.v1", "dub_audio")
        output("clip", "clip.json", "DubClip.v1", "dub_clip")
        output("qa", "qa.json", "AudioQA.v1", "qa_report")
        if r["strategy_id"] == "audio_qa_asr":
            from .config import ROOT

            if len(r["models"]) != 1:
                raise ValueError("Configure N12/audio_qa_asr deployment")
            subprocess.run(
                [
                    r["models"][0]["python"],
                    str(ROOT / "workers/audio_content_worker.py"),
                    "--request",
                    args.request,
                    "--result",
                    str(work / "content.json"),
                ],
                check=True,
            )
            output("asr", "content.json", "Binary.v1", "audio_content_assessment")

    elif node == "N02":
        shutil.copyfile(p["source_path"], work / "source.bin")
        shutil.copyfile(p["receipt_path"], work / "receipt.json")
        output("source", "source.bin", "Binary.v1", "source")
        output("receipt", "receipt.json", "AcquisitionReceipt.v1", "receipt")
    elif node == "N03":
        atomic_json(work / "probe.json", media.probe(source("source"), work))
        output("probe", "probe.json", "MediaProbe.v1", "probe")
    elif node == "N04":
        if r["strategy_id"] == "original":
            media.preserve_source(source("source"), read_json(source("probe")), work)
            output("video", "original.bin", "Video.v1", "original_video")
        else:
            media.normalize(source("source"), read_json(source("probe")), work, p["fps"])
            output("video", "canonical.mkv", "Video.v1", "canonical_video")
        output("audio", "canonical.wav", "Audio.v1", "canonical_audio")
        output("canonical", "canonical.json", "CanonicalMedia.v1", "canonical")
    elif node == "N16":
        atomic_json(
            work / "timeline.json",
            plan(read_json(source("canonical")), p["utterances"]),
        )
        output("timeline", "timeline.json", "TimelinePlan.v1", "timeline")
    elif node == "N18":
        media.render(
            source("video"), source("audio"), read_json(source("timeline")), work
        )
        for port, path, schema in [
            ("master", "master.mkv", "Video.v1"),
            ("audio", "master.wav", "Audio.v1"),
            ("preview", "preview.mp4", "Video.v1"),
            ("qa", "qa.json", "QAReport.v1"),
        ]:
            output(port, path, schema, "qa_report" if port == "qa" else port)
    elif node == "TEST":
        time.sleep(p["sleep"])
        if p["fill_bytes"]:
            with (work / "pressure.bin").open("wb") as f:
                remaining = p["fill_bytes"]
                while remaining:
                    n = min(remaining, 65536)
                    f.write(b"X" * n)
                    remaining -= n
        if p["fail"]:
            raise ValueError("Requested fixture failure")
        text = p["text"].upper() if r["strategy_id"] == "fixture_b" else p["text"]
        atomic_json(
            work / "fixture.json",
            {"text": text, "fixture": True, "invocation": uuid.uuid4().hex},
        )
        output("result", "fixture.json", "Fixture.v1", "fixture")
    else:
        raise ValueError("Unknown worker node")
    atomic_json(
        Path(args.result),
        {
            "protocol_version": 1,
            "state": "SUCCEEDED",
            "artifacts": artifacts,
            "warnings": [],
        },
    )


if __name__ == "__main__":
    main()
