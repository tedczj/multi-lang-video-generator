import wave

import pytest

from mlvideo.media import join_voice_parts


def test_role_and_narrator_become_one_audio_without_changing_their_pcm(tmp_path):
    role, narrator, output = (tmp_path / name for name in ('role.wav', 'narrator.wav', 'sentence.wav'))
    payloads = [b'\x10\x00\x20\x00' * 100, b'\x30\x00\x40\x00' * 60]
    for path, payload in zip((role, narrator), payloads):
        with wave.open(str(path), 'wb') as f:
            f.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
            f.writeframes(payload)
    offsets = join_voice_parts([role, narrator], output, gap_samples=8)
    with wave.open(str(output), 'rb') as f:
        assert f.getnframes() == 168
        assert f.readframes(168) == payloads[0] + bytes(8 * 4) + payloads[1]
    assert [(x['start_sample'], x['end_sample']) for x in offsets] == [(0, 100), (108, 168)]
    with pytest.raises(ValueError):
        join_voice_parts([], output)
