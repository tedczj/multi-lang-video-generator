import json
import shutil
import subprocess
import wave

import pytest

from mlvideo.continuous import mix, plan, render
from mlvideo.continuous_verify import verify
from mlvideo.util import sha512


def test_render_decode_and_tamper(tmp_path):
    pytest.importorskip("numpy")
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg required")
    source = tmp_path / "source.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=192x108:rate=10:duration=6",
            "-c:v",
            "libx264",
            "-crf",
            "0",
            str(source),
        ],
        check=True,
    )
    source_pcm = b"\x01\x02\x03\x04" * 288000
    dub = b"\x05\x06\x07\x08" * 48000
    groups = [{"start_frame": 0, "end_frame": 60, "hold_frame": 59, "dub": "dub.wav"}]
    t = plan(list(range(0, 288001, 4800)), groups, [48000])
    for name, data in [
        ("source.wav", source_pcm),
        ("dub.wav", dub),
        ("combined.wav", mix(source_pcm, [dub], t)),
    ]:
        with wave.open(str(tmp_path / name), "wb") as w:
            w.setparams((2, 2, 48000, 0, "NONE", ""))
            w.writeframes(data)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"source": "source.mkv", "audio": "source.wav", "groups": groups})
    )
    for mode in ("continuous", "hold"):
        render(
            source,
            tmp_path / "combined.wav",
            t[mode],
            [],
            tmp_path / f"{mode}.mkv",
            {59},
        )
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(tmp_path / f"{mode}.mkv"),
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                str(tmp_path / f"{mode}.mp4"),
            ],
            check=True,
        )
    t["inputs"] = {
        str(p): sha512(p)
        for p in [manifest, source, tmp_path / "source.wav", tmp_path / "dub.wav"]
    }
    t["outputs"] = {
        p.name: sha512(p)
        for p in tmp_path.iterdir()
        if p.suffix in (".mkv", ".mp4", ".wav")
    }
    (tmp_path / "timeline.json").write_text(json.dumps(t))
    result = verify(tmp_path)
    assert result["variants"]["continuous"]["frames"] == 60
    assert result["variants"]["hold"]["frames"] == 150
    assert result["output_seconds"] == 9
    assert result["human_listening"] == "REVIEW"
    with (tmp_path / "continuous.mkv").open("ab") as f:
        f.write(b"changed")
    with pytest.raises(ValueError, match="Output changed"):
        verify(tmp_path)
