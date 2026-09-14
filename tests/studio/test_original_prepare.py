"""Preparation retries upgrade old capped acquisitions without rewriting their history."""
import shutil
import subprocess
from pathlib import Path

import pytest
from yt_dlp import YoutubeDL

from mlvideo import store
from mlvideo.config import ROOT
from mlvideo.studio.catalog import Conflict
from mlvideo.studio.jobs import JobExecutor, execute_next
from mlvideo.util import read_json, sha512
from .helpers import register


def test_resolution_wins_over_fps_and_extractor_preference():
    with YoutubeDL({'format': 'bv*+ba/b', 'format_sort': ['res', 'fps'],
                    'format_sort_force': True, 'quiet': True}) as downloader:
        result = downloader.process_ie_result({'id': 'TEST', 'title': 'TEST', 'formats': [
            {'format_id': '1080p60', 'url': 'https://example.invalid/low.mp4', 'height': 1080,
             'width': 1920, 'fps': 60, 'vcodec': 'h264', 'acodec': 'none', 'ext': 'mp4', 'preference': 100},
            {'format_id': '2160p30', 'url': 'https://example.invalid/high.mp4', 'height': 2160,
             'width': 3840, 'fps': 30, 'vcodec': 'h264', 'acodec': 'none', 'ext': 'mp4', 'preference': -1},
            {'format_id': 'audio', 'url': 'https://example.invalid/audio.m4a',
             'vcodec': 'none', 'acodec': 'aac', 'ext': 'm4a'},
        ]}, download=False)
    assert result['format_id'] == '2160p30+audio'


def test_prepare_retry_refreshes_capped_download_and_uses_original(catalog, monkeypatch, tmp_path):
    old = store.ingest(catalog.engine, ROOT / 'tests/fixtures/generated/cfr.mkv')
    series = catalog.create_series('TEST')
    ep = catalog.create_episode(series['id'], 'TEST', 'https://www.youtube.com/watch?v=TEST0000000',
                               old['asset_sha512'], old['source_artifact_id'])
    job = catalog.prepare(ep['id'])
    catalog.finish_job(job['id'], 'RUNNING')
    executor = JobExecutor(catalog, catalog.get('studio_jobs', job['id']))
    executor.step('probe', old['asset_sha512'], 'N03', 'ffprobe', {'source': old['source_artifact_id']})
    catalog.finish_job(job['id'], 'FAILED', executor.result, 'TEST old N04 failure')
    high = tmp_path / 'highest.mkv'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=size=3840x2160:rate=25:duration=0.12',
                    '-f', 'lavfi', '-i', 'sine=sample_rate=48000:duration=0.2', '-c:v', 'libx264',
                    '-preset', 'ultrafast', '-c:a', 'pcm_s16le', '-ac', '2', str(high)], check=True)
    calls = []
    real_run = store.run_worker
    def download(argv, work, timeout):
        if 'yt_dlp' not in argv:
            return real_run(argv, work, timeout)
        calls.append(argv)
        assert argv[argv.index('-f') + 1] == 'bv*+ba/b'
        assert argv[argv.index('-S') + 1] == 'res,fps'
        assert '--format-sort-force' in argv
        snapshot = work / 'media.mkv'
        shutil.copyfile(high, snapshot)
        (work / 'final.txt').write_text(str(snapshot) + '\n')
    monkeypatch.setattr(store, 'run_worker', download)
    real_step = JobExecutor.step
    fail_once = [True]
    def step(self, key, asset, node, strategy, inputs, *args, **kwargs):
        if key != 'speech':
            return real_step(self, key, asset, node, strategy, inputs, *args, **kwargs)
        if fail_once[0]:
            fail_once[0] = False
            raise RuntimeError('TEST recognition failed after original preparation')
        return register(catalog, asset, {
            'speech': ('SpeechTrack.v1', {'audio_artifact_id': inputs['audio'], 'sample_rate': 48000,
                'segments': [{'id': 's', 'start_sample': 1000, 'end_sample': 2000, 'text': 'TEST', 'speaker_id': None}],
                'words': [], 'protected_intervals': [], 'coverage_status': 'REVIEW', 'warnings': ['TEST ASR fixture']}),
            'vad': ('Binary.v1', {'sample_rate': 16000, 'intervals': []}),
        })
    monkeypatch.setattr(JobExecutor, 'step', step)
    retry = catalog.retry_job(job['id'])
    assert execute_next(catalog)
    failed = catalog.get('studio_jobs', retry['id'])
    assert failed['state'] == 'FAILED'
    assert failed['result']['executions']['canonical']['state'] == 'SUCCEEDED'
    second_retry = catalog.retry_job(retry['id'])
    assert execute_next(catalog)
    completed = catalog.get('studio_jobs', second_retry['id'])
    assert completed['state'] == 'SUCCEEDED', completed['error_text']
    assert len(calls) == 1
    new_ep = catalog.get('studio_episodes', ep['id'])
    assert new_ep['asset_sha512'] == sha512(high) != old['asset_sha512']
    revision = catalog.get('studio_revisions', new_ep['active_revision_id'])
    video = catalog.engine.artifact(revision['payload']['bindings']['video'], new_ep['asset_sha512'])
    assert video['sha512'] == sha512(high)
    c = read_json(Path(catalog.engine.artifact(revision['payload']['bindings']['canonical'], new_ep['asset_sha512'])['path']))
    assert c['source_frames'] == 3 and c['source_samples'] == 9600
    assert c['video_tail_frames'] == 0
    # A changed asset starts its own execution chain; the Studio retry still points to the old job.
    assert completed['retry_of'] == retry['id']
    assert failed['result']['executions']['probe']['retry_of'] is None
    assert completed['result']['executions']['probe']['retry_of'] == failed['result']['executions']['probe']['execution_id']
    assert completed['result']['executions']['canonical']['retry_of'] == failed['result']['executions']['canonical']['execution_id']
    assert catalog.get('studio_jobs', job['id'])['state'] == 'FAILED'
    assert catalog.engine.artifact(old['source_artifact_id'], old['asset_sha512'])['sha512'] == old['asset_sha512']
    with pytest.raises(Conflict):
        catalog.bind_source(ep['id'], old['asset_sha512'], old['source_artifact_id'], replace_unreviewed=True)
    receipts = catalog.db.query("SELECT relative_path FROM artifacts WHERE schema_id='AcquisitionReceipt.v1'")
    assert any(read_json(catalog.root / r['relative_path']).get('download_policy') == store.DOWNLOAD_POLICY for r in receipts)
