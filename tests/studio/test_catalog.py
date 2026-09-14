import copy
import json
import pytest
from mlvideo.studio.catalog import Conflict, REFERENCE_CHECKS, VOICE_CHECKS, youtube_url
from mlvideo.util import canonical
from .helpers import seed_episode, annotate_all, profile_ready, fake_tts, run_next


@pytest.mark.parametrize('url', ['http://youtube.com/watch?v=Ye33eY4UNtY','https://evil.test/watch?v=Ye33eY4UNtY',
    'https://youtube.com.evil.test/watch?v=Ye33eY4UNtY','https://youtube.com/playlist?list=abc',
    'https://user:pass@youtube.com/watch?v=Ye33eY4UNtY','file:///etc/passwd','https://youtu.be/short'])
def test_url_rejects_non_video(url):
    with pytest.raises(ValueError): youtube_url(url)


def test_url_removes_offset_and_playlist():
    assert youtube_url('https://www.youtube.com/watch?v=Ye33eY4UNtY&t=33s&list=abc') == 'https://www.youtube.com/watch?v=Ye33eY4UNtY'
    assert youtube_url('https://youtu.be/Ye33eY4UNtY') == 'https://www.youtube.com/watch?v=Ye33eY4UNtY'


def test_series_do_not_share_character_identity(catalog):
    a=catalog.create_series('A');b=catalog.create_series('B');c=catalog.create_character(a['id'],'Narrator')
    with pytest.raises(ValueError,match='不属于'): catalog.character_in_series(c['id'],b['id'])


def test_annotations_optimistic_lock_and_suggestions(catalog):
    series,ep,rev=seed_episode(catalog);char=catalog.create_character(series['id'],'A');s=rev['segments'][0]
    first=catalog.annotate(rev['id'],s['id'],0,char['id'],'CONFIRMED',s['text'],'你好','Ted','实际检查')
    with pytest.raises(Conflict):catalog.annotate(rev['id'],s['id'],0,char['id'],'CONFIRMED',s['text'],'旧页面','Ted','stale')
    for target in ['第一份建议','第二份建议']:
        catalog.add_suggestions(rev['id'],[{'segment_id':s['id'],'kind':'translation','text':target}])
    actual=catalog.revision_view(rev['id'])['segments'][0]
    assert actual['annotation']['id']==first['id'] and actual['annotation']['chinese_text']=='你好'
    assert actual['suggestions']['translation']['payload']['text']=='第二份建议'
    assert not catalog.rows('studio_references')


def test_revision_only_carries_unchanged_rows(catalog):
    series,ep,rev=seed_episode(catalog);char=catalog.create_character(series['id'],'A');annotate_all(catalog,rev,char)
    rows=copy.deepcopy(rev['payload']['segments']);rows[0]['text']='Edited text.'
    new=catalog.create_revision(ep['id'],rev['payload']['bindings'],rows,rev['id'],'修改转录')
    assert new['segments'][0]['annotation'] is None and new['segments'][1]['annotation']['carried_from']
    with pytest.raises(Conflict):catalog.annotate(rev['id'],rev['segments'][0]['id'],1,char['id'],'CONFIRMED','Hello','你好','Ted','旧分段')


def test_batch_rollback_on_stale_version(catalog):
    series,ep,rev=seed_episode(catalog);char=catalog.create_character(series['id'],'A')
    items=[{'segment_id':s['id'],'expected_version':0 if i==0 else 9,'character_id':char['id'],'status':'CONFIRMED',
            'english_text':s['text'],'chinese_text':'你好','reviewer':'Ted','reason':'批量测试'} for i,s in enumerate(rev['segments'])]
    with pytest.raises(Conflict):catalog.batch_annotate(rev['id'],items)
    assert not catalog.rows('studio_annotations')


def test_reference_cannot_span_unknown(catalog):
    series,ep,rev=seed_episode(catalog);char=catalog.create_character(series['id'],'A');s=rev['segments'][0]
    catalog.annotate(rev['id'],s['id'],0,char['id'],'CONFIRMED',s['text'],'你好','Ted','确认第一段')
    with pytest.raises(ValueError,match='包含未确认'):
        catalog.create_reference(rev['id'],char['id'],s['start_sample'],rev['segments'][1]['end_sample'],'Full text','Ted','test')
    ref=catalog.create_reference(rev['id'],char['id'],s['start_sample'],s['end_sample'],s['text'],'Ted','candidate')
    assert ref['state']=='CANDIDATE' and ref['artifacts'] is None
    with pytest.raises(ValueError):catalog.approve_reference(ref['id'],'Ted','not ready',dict.fromkeys(REFERENCE_CHECKS,True))
    run_next(catalog)
    with pytest.raises(ValueError):catalog.approve_reference(ref['id'],'Ted','missing checks',{})
    assert catalog.approve_reference(ref['id'],'Ted','实际复核',dict.fromkeys(REFERENCE_CHECKS,True))['state']=='APPROVED'
    catalog.disable_reference(ref['id'],'Ted','found contamination')
    with pytest.raises(ValueError):catalog.create_profile(char['id'],ref['id'],'disabled')


