"""Real N11/N12 protocol smoke. Manual text inputs; no N09 or MySQL claim."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from mlvideo.contracts import strategy, validate, validate_output
from mlvideo.process import run_worker
from mlvideo.util import atomic_json, code_provenance, read_json, sha512, uid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--deployment", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--transcript", required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--regression-audio", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    audio = Path(args.reference).resolve()
    asset = sha512(audio)

    def ref(path, schema, execution="manual_smoke_input"):
        return {
            "artifact_id": execution + "_" + path.stem,
            "asset_sha512": asset,
            "execution_id": execution,
            "relative_path": path.name,
            "schema_id": schema,
            "sha512": sha512(path),
            "bytes": path.stat().st_size,
            "path": str(path),
        }

    def audio_info(path):
        result = json.loads(
            subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "a:0",
                    "-show_streams",
                    "-of",
                    "json",
                    str(path),
                ]
            )
        )["streams"][0]
        from fractions import Fraction

        sr = int(result["sample_rate"])
        return (
            sr,
            int(Fraction(result["duration_ts"]) * Fraction(result["time_base"]) * sr),
            int(result["channels"]),
        )

    sr, frames, _ = audio_info(audio)
    translation = out / "manual-translation.json"
    reference = out / "reference.json"
    atomic_json(
        translation,
        {
            "batch_id": "manual_smoke_not_codex",
            "source_artifact_id": "manual_smoke_transcript",
            "items": [
                {"unit_id": "u1", "source_text": args.transcript, "text": args.text}
            ],
        },
    )
    atomic_json(
        reference,
        {
            "speaker_id": "speaker2",
            "text": args.transcript,
            "audio_sha512": asset,
            "source_audio_artifact_id": "manual_smoke_reference",
            "start_sample": 0,
            "end_sample": frames,
            "sample_rate": sr,
        },
    )
    inputs = {
        "translation": ref(translation, "TranslationSet.v1"),
        "reference": ref(reference, "VoiceReference.v1"),
        "audio": ref(audio, "Audio.v1"),
    }

    def execute(node, name, inputs, params, version, retry_of=None):
        identity = uid("exec")
        directory = out / identity
        work = directory / "work"
        work.mkdir(parents=True)
        models = [read_json(Path(args.deployment))] if node == "N11" else []
        request = {
            "protocol_version": 1,
            "execution_id": identity,
            "asset_sha512": asset,
            "run_id": None,
            "node": node,
            "scope": "protocol_smoke",
            "version": version,
            "strategy_id": name,
            "strategy_version": "1",
            "retry_of": retry_of,
            "inputs": {p: [v] for p, v in inputs.items()},
            "params": params,
            "models": models,
            "output_dir": str(work),
            "code": code_provenance(directory / "source.snapshot.zip"),
        }
        validate("WorkerRequest.v1", request)
        atomic_json(directory / "request.json", request)
        result_path = work / "worker-result.json"
        run_worker(
            [
                sys.executable,
                "-m",
                "mlvideo.worker",
                "--request",
                str(directory / "request.json"),
                "--result",
                str(result_path),
            ],
            work,
            600,
        )
        result = read_json(result_path)
        validate("WorkerResult.v1", result)
        assert {a["port"]: a["schema_id"] for a in result["artifacts"]} == strategy(
            node, name
        )[1]
        outputs = {
            a["port"]: ref(validate_output(work, a), a["schema_id"], identity)
            for a in result["artifacts"]
        }
        print(
            json.dumps({"node": node, "execution": identity, "version": version}),
            flush=True,
        )
        return identity, outputs

    previous = None
    runs = []
    for version in [1, 2]:
        identity, outputs = execute(
            "N11",
            "cosyvoice3_zero_shot",
            inputs,
            {"unit_id": "u1", "seed": 42},
            version,
            previous,
        )
        previous = identity
        qa_id, qa = execute(
            "N12",
            "audio_qa",
            {p: outputs[p] for p in ["audio", "clip"]},
            {"leading_review_seconds": 1.0},
            version,
        )
        runs.append(
            {
                "tts_execution": identity,
                "qa_execution": qa_id,
                "outputs": outputs,
                "qa": read_json(Path(qa["qa"]["path"])),
            }
        )
    bad_audio = Path(args.regression_audio).resolve()
    sr, frames, channels = audio_info(bad_audio)
    bad_clip = out / "regression-clip.json"
    clip = read_json(Path(runs[0]["outputs"]["clip"]["path"]))
    clip.update(
        audio_sha512=sha512(bad_audio), frames=frames, sample_rate=sr, channels=channels
    )
    atomic_json(bad_clip, clip)
    _, qa = execute(
        "N12",
        "audio_qa",
        {"audio": ref(bad_audio, "Audio.v1"), "clip": ref(bad_clip, "RawDubClip.v1")},
        {"leading_review_seconds": 1.0},
        1,
    )
    regression = read_json(Path(qa["qa"]["path"]))
    assert regression["overall"] == "REVIEW" and regression["trimmed_samples"] == 0
    assert (
        next(c for c in regression["checks"] if c["id"] == "leading_audio")["status"]
        == "REVIEW"
    )
    atomic_json(
        out / "summary.json",
        {
            "status": "passed",
            "scope": "real worker protocol; manual translation; no DB integration or complete phase2 acceptance",
            "runs": runs,
            "regression": regression,
        },
    )


if __name__ == "__main__":
    main()
