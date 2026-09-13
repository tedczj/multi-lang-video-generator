import json
from pathlib import Path
import pytest
from mlvideo.studio.catalog import CLIP_CHECKS, VOICE_CHECKS
from mlvideo.studio.jobs import execute_next
from mlvideo.util import sha512, read_json
from .helpers import seed_episode, annotate_all, profile_ready, fake_tts, run_next


def test_cross_episode_generation_retry_selection_and_real_render(catalog,monkeypatch):
    calls,_=fake_tts(monkeypatch)
    font=Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    # Platform-local test font; never distributed in the implementation archive.
    if not font.is_file():
        import os
        font=Path(os.environ['MLVIDEO_TEST_LATIN_FONT'])
    catalog.config['models']['N17/bilingual']=[{'font_path':str(font),'sha512':sha512(font),'source':'system test font'}]
    catalog.config['studio']={'render_settings':{'font_size':16}}
    series,ep1,rev1=seed_episode(catalog)
    char=catalog.create_character(series['id'],'A')
    profile,_=profile_ready(catalog,ep1,rev1,char)
    _,ep2,rev2=seed_episode(catalog,series,frequency=550,title='Second fixture episode')
    annotate_all(catalog,rev2,char,chinese=False)
    plan=catalog.create_plan(ep2['id'],rev2['id'],'TEST','Explicit test boundary review');run_next(catalog)
    job1=catalog.generate(plan['id']);run_next(catalog)
    result1=catalog.get('studio_jobs',job1['id'])['result']
    assert len(result1['units'])==2 and len(calls)==3  # preview + two actual boundary calls
    # Ordinary inputs cannot cross assets; approved importer is the only capability.
    source_ref=profile['payload']['references']['audio']
    with pytest.raises(ValueError,match='cross-asset'):
        catalog.engine.artifact(source_ref['artifact_id'],ep2['asset_sha512'])
    imports=[r for r in result1['executions'].values() if any('import' in a['relative_path'] for a in r['artifacts'])]
    assert imports
    imported=imports[0]
    parents=catalog.db.query('SELECT artifact_id FROM execution_inputs WHERE execution_id=%s',(imported['execution_id'],))
    assert source_ref['artifact_id'] in {p['artifact_id'] for p in parents}
    req=read_json(catalog.root/catalog.db.one('SELECT request_path FROM executions WHERE id=%s',(imported['execution_id'],))['request_path'])
    assert req['inputs']['audio'][0]['asset_sha512']==ep1['asset_sha512']
    assert req['asset_sha512']==ep2['asset_sha512']
    for u in result1['units']:
        catalog.select_clip(plan['id'],job1['id'],u['unit_id'],'TEST','TEST only',dict.fromkeys(CLIP_CHECKS,True))
    job2=catalog.retry_job(job1['id']);run_next(catalog)
    result2=catalog.get('studio_jobs',job2['id'])['result']
    assert len(calls)==5
    assert all(x['job_id']==job1['id'] for x in catalog.selections(plan['id']).values())
    assert [u['audio'] for u in result1['units']] != [u['audio'] for u in result2['units']]
    rerender=catalog.render(plan['id'])
    # Changing the UI selection AFTER enqueue must not change the queued render.
    u=result2['units'][0]
    catalog.select_clip(plan['id'],job2['id'],u['unit_id'],'TEST','new selection',dict.fromkeys(CLIP_CHECKS,True))
    assert rerender['payload']['selections'][u['unit_id']]['job_id']==job1['id']
    run_next(catalog)
    rendered=catalog.get('studio_jobs',rerender['id'])['result']
    assert rendered['state']=='REVIEW'
    preview=catalog.engine.artifact(rendered['render']['preview'],ep2['asset_sha512'])
    assert Path(preview['path']).stat().st_size>1000
    qa=read_json(Path(catalog.engine.artifact(rendered['render']['qa'],ep2['asset_sha512'])['path']))
    assert qa['overall']!='FAIL'
    timeline=read_json(Path(catalog.engine.artifact(rendered['timeline']['timeline'],ep2['asset_sha512'])['path']))
    assert len(timeline['dubs'])==2 and timeline['output_samples']>=timeline['source_samples']


def test_cross_series_import_and_disabled_reference_are_blocked(catalog,monkeypatch):
    fake_tts(monkeypatch);series,ep,rev=seed_episode(catalog);c=catalog.create_character(series['id'],'A')
    p,ref=profile_ready(catalog,ep,rev,c)
    other=catalog.create_series('Other');_,ep2,_=seed_episode(catalog,other,frequency=660)
    with pytest.raises(ValueError,match='outside'):
        catalog.engine.run(ep2['asset_sha512'],'N10','studio_import_reference',
            {k:p['payload']['references'][k]['artifact_id'] for k in ('audio','reference')},
            {'profile_id':p['id'],'profile_sha512':p['payload_sha512'],'purpose':'dubbing'})
    catalog.disable_reference(ref['id'],'TEST','contaminated')
    with pytest.raises(ValueError):catalog.preview(p['id'],'你好')


def test_failed_model_requires_explicit_retry_and_preserves_attempt(catalog,monkeypatch):
    calls,stub=fake_tts(monkeypatch);series,ep,rev=seed_episode(catalog);c=catalog.create_character(series['id'],'A')
    profile_ready(catalog,ep,rev,c)
    plan=catalog.create_plan(ep['id'],rev['id'],'TEST','test boundaries');run_next(catalog)
    job=catalog.generate(plan['id'],[rev['segments'][0]['id']]);stub.fail=True
    assert execute_next(catalog)
    failed=catalog.get('studio_jobs',job['id']);assert failed['state']=='FAILED'
    before=len(calls);assert not execute_next(catalog);assert len(calls)==before
    stub.fail=False
    retried=catalog.retry_job(job['id']);assert execute_next(catalog)
    assert catalog.get('studio_jobs',retried['id'])['state']=='SUCCEEDED'
    assert catalog.get('studio_jobs',job['id'])['state']=='FAILED'
    assert calls[-1]['retry_of']==calls[-2]['execution_id']