def test_publishing_requires_bound_preview(catalog,monkeypatch):
    fake_tts(monkeypatch);series,ep,rev=seed_episode(catalog);char=catalog.create_character(series['id'],'A');annotate_all(catalog,rev,char)
    s=rev['segments'][0];ref=catalog.create_reference(rev['id'],char['id'],s['start_sample'],s['end_sample'],s['text'],'Ted','candidate');run_next(catalog)
    catalog.approve_reference(ref['id'],'Ted','review',dict.fromkeys(REFERENCE_CHECKS,True))
    p=catalog.create_profile(char['id'],ref['id'],'voice');j=catalog.preview(p['id'],'你好')
    with pytest.raises(ValueError):catalog.publish_profile(p['id'],j['id'],'Ted','not executed',dict.fromkeys(VOICE_CHECKS,True))
    run_next(catalog);catalog.publish_profile(p['id'],j['id'],'Ted','TEST ONLY',dict.fromkeys(VOICE_CHECKS,True))
    assert catalog.get('studio_characters',char['id'])['active_profile_id']==p['id']


def test_plan_freezes_profile_and_translation(catalog,monkeypatch):
    fake_tts(monkeypatch);series,ep,rev=seed_episode(catalog);char=catalog.create_character(series['id'],'A')
    p,ref=profile_ready(catalog,ep,rev,char);plan=catalog.create_plan(ep['id'],rev['id'],'Ted','boundaries reviewed')
    frozen=canonical(plan['payload']);run_next(catalog)
    catalog.create_profile(char['id'],ref['id'],'voice2')
    assert catalog.get('studio_characters',char['id'])['active_profile_id']==p['id']
    s=catalog.revision_view(rev['id'])['segments'][0]
    catalog.annotate(rev['id'],s['id'],1,char['id'],'CONFIRMED',s['text'],'不同译文','Ted','修改')
    assert canonical(catalog.get('studio_plans',plan['id'])['payload'])==frozen
    assert catalog.plan_stale(catalog.get('studio_plans',plan['id']))
    with pytest.raises(Conflict):catalog.generate(plan['id'])


def test_unknown_blocks_plan(catalog):
    series,ep,rev=seed_episode(catalog)
    with pytest.raises(ValueError,match='所有片段'):catalog.create_plan(ep['id'],rev['id'],'Ted','test')


def test_translation_keeps_queued_annotation_identity(catalog, monkeypatch):
    from mlvideo.studio.jobs import JobExecutor

    series, ep, rev = seed_episode(catalog)
    s = rev['segments'][0]
    char = catalog.create_character(series['id'], 'A')
    old = catalog.annotate(rev['id'], s['id'], 0, char['id'], 'REVIEW', s['text'], '', 'local-user', '角色草稿')
    job = catalog.translate(rev['id'])
    assert job['payload']['source_annotations'][s['id']] == old['id']
    new = catalog.annotate(rev['id'], s['id'], 1, char['id'], 'REVIEW', s['text'], '用户先改的译文', 'local-user', '改译文')
    executor = JobExecutor(catalog, job)
    monkeypatch.setattr(executor, 'step', lambda key, *args, **kwargs:
                        {'utterances': 'TEST'} if key == 'translation_units' else {'translation': 'TEST'})
    monkeypatch.setattr(executor, 'value', lambda *args: {'items': [
        {'unit_id': s['id'], 'source_text': s['text'], 'text': '迟到的测试译文'}]})
    executor.translate()
    saved = catalog.revision_view(rev['id'])['segments'][0]
    assert saved['annotation']['id'] == new['id']
    assert saved['annotation']['chinese_text'] == '用户先改的译文'
    assert saved['suggestions']['translation']['payload']['source_annotation_id'] == old['id']


