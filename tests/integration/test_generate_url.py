"""Real file/codec/dispatch test with explicit deterministic model substitutes."""

import json
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from mlvideo.generate import run
from mlvideo.util import atomic_json, sha512


def test_single_url_dispatch_resume_and_tamper(tmp_path, monkeypatch):
    np = pytest.importorskip("numpy")
    font = Path("/System/Library/Fonts/STHeiti Medium.ttc")
    if not font.exists() or not shutil.which("ffmpeg"):
        pytest.skip("Mac font and ffmpeg required")
    url = "https://www.youtube.com/watch?v=abcdefghijk"
    calls = []
    weight = tmp_path / "weight"
    weight.write_bytes(b"model fixture")
    model = {
        "python": sys.executable,
        "model_path": str(weight),
        "sha512": sha512(weight),
    }
    cfg = {
        "models": {
            "N07/whisper": [model],
            "N07/speaker_diarization": [model],
            "N11/cosyvoice3_zero_shot": [model],
            "N12/audio_qa_asr": [model],
            "N09/codex": [{"model_id": "controlled-test"}],
            "N06/captions": [{"endpoint": "controlled-test"}],
            "N17/bilingual": [{"font_path": str(font)}],
        }
    }
    which = shutil.which
    monkeypatch.setattr(
        "mlvideo.generate.shutil.which",
        lambda name: sys.executable if name in ("node", "codex") else which(name),
    )

    def worker(argv, work, timeout):
        work = Path(work)
        work.mkdir(parents=True, exist_ok=True)
        if "yt_dlp" in argv:
            calls.append("download")
            root = work.parent
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=320x180:rate=10:duration=6",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:sample_rate=48000:duration=6",
                    "-vf",
                    f"drawbox=x=40:y=125:w=100:h=35:color=blue:t=fill,drawtext=fontfile={font}:text=Hello:x=50:y=130:fontsize=20:fontcolor=white:borderw=2",
                    "-c:v",
                    "libx264",
                    "-c:a",
                    "aac",
                    str(root / "source.mp4"),
                ],
                check=True,
            )
            atomic_json(
                root / "source.info.json",
                {"id": "abcdefghijk", "ext": "mp4", "webpage_url": url},
            )
            return
        script = Path(argv[1]).name
        calls.append(script)
        r = json.loads(Path(argv[argv.index("--request") + 1]).read_text())
        if script == "speech_worker.py":
            atomic_json(
                work / "asr.json",
                {
                    "segments": [
                        {
                            "id": 0,
                            "start": 1.0,
                            "end": 2.0,
                            "text": " Hello",
                            "words": [{"word": " Hello", "start": 1.0, "end": 2.0}],
                        }
                    ]
                },
            )
            atomic_json(
                work / "vad.json", {"intervals": [{"start": 16000, "end": 32000}]}
            )
            names = ["asr.json", "vad.json"]
        elif script == "speaker_worker.py":
            atomic_json(
                work / "speakers.json", {"estimated_speaker_count": 1, "turns": []}
            )
            names = ["speakers.json"]
        elif script == "cosyvoice_batch_worker.py":
            receipts = []
            for item in r["items"]:
                audio = work / f"{item['id']}.raw.wav"
                with wave.open(str(audio), "wb") as w:
                    w.setparams((1, 2, 24000, 0, "NONE", ""))
                    w.writeframes(
                        (np.sin(np.arange(24000) * 2 * np.pi * 660 / 24000) * 10000)
                        .astype("<i2")
                        .tobytes()
                    )
                row = {"id": item["id"], "audio_sha512": sha512(audio)}
                atomic_json(work / f"{item['id']}.json", row)
                receipts.append(row)
            atomic_json(work / "environment.json", {"model": "controlled-test"})
            atomic_json(work / "result.json", {"items": receipts})
            return
        elif script == "dub_check_batch.py":
            atomic_json(
                work / "report.json",
                {
                    "status": "REVIEW",
                    "items": [
                        {"id": i["id"], "expected": i["text"], "recognized": i["text"]}
                        for i in r["items"]
                    ],
                },
            )
            return
        else:
            raise AssertionError(script)
        atomic_json(
            work / "result.json",
            {"state": "SUCCEEDED", "artifacts": [{"path": name} for name in names]},
        )

    monkeypatch.setattr("mlvideo.process.run_worker", worker)

    def scan(source, directory, deployment, **kwargs):
        directory = Path(directory)
        directory.mkdir()
        picture = directory / "frame.jpg"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                "1.5",
                "-i",
                str(source),
                "-frames:v",
                "1",
                str(picture),
            ],
            check=True,
        )
        value = {
            "width": 320,
            "height": 180,
            "rows": [
                {
                    "frame": 15,
                    "seconds": 1.5,
                    "evidence": str(picture),
                    "lines": [
                        {"text": "Hello", "box": [48, 128, 103, 157], "score": 1}
                    ],
                }
            ],
        }
        atomic_json(directory / "scan.json", value)
        return value

    monkeypatch.setattr("mlvideo.caption_scan.scan", scan)

    def script(asr, vad, directory, model, **kwargs):
        from mlvideo.auto_script import source_words, validate_script

        calls.append("translation")
        directory = Path(directory)
        directory.mkdir()
        words, _ = source_words(
            json.loads(Path(asr).read_text()), json.loads(Path(vad).read_text())
        )
        raw = {
            "items": [
                {
                    "parts": [
                        {
                            "start_word": 0,
                            "end_word": 1,
                            "speaker": "narrator",
                            "translation": "你好。",
                        }
                    ]
                }
            ]
        }
        value = {"words": words, "items": validate_script(words, raw), "discarded": []}
        atomic_json(directory / "script.json", value)
        atomic_json(directory / "response.json", raw)
        (directory / "prompt.txt").write_text("controlled translation fixture")
        (directory / "events.jsonl").write_text("{}\n")
        return value

    monkeypatch.setattr("mlvideo.auto_script.build", script)
    root = tmp_path / "job"
    out = tmp_path / "delivery/film.mp4"
    result = run(url, cfg, root, out)
    assert out.exists() and result["automated_checks"] == "PASS"
    assert result["human_listening"] == "REVIEW"
    assert sorted(x.name for x in out.parent.iterdir()) == ["film.mp4"]
    assert calls == [
        "download",
        "speech_worker.py",
        "speaker_worker.py",
        "translation",
        "cosyvoice_batch_worker.py",
        "dub_check_batch.py",
    ]
    first = list(calls)
    assert run(url, cfg, root, out, resume=True) == result
    assert calls == first
    original_hash = sha512(out)
    another = out.with_name("second-version.mp4")
    run(url, cfg, root, another, resume=True)
    assert another.exists() and sha512(out) == original_hash
    assert calls == first
    with out.open("ab") as f:
        f.write(b"tamper")
    with pytest.raises(ValueError, match="output changed"):
        run(url, cfg, root, out, resume=True)
    assert json.loads((root / "job.json").read_text())["state"] == "FAILED"
