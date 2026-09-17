import json
import shutil
import subprocess
import wave

import pytest

from mlvideo.continuous import produce
from mlvideo.continuous_verify import verify_delivery


def test_render_decode_immediate_audio_single_mp4_and_tamper(tmp_path):
    np = pytest.importorskip("numpy")
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
    for name, n, hz in [("source.wav", 288000, 440), ("dub.wav", 48000, 660)]:
        mono = (np.sin(np.arange(n) * hz * 2 * np.pi / 48000) * 10000).astype("<i2")
        data = np.column_stack([mono, mono]).tobytes()
        with wave.open(str(tmp_path / name), "wb") as w:
            w.setparams((2, 2, 48000, 0, "NONE", ""))
            w.writeframes(data)
    font = tmp_path / "unused-font"
    font.write_text("no subtitle pages in this codec fixture")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "source": "source.mkv",
                "audio": "source.wav",
                "font": str(font),
                "groups": [
                    {
                        "start_frame": 0,
                        "end_frame": 60,
                        "hold_frame": 59,
                        "speech_end_sample": 240000,
                        "dub": "dub.wav",
                    }
                ],
                "speech_intervals": [[1000, 240000]],
                "pages": [],
            }
        )
    )
    out = tmp_path / "delivery/output.mp4"
    work = tmp_path / "work"
    result = produce(manifest, work, out)
    assert result["seconds"] == 7
    assert sorted(x.name for x in out.parent.iterdir()) == ["output.mp4"]
    assert not list(work.glob("*.mkv"))
    report = verify_delivery(work)
    assert report["frames"] == 60
    assert report["added_english_chinese_gap_samples"] == 0
    assert report["human_listening"] == "REVIEW"
    with pytest.raises(ValueError, match="exists"):
        produce(manifest, tmp_path / "new-work", out)
    with out.open("ab") as f:
        f.write(b"changed")
    with pytest.raises(ValueError, match="Output changed"):
        verify_delivery(work)
