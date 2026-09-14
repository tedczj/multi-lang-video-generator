from pathlib import Path
import json
import pytest
from mlvideo.backup import backup,restore,TABLES
from mlvideo.studio.catalog import Catalog,VOICE_CHECKS
from mlvideo.studio.jobs import execute_next
from mlvideo.util import file_lock
from .helpers import seed_episode, profile_ready, fake_tts
from .sqlite_protocol import SQLiteProtocolDB


def test_backup_contains_and_restores_studio(catalog,tmp_path):
    s=catalog.create_series('Backup series');catalog.create_character(s['id'],'A')
    _, ep, rev = seed_episode(catalog, series=s)
    note = catalog.save_note(rev['id'], rev['segments'][0]['id'], 0, '独立备注必须随备份恢复')
    with file_lock(catalog.root/'.writer.lock'):
        out=tmp_path/'backup';result=backup(catalog.engine,out)
    assert 'studio_profiles' in result['tables'] and result['tables']['studio_characters']==1
    target=dict(catalog.config,data_root=str(tmp_path/'restored'),test_sqlite=str(tmp_path/'restored.sqlite'))
    target['database']={**target['database'],'database':'RESTORE_ISOLATED'}
    Path(target['data_root']).mkdir()
    db=SQLiteProtocolDB(target);db.migrate()
    try:
        cat=Catalog(db,target)
        with file_lock(cat.root/'.writer.lock'):restore(cat.engine,out)
        assert cat.rows('studio_series')[0]['name']=='Backup series'
        assert cat.rows('studio_characters')[0]['series_id']==s['id']
        assert cat.revision_view(rev['id'])['segments'][0]['note']['notes'] == note['notes']
    finally:db.close()


def test_profile_rejects_another_profiles_preview(catalog,monkeypatch):
    fake_tts(monkeypatch);s,ep,rev=seed_episode(catalog);c=catalog.create_character(s['id'],'A')
    p,ref=profile_ready(catalog,ep,rev,c)
    p2=catalog.create_profile(c['id'],ref['id'],'Second draft')
    j=catalog.rows('studio_jobs',"kind='PREVIEW'")[0]
    with pytest.raises(ValueError):catalog.publish_profile(p2['id'],j['id'],'TEST','wrong binding',dict.fromkeys(VOICE_CHECKS,True))


def test_unknown_job_transition_is_rejected(catalog):
    s=catalog.create_series('S');ep=catalog.create_episode(s['id'],'E','https://youtu.be/Ye33eY4UNtY')
    j=catalog.prepare(ep['id'])
    with pytest.raises(ValueError):catalog.finish_job(j['id'],'SUCCEEDED',{})
    assert catalog.get('studio_jobs',j['id'])['state']=='QUEUED'


def test_qa_fail_is_persisted_and_cannot_be_consumed(catalog,monkeypatch):
    _,stub=fake_tts(monkeypatch);s,ep,rev=seed_episode(catalog);c=catalog.create_character(s['id'],'A')
    p,_=profile_ready(catalog,ep,rev,c)
    stub.silence=True
    job=catalog.preview(p['id'],'你好');assert execute_next(catalog)
    result=catalog.get('studio_jobs',job['id'])['result']
    assert result['quality_status']=='FAIL'
    execution=catalog.db.one('SELECT execution_id FROM artifacts WHERE id=%s',(result['qa'],))['execution_id']
    assert catalog.engine.result(execution)['quality_status']=='FAIL'
    with pytest.raises(ValueError,match='failed quality checks'):catalog.engine.artifact(result['audio'],ep['asset_sha512'])
    with pytest.raises(ValueError):catalog.publish_profile(p['id'],job['id'],'TEST','must reject silent clip',dict.fromkeys(VOICE_CHECKS,True))


def test_worker_restart_preserves_interrupted_job(catalog):
    from mlvideo.studio.jobs import worker_loop

    series = catalog.create_series('Restart test')
    episode = catalog.create_episode(series['id'], 'E', 'https://youtu.be/Ye33eY4UNtY')
    job = catalog.prepare(episode['id'])
    catalog.finish_job(job['id'], 'RUNNING')
    catalog.job_progress(job['id'], {'executions': {}, 'current_step': 'speech'})
    worker_loop(catalog.config, once=True, db_factory=SQLiteProtocolDB)
    interrupted = catalog.get('studio_jobs', job['id'])
    assert interrupted['state'] == 'INTERRUPTED'
    assert interrupted['result']['current_step'] == 'speech'
    assert len(catalog.rows('studio_jobs')) == 1


def test_restore_pre_studio_backup(catalog, tmp_path):
    from mlvideo.studio.catalog import TABLES as STUDIO_TABLES
    from mlvideo.util import atomic_json, read_json, sha512

    out = tmp_path / 'legacy-backup'
    with file_lock(catalog.root / '.writer.lock'):
        backup(catalog.engine, out)
    dump = read_json(out / 'database.json')
    for table in STUDIO_TABLES:
        del dump[table]
    dump['schema_migrations'] = [r for r in dump['schema_migrations'] if r['version'] == 1]
    atomic_json(out / 'database.json', dump)
    manifest = read_json(out / 'manifest.json')
    manifest['dump_sha512'] = sha512(out / 'database.json')
    atomic_json(out / 'manifest.json', manifest)
    config = dict(catalog.config, data_root=str(tmp_path / 'restored'), test_sqlite=str(tmp_path / 'restore.sqlite'))
    config['database'] = dict(config['database'], database='LEGACY_RESTORE_ISOLATED')
    Path(config['data_root']).mkdir()
    db = SQLiteProtocolDB(config)
    try:
        db.migrate()
        cat = Catalog(db, config)
        with file_lock(cat.root / '.writer.lock'):
            result = restore(cat.engine, out)
        assert all(result['tables'][table] == 0 for table in STUDIO_TABLES)
        assert len(db.query('SELECT * FROM schema_migrations')) == 3
    finally:
        db.close()


@pytest.mark.parametrize("error_type", ["OperationalError", "InterfaceError"])
def test_worker_reconnects_without_replaying_claimed_job(catalog, monkeypatch, error_type):
    from pymysql import err
    database_error = getattr(err, error_type)
    from mlvideo.studio import jobs
    series = catalog.create_series('Reconnect test')
    ep = catalog.create_episode(series['id'], 'E', 'https://youtu.be/Ye33eY4UNtY')
    job = catalog.prepare(ep['id'])
    catalog.finish_job(job['id'], 'RUNNING')
    connections = []
    def factory(config):
        connections.append(True)
        if len(connections) == 1:
            raise database_error(2003, 'database offline')
        return SQLiteProtocolDB(config)
    def execute(cat):
        assert cat.get('studio_jobs', job['id'])['state'] == 'INTERRUPTED'
        assert len(cat.rows('studio_jobs')) == 1
        raise KeyboardInterrupt
    monkeypatch.setattr(jobs, 'execute_next', execute)
    monkeypatch.setattr(jobs.time, 'sleep', lambda seconds: None)
    with pytest.raises(KeyboardInterrupt):
        jobs.worker_loop(catalog.config, db_factory=factory)
    assert len(connections) == 2