def test_snapshot_tampering_is_detected(catalog):
    series,ep,rev=seed_episode(catalog)
    catalog.db.query('UPDATE studio_revisions SET payload_json=%s WHERE id=%s',(json.dumps({'bad':True}),rev['id']))
    with pytest.raises(ValueError,match='快照校验'):catalog.get('studio_revisions',rev['id'])


def test_no_automatic_job_retry(catalog):
    series=catalog.create_series('test');ep=catalog.create_episode(series['id'],'test','https://youtu.be/Ye33eY4UNtY')
    job=catalog.prepare(ep['id']);catalog.finish_job(job['id'],'RUNNING');catalog.finish_job(job['id'],'FAILED',{},'test failure')
    assert len(catalog.rows('studio_jobs'))==1
    retry=catalog.retry_job(job['id'])
    assert retry['id']!=job['id'] and retry['retry_of']==job['id'] and retry['payload']==job['payload']


def test_duplicate_or_out_of_bounds_revision(catalog):
    series,ep,rev=seed_episode(catalog)
    rows=copy.deepcopy(rev['payload']['segments']);rows[1]['id']=rows[0]['id']
    with pytest.raises(ValueError):catalog.create_revision(ep['id'],rev['payload']['bindings'],rows,rev['id'])
    rows=copy.deepcopy(rev['payload']['segments']);rows[0]['start_sample']=-1
    with pytest.raises(ValueError):catalog.create_revision(ep['id'],rev['payload']['bindings'],rows,rev['id'])


def test_revision_does_not_carry_reviews_to_replaced_audio(catalog):
    from mlvideo.phase2_pipeline import ports
    from mlvideo.util import read_json
    from .helpers import register
    from pathlib import Path

    series, ep, rev = seed_episode(catalog)
    char = catalog.create_character(series['id'], 'A')
    annotate_all(catalog, rev, char)
    source = ep['source_artifact_id']
    probe = ports(catalog.engine.run(ep['asset_sha512'], 'N03', 'ffprobe', {'source': source}, {}))
    media = ports(catalog.engine.run(ep['asset_sha512'], 'N04', 'ffmpeg', {'source': source, 'probe': probe['probe']}, {}))
    inventory = ports(catalog.engine.run(ep['asset_sha512'], 'N05', 'inventory', {'video': media['video'], 'probe': probe['probe']}, {}))
    old = rev['payload']['bindings']
    speech = read_json(Path(catalog.engine.artifact(old['speech'], ep['asset_sha512'])['path']))
    vad = read_json(Path(catalog.engine.artifact(old['vad'], ep['asset_sha512'])['path']))
    speech['audio_artifact_id'] = media['audio']
    analysis = register(catalog, ep['asset_sha512'], {'speech': ('SpeechTrack.v1', speech), 'vad': ('Binary.v1', vad)})
    captions = ports(catalog.engine.run(ep['asset_sha512'], 'N06', 'studio_captions', {'speech': analysis['speech'], 'inventory': inventory['inventory']}, {}))
    bindings = {k: media[k] for k in ('audio', 'video', 'canonical')} | analysis | captions
    new = catalog.create_revision(ep['id'], bindings, rev['payload']['segments'], rev['id'], '重新绑定分析')
    assert all(s['annotation'] is None for s in new['segments'])


def test_reference_rejects_unreviewed_speech_in_gap(catalog):
    from pathlib import Path
    from mlvideo.util import read_json
    from .helpers import register

    series, ep, rev = seed_episode(catalog)
    char = catalog.create_character(series['id'], 'A')
    bindings = dict(rev['payload']['bindings'])
    speech = read_json(Path(catalog.engine.artifact(bindings['speech'], ep['asset_sha512'])['path']))
    speech['segments'].insert(1, {'id': 'missing', 'start_sample': 120000, 'end_sample': 144000, 'text': 'Other voice', 'speaker_id': None})
    vad = read_json(Path(catalog.engine.artifact(bindings['vad'], ep['asset_sha512'])['path']))
    bindings.update(register(catalog, ep['asset_sha512'], {'speech': ('SpeechTrack.v1', speech), 'vad': ('Binary.v1', vad)}))
    new = catalog.create_revision(ep['id'], bindings, rev['payload']['segments'], rev['id'], '审核分段遗漏中间讲话')
    annotate_all(catalog, new, char)
    with pytest.raises(ValueError, match='未覆盖'):
        catalog.create_reference(new['id'], char['id'], 24000, 264000, 'Hello there. Let us go.', 'Ted', 'test')
