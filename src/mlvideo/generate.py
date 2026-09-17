"""One YouTube URL -> one MP4, with resumable, source-bound automatic candidates."""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from .config import ROOT
from .util import atomic_json, digest, file_lock, read_json, sha512, uid


class Job:
    def __init__(self, root, url, config, resume=False):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.config = config
        self.binding = {
            "url": url,
            "models": config["models"],
            "policy": "youtube-mp4-v1",
        }
        path = self.root / "job.json"
        if path.exists():
            prior = read_json(path)
            if not resume:
                raise ValueError("Job exists; use --resume or a new work directory")
            if prior["binding"] != self.binding:
                raise ValueError("Job source/models changed; create a new job")
        elif any(p.name != ".writer.lock" for p in self.root.iterdir()) and not resume:
            raise ValueError("Nonempty work directory; use a new job")
        self.state = {
            "binding": self.binding,
            "state": "RUNNING",
            "stage": "preflight",
            "human_acceptance": "REVIEW",
        }
        atomic_json(path, self.state)

    def stage(self, name, inputs, action):
        marker = self.root / ".stages" / f"{name}.json"
        key = digest(inputs)
        self.state.update(stage=name)
        atomic_json(self.root / "job.json", self.state)
        if marker.exists():
            done = read_json(marker)
            if done["binding"] != key:
                raise ValueError(
                    f"Stage {name} inputs changed; new work directory required"
                )
            for p, h in done["outputs"].items():
                if sha512(p) != h:
                    raise ValueError(f"Stage {name} output changed: {p}")
            print(name, "cached", flush=True)
            return done["result"]
        print(name, "running", flush=True)
        result, paths = action()
        atomic_json(
            marker,
            {
                "binding": key,
                "result": result,
                "outputs": {str(Path(p).resolve()): sha512(p) for p in paths},
            },
        )
        print(name, "done", flush=True)
        return result

    def worker(self, name, script, request):
        work = self.root / name
        work.mkdir(parents=True, exist_ok=True)
        request = request | {"output_dir": str(work)}
        path = work / "request.json"
        if path.exists():
            if read_json(path) != request:
                raise ValueError(f"Worker {name} request changed")
        else:
            atomic_json(path, request)
        result = work / "result.json"
        if result.exists():
            value = read_json(result)
            if value.get("state") != "SUCCEEDED":
                raise ValueError(f"Worker {name} not successful")
            for artifact in value["artifacts"]:
                if not (work / artifact["path"]).is_file():
                    raise ValueError("Missing worker artifact")
        else:
            from .process import run_worker

            run_worker(
                [
                    request["models"][0]["python"],
                    str(ROOT / "workers" / script),
                    "--request",
                    str(path),
                    "--result",
                    str(result),
                ],
                work,
                7200,
            )
        return read_json(result), [result, path] + [
            work / a["path"] for a in read_json(result)["artifacts"]
        ]


def preflight(config, work):
    for program in ["ffmpeg", "ffprobe", "node", "codex"]:
        if not shutil.which(program):
            raise ValueError(f"Missing command: {program}")
    for key in ["N07/whisper", "N07/speaker_diarization", "N11/cosyvoice3_zero_shot"]:
        d = config["models"][key][0]
        for field in [
            "python",
            "model_path",
            "segmentation_path",
            "embedding_path",
            "model_dir",
            "source_dir",
        ]:
            if field in d and not Path(d[field]).exists():
                raise ValueError(f"Missing {key} {field}: {d[field]}")
    if shutil.disk_usage(work).free < 3 * 1024**3:
        raise ValueError("Need at least 3 GiB free in generation workspace")


