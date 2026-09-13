"""Disk pressure must stop workers before it takes down the database."""
import subprocess
import sys
from types import SimpleNamespace

import pytest
from mlvideo import process
from mlvideo.util import read_json


def test_worker_refuses_low_space_before_start(tmp_path, monkeypatch):
    monkeypatch.setattr(process.shutil, 'disk_usage', lambda path: SimpleNamespace(free=0))
    work = tmp_path / 'work'
    with pytest.raises(RuntimeError, match='磁盘可用空间不足'):
        process.run_worker([sys.executable, '-c', 'raise AssertionError("must not run")'], work, 5)
    assert not (tmp_path / 'runtime.json').exists()


def test_disk_pressure_stops_running_worker(tmp_path, monkeypatch):
    readings = iter([3 * 1024**3, 3 * 1024**3, 0])
    monkeypatch.setattr(process.shutil, 'disk_usage', lambda path: SimpleNamespace(free=next(readings)))
    with pytest.raises(RuntimeError, match='磁盘可用空间不足'):
        process.run_worker([sys.executable, '-c', 'import time; print("started", flush=True); time.sleep(30)'], tmp_path / 'work', 20)
    runtime = read_json(tmp_path / 'runtime.json')
    assert runtime['stopped'] and runtime['returncode'] != 0
    assert 'started' in (tmp_path / 'work/stdout.log').read_text()
    process.confirm_stopped(tmp_path / 'runtime.json')


def test_padding_checks_peak_disk_before_second_video(tmp_path, monkeypatch):
    from mlvideo.media import normalize, probe
    source = tmp_path / 'source.mkv'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
        'testsrc2=size=64x48:rate=25:duration=0.4', '-f', 'lavfi', '-i',
        'sine=frequency=440:sample_rate=48000:duration=0.8', '-c:v', 'ffv1',
        '-c:a', 'pcm_s16le', str(source)], check=True)
    work = tmp_path / 'normalize'
    work.mkdir()
    info = probe(source, work)
    monkeypatch.setattr(process.shutil, 'disk_usage', lambda path: SimpleNamespace(free=1024**3))
    with pytest.raises(RuntimeError, match='视频补帧需要至少'):
        normalize(source, info, work)
    assert (work / 'canonical.mkv').is_file()
    assert not (work / 'padded.mkv').exists()