def run(url, config, work, output, resume=False):
    import bisect

    from . import media
    from .asr_refine import apply, candidates
    from .audio_qa import inspect_audio
    from .auto_script import build
    from .caption_scan import scan
    from .caption_tracking import merge_shared_pages, track
    from .continuous import produce
    from .generate_prepare import references, reuse_voices, review_summary, safe_groups

    if urlparse(url).scheme not in ("http", "https") or urlparse(url).hostname not in (
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtu.be",
    ):
        raise ValueError("Expected a YouTube video URL")
    work = Path(work).resolve()
    output = Path(output).resolve()
    if output.exists() and not resume:
        raise ValueError("Output exists; choose a new filename")
    work.mkdir(parents=True, exist_ok=True)
    with file_lock(work / ".writer.lock"):
        # The lock file alone does not make a fresh job nonempty.
        job = Job(work, url, config, resume=resume)
        try:
            preflight(config, work)

            def acquire():
                info_path = work / "source.info.json"
                if not info_path.exists():
                    from .process import run_worker

                    run_worker(
                        [
                            sys.executable,
                            "-m",
                            "yt_dlp",
                            "--js-runtimes",
                            "node",
                            "--no-playlist",
                            "--no-cache-dir",
                            "-f",
                            "bv*+ba/b",
                            "--format-sort-force",
                            "-S",
                            "res,fps",
                            "--write-info-json",
                            "-o",
                            str(work / "source.%(ext)s"),
                            url,
                        ],
                        work / "download",
                        7200,
                    )
                info = read_json(info_path)
                if info.get("webpage_url") != url and info.get("original_url") != url:
                    # Playlist parameters are irrelevant because --no-playlist is fixed.
                    requested = urlparse(url)
                    from urllib.parse import parse_qs

                    identity = (
                        requested.path.strip("/")
                        if requested.hostname == "youtu.be"
                        else parse_qs(requested.query).get("v", [""])[0]
                    )
                    if identity != info["id"]:
                        raise ValueError("Cached download is a different video")
                source = work / ("source." + info["ext"])
                if not source.is_file():
                    raise ValueError("Downloaded source missing")
                return {
                    "source": str(source),
                    "id": info["id"],
                    "sha512": sha512(source),
                }, [source, info_path]

            acquisition = job.stage(
                "download", {"url": url, "policy": "highest_resolution_v1"}, acquire
            )
            source = Path(acquisition["source"])
            canonical = work / "canonical"

            def prepare():
                canonical.mkdir(exist_ok=True)
                if not (canonical / "canonical.json").exists():
                    media.preserve_source(
                        source, media.probe(source, canonical, False), canonical
                    )
                elif sha512(canonical / "original.bin") != acquisition["sha512"]:
                    raise ValueError("Prepared source differs from download")
                return read_json(canonical / "canonical.json"), [
                    canonical / "canonical.json",
                    canonical / "canonical.wav",
                    canonical / "original.bin",
                ]

            clock = job.stage("canonical", {"source": acquisition["sha512"]}, prepare)
            audio = canonical / "canonical.wav"
            audio_hash = sha512(audio)
            inputs = {
                "audio": [
                    {
                        "path": str(audio),
                        "sha512": audio_hash,
                        "artifact_id": "source-audio",
                    }
                ]
            }
            for name, key, strategy, script, params in [
                ("speech", "N07/whisper", "whisper", "speech_worker.py", {}),
                (
                    "speakers",
                    "N07/speaker_diarization",
                    "speaker_diarization",
                    "speaker_worker.py",
                    {"cluster_threshold": 0.5},
                ),
            ]:
                request = {
                    "node": "N07",
                    "strategy_id": strategy,
                    "models": config["models"][key],
                    "inputs": inputs,
                    "params": params,
                }
                job.stage(
                    name,
                    request,
                    lambda n=name, s=script, r=request: job.worker(n, s, r),
                )
            asr_path = work / "speech/asr.json"
            vad_path = work / "speech/vad.json"
            asr = read_json(asr_path)
            vad = read_json(vad_path)
            from fractions import Fraction

            times = [
                float(Fraction(t) - Fraction(clock["origin_seconds"]))
                for t in clock["source_pts"]
            ]
            samples = set()
            for segment in asr["segments"]:
                a, b = segment["start"], segment["end"]
                for t in [(a + b) / 2] + [
                    a + 0.3 + 2 * i for i in range(int((b - a) / 2))
                ]:
                    samples.add(min(len(times) - 1, bisect.bisect_left(times, t)))
            ocr_dir = work / "ocr-script"

            def ocr_action():
                result = scan(
                    source,
                    ocr_dir,
                    config["models"]["N06/captions"][0],
                    sample_frames=sorted(samples),
                )
                return {"path": str(ocr_dir / "scan.json")}, [ocr_dir / "scan.json"] + [
                    Path(r["evidence"]) for r in result["rows"]
                ]

            job.stage(
                "ocr",
                {
                    "source": acquisition["sha512"],
                    "samples": sorted(samples),
                    "model": config["models"]["N06/captions"],
                },
                ocr_action,
            )
            ocr = read_json(ocr_dir / "scan.json")
            refined = work / "asr-refined-v2"
            refined.mkdir(exist_ok=True)

            def refine():
                items = candidates(asr, ocr)
                request = {
                    "items": items,
                    "model": config["models"]["N07/whisper"][0],
                    "audio": str(audio),
                    "audio_sha512": audio_hash,
                    "output_dir": str(refined),
                }
                if items:
                    if (refined / "request.json").exists() and read_json(
                        refined / "request.json"
                    ) != request:
                        raise ValueError("ASR refinement request changed")
                    atomic_json(refined / "request.json", request)
                    if not (refined / "patches.json").exists():
                        from .process import run_worker

                        run_worker(
                            [
                                request["model"]["python"],
                                str(ROOT / "workers/asr_refine_worker.py"),
                                "--request",
                                str(refined / "request.json"),
                            ],
                            refined,
                            7200,
                        )
                    revised, audit = apply(asr, read_json(refined / "patches.json"))
                else:
                    revised, audit = asr, []
                atomic_json(refined / "asr.json", revised)
                atomic_json(refined / "audit.json", audit)
                return {"path": str(refined / "asr.json")}, [
                    refined / "asr.json",
                    refined / "audit.json",
                ]

            job.stage(
                "refine-asr",
                {"asr": sha512(asr_path), "ocr": sha512(ocr_dir / "scan.json")},
                refine,
            )
            hints = [
                {
                    "seconds": x["seconds"],
                    "text": " ".join(
                        l["text"]
                        for l in x["lines"]
                        if l["box"][1] >= ocr["height"] * 0.5
                    ),
                }
                for x in ocr["rows"]
            ]
            prior = (
                read_json(work / "script/script.json")
                if (work / "script/script.json").exists()
                else None
            )
            script_dir = work / "script-final"

            def scripting():
                build(
                    refined / "asr.json",
                    vad_path,
                    script_dir,
                    config["models"]["N09/codex"][0],
                    ocr=hints,
                    prior=prior,
                )
                return {"path": str(script_dir / "script.json")}, [
                    script_dir / "script.json",
                    script_dir / "response.json",
                    script_dir / "prompt.txt",
                    script_dir / "events.jsonl",
                ]

            job.stage(
                "script",
                {
                    "asr": sha512(refined / "asr.json"),
                    "vad": sha512(vad_path),
                    "hints": hints,
                    "prior": digest(prior or {}),
                    "model": config["models"]["N09/codex"],
                },
                scripting,
            )
            script_data = read_json(script_dir / "script.json")
            groups = safe_groups(script_data, clock, vad)
            groups = merge_shared_pages(ocr, groups)
            refs_dir = work / "references-final"

            def reference_action():
                if (refs_dir / "references.json").exists():
                    result = read_json(refs_dir / "references.json")
                    for r in result.values():
                        if (
                            r["source_audio_sha512"] != audio_hash
                            or sha512(r["audio"]) != r["audio_sha512"]
                        ):
                            raise ValueError("Reference input/output changed")
                else:
                    result = references(script_data, audio, refs_dir)
                return result, [refs_dir / "references.json"] + [
                    Path(r["audio"]) for r in result.values()
                ]

            refs = job.stage(
                "references",
                {"script": sha512(script_dir / "script.json"), "audio": audio_hash},
                reference_action,
            )
            tts = work / "tts-final"
            items = [p for u in script_data["items"] for p in u["parts"]]
            model = config["models"]["N11/cosyvoice3_zero_shot"][0]

            def synthesis():
                tts.mkdir(exist_ok=True)
                request = {
                    "model": model,
                    "references": refs,
                    "items": items,
                    "output_dir": str(tts),
                }
                if (tts / "request.json").exists() and read_json(
                    tts / "request.json"
                ) != request:
                    raise ValueError("TTS request changed")
                if (
                    not (tts / "request.json").exists()
                    and (work / "tts/result.json").exists()
                ):
                    reuse_voices(
                        work / "tts",
                        tts,
                        model,
                        items,
                        refs,
                        ROOT / "workers/cosyvoice_batch_worker.py",
                    )
                atomic_json(tts / "request.json", request)
                if not (tts / "result.json").exists():
                    from .process import run_worker

                    run_worker(
                        [
                            model["python"],
                            str(ROOT / "workers/cosyvoice_batch_worker.py"),
                            "--request",
                            str(tts / "request.json"),
                        ],
                        tts,
                        14400,
                    )
                result = read_json(tts / "result.json")
                if {r["id"] for r in result["items"]} != {i["id"] for i in items}:
                    raise ValueError("TTS result incomplete")
                paths = [
                    tts / "result.json",
                    tts / "environment.json",
                    tts / "request.json",
                ]
                for row in result["items"]:
                    path = tts / f"{row['id']}.raw.wav"
                    if sha512(path) != row["audio_sha512"]:
                        raise ValueError("TTS audio changed")
                    paths += [path, tts / f"{row['id']}.json"]
                return {"parts": len(items)}, paths

            job.stage(
                "tts",
                {
                    "script": sha512(script_dir / "script.json"),
                    "references": refs,
                    "model": model,
                },
                synthesis,
            )
            check_dir = work / "check-zh"

            def chinese_check():
                check_dir.mkdir(exist_ok=True)
                request = {
                    "model": config["models"]["N12/audio_qa_asr"][0],
                    "items": [
                        {
                            "id": i["id"],
                            "audio": str(tts / f"{i['id']}.raw.wav"),
                            "text": i["translation"],
                        }
                        for i in items
                    ],
                    "output_dir": str(check_dir),
                }
                if (check_dir / "request.json").exists() and read_json(
                    check_dir / "request.json"
                ) != request:
                    raise ValueError("Chinese check inputs changed")
                atomic_json(check_dir / "request.json", request)
                if not (check_dir / "report.json").exists():
                    from .process import run_worker

                    run_worker(
                        [
                            request["model"]["python"],
                            str(ROOT / "workers/dub_check_batch.py"),
                            "--request",
                            str(check_dir / "request.json"),
                        ],
                        check_dir,
                        7200,
                    )
                return {"path": str(check_dir / "report.json")}, [
                    check_dir / "report.json",
                    check_dir / "request.json",
                ] + list(check_dir.glob("u*.json"))

            job.stage(
                "check-zh",
                {
                    "voices": sha512(tts / "result.json"),
                    "model": config["models"]["N12/audio_qa_asr"],
                },
                chinese_check,
            )
            joined = work / "joined"

            def assemble():
                joined.mkdir(exist_ok=True)
                reports = []
                result_groups = []
                for group in groups:
                    paths = []
                    for part in group["parts"]:
                        raw = tts / f"{part['id']}.raw.wav"
                        pcm_path = joined / f"{part['id']}.wav"
                        qa_dir = joined / part["id"]
                        qa_dir.mkdir(exist_ok=True)
                        qa = inspect_audio(raw, qa_dir)
                        if any(c["status"] == "FAIL" for c in qa["checks"]):
                            raise ValueError(f"Invalid audio: {part['id']}")
                        reports.append({"id": part["id"], "qa": qa})
                        if not pcm_path.exists():
                            subprocess.run(
                                [
                                    "ffmpeg",
                                    "-nostdin",
                                    "-v",
                                    "error",
                                    "-i",
                                    str(raw),
                                    "-ar",
                                    "48000",
                                    "-ac",
                                    "2",
                                    "-c:a",
                                    "pcm_s16le",
                                    str(pcm_path),
                                ],
                                check=True,
                            )
                        paths.append(pcm_path)
                    destination = joined / f"{group['id']}.wav"
                    if not destination.exists():
                        media.join_voice_parts(paths, destination, gap_samples=0)
                    result_groups.append(group | {"dub": str(destination)})
                atomic_json(joined / "groups.json", result_groups)
                atomic_json(joined / "audio-qa.json", reports)
                return result_groups, [
                    joined / "groups.json",
                    joined / "audio-qa.json",
                ] + list(joined.glob("*.wav"))

            groups = job.stage(
                "join",
                {"groups": groups, "voices": sha512(tts / "result.json")},
                assemble,
            )
            page_dir = work / "pages-v2"

            def pages_action():
                pages = track(source, ocr, groups, page_dir)
                return pages, [
                    page_dir / "pages.json",
                    page_dir / "tracking.json",
                    page_dir / "candidates.json",
                ]

            pages = job.stage(
                "pages-v2",
                {
                    "source": acquisition["sha512"],
                    "groups": groups,
                    "ocr": sha512(ocr_dir / "scan.json"),
                },
                pages_action,
            )
            manifest = {
                "source": str(source),
                "audio": str(audio),
                "canonical": str(canonical / "canonical.json"),
                "font": config["models"]["N17/bilingual"][0]["font_path"],
                "groups": groups,
                "pages": pages,
                "speech_intervals": [
                    [g["start_sample"], g["end_sample"]] for g in groups
                ],
                "lineage": {
                    "url": url,
                    "source_sha512": acquisition["sha512"],
                    "script": str(script_dir / "script.json"),
                    "voice": "CosyVoice3 zero-shot",
                    "quality_status": "REVIEW",
                },
            }
            manifest_path = work / "manifests" / (digest(manifest)[:24] + ".json")
            atomic_json(manifest_path, manifest)

            def render_action():
                # Failed attempts stay separate; only a verified MP4 receives the delivery name.
                render_dir = work / uid("render")
                result = produce(manifest_path, render_dir, output)
                return result, [
                    output,
                    render_dir / "timeline.json",
                    render_dir / "checks.json",
                    render_dir / "combined.wav",
                ]

            result = job.stage(
                "render-" + digest([sha512(manifest_path), str(output)])[:16],
                {"manifest": sha512(manifest_path), "output": str(output)},
                render_action,
            )
            review_summary(work, result)
            job.state.update(
                state="GENERATED",
                stage="complete",
                output=str(output),
                automated_checks="PASS",
                human_acceptance="REVIEW",
            )
            atomic_json(work / "job.json", job.state)
            return result
        except BaseException as error:
            job.state.update(state="FAILED", error=str(error))
            atomic_json(work / "job.json", job.state)
            raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("url")
    p.add_argument("--config", type=Path, default=ROOT / "config/local-generation.json")
    p.add_argument("--work", type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--resume", action="store_true")
    args = p.parse_args()
    if args.resume and args.work is None:
        p.error("--resume requires --work")
    work = args.work
    try:
        config = read_json(args.config)
        work = args.work or Path(config["generation_work_root"]) / uid("video")
        output = args.output or Path(config["generation_output_root"]) / (
            work.name + ".mp4"
        )
        if output.suffix.lower() != ".mp4":
            raise ValueError("Delivery filename must end in .mp4")
        print(
            json.dumps(
                run(args.url, config, work, output, args.resume),
                ensure_ascii=False,
                indent=2,
            )
        )
    except (
        OSError,
        ValueError,
        RuntimeError,
        KeyError,
        ImportError,
        subprocess.SubprocessError,
    ) as e:
        print(
            json.dumps(
                {"error": str(e), "work": str(args.work) if args.work else None},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
