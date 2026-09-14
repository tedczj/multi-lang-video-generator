import io
import wave
import pytest
from fastapi.testclient import TestClient
from mlvideo.studio.api import create_app
from .sqlite_protocol import SQLiteProtocolDB
from .helpers import seed_episode


@pytest.fixture
def client(catalog,config):
    with TestClient(create_app(config,SQLiteProtocolDB,allowed_hosts={'testserver'})) as c:
        c.headers['X-Studio-Token']=c.get('/api/session').json()['token']
        yield c


def test_api_series_and_strict_fields(client):
    assert client.post('/api/series',json={'name':'Rocket Girl','brand':'Little Fox','python':'/bin/sh'}).status_code==422
    r=client.post('/api/series',json={'name':'Rocket Girl','brand':'Little Fox'})
    assert r.status_code==201
    s=r.json()
    c=client.post('/api/series/'+s['id']+'/characters',json={'name':'Narrator','notes':'旁白'})
    assert c.status_code==201
    assert client.get('/api/series/'+s['id']).json()['characters'][0]['name']=='Narrator'


def test_original_mp4_media_type_and_bytes(client, catalog):
    from mlvideo.config import ROOT
    from mlvideo.store import ingest
    from mlvideo.phase2_pipeline import ports
    source = ROOT / 'tests/fixtures/generated/rotate.mp4'
    acquired = ingest(catalog.engine, source)
    asset = acquired['asset_sha512']
    ref = acquired['source_artifact_id']
    probe = ports(catalog.engine.run(asset, 'N03', 'ffprobe', {'source': ref}, {}))
    c = ports(catalog.engine.run(asset, 'N04', 'original', {'source': ref, 'probe': probe['probe']}, {}))
    response = client.get('/api/media/' + c['video'])
    assert response.status_code == 200
    assert response.headers['content-type'] == 'video/mp4'
    assert response.content == source.read_bytes()


def test_csrf_origin_and_host(client):
    assert client.post('/api/series',json={'name':'blocked'},headers={'X-Studio-Token':''}).status_code==403
    assert client.post('/api/series',json={'name':'blocked'},headers={'Origin':'https://evil.test'}).status_code==403
    assert client.get('/api/session',headers={'Host':'evil.test'}).status_code==403
    assert client.get('/api/session',headers={'Sec-Fetch-Site':'cross-site'}).status_code==403
    assert client.get('/').headers['content-security-policy'].find("frame-ancestors 'none'")>=0
    assert client.post('/api/series',content='x',headers={'Content-Type':'text/plain'}).status_code==415


def test_input_html_is_data_not_executable(client):
    s=client.post('/api/series',json={'name':'<script>bad()</script>'}).json()
    assert client.get('/api/series/'+s['id']).json()['name']=='<script>bad()</script>'
    js=client.get('/static/studio.js')
    assert js.status_code==200 and "esc(s.name)" in js.text
    assert 'script-src' in client.get('/').headers['content-security-policy']


def test_audio_pcm_slice_and_ranges(client,catalog):
    series,ep,rev=seed_episode(catalog)
    audio=rev['payload']['bindings']['audio']
    path=catalog.engine.artifact(audio,ep['asset_sha512'])['path']
    query=f'/api/audio/{audio}/clip?start_sample=24000&end_sample=96000'
    full=client.get(query)
    assert full.status_code==200
    with wave.open(path) as source:
        source.setpos(24000); expected=source.readframes(72000)
    with wave.open(io.BytesIO(full.content)) as sliced:
        assert sliced.getnframes()==72000
        assert sliced.readframes(72000)==expected
    for bounds in [(0,43),(41,77),(100,1000),(45,999)]:
        a,b=bounds;r=client.get(query,headers={'Range':f'bytes={a}-{b}'})
        assert r.status_code==206 and r.content==full.content[a:b+1]
    suffix=client.get(query,headers={'Range':'bytes=-30'})
    assert suffix.content==full.content[-30:]
    assert client.head(query).content==b''
    assert client.get(query,headers={'Range':'bytes=999999999-'}).status_code==416
    assert client.get(query,headers={'Range':'bytes=0-3,9-10'}).status_code==416
    assert client.get(f'/api/audio/{audio}/clip?start_sample=-1&end_sample=100').status_code==400
    assert client.get('/api/media/'+ep['source_artifact_id']).status_code==400
    assert client.get('/api/media/missing').status_code==400


def test_annotation_conflict_api_and_invalid_cross_series(client,catalog):
    s,ep,rev=seed_episode(catalog); c=catalog.create_character(s['id'],'A')
    row=rev['segments'][0]
    body={'expected_version':0,'character_id':c['id'],'status':'CONFIRMED','english_text':row['text'],'chinese_text':'你好',
          'reviewer':'Ted','reason':'已核对'}
    route=f"/api/revisions/{rev['id']}/segments/{row['id']}/annotation"
    assert client.post(route,json=body).status_code==201
    assert client.post(route,json=body).status_code==409
    other=catalog.create_series('Other');cc=catalog.create_character(other['id'],'A')
    body.update(expected_version=1,character_id=cc['id'])
    assert client.post(route,json=body).status_code==400


def test_static_scripts_no_external_dependencies(client):
    html=client.get('/').text
    assert '/static/studio.js' in html
    assert 'https://' not in html
    assert client.get('/static/studio.css').status_code==200
    assert client.get('/static/../catalog.py').status_code==404


def test_review_actions_without_personal_name(client, catalog):
    from mlvideo.studio.api import ReferenceIn, ReviewIn, DisableIn, PlanIn
    series, ep, rev = seed_episode(catalog)
    char = catalog.create_character(series['id'], 'A')
    segment = rev['segments'][0]
    r = client.post(f"/api/revisions/{rev['id']}/segments/{segment['id']}/annotation", json={
        'expected_version': 0, 'character_id': char['id'], 'status': 'CONFIRMED',
        'english_text': segment['text'], 'chinese_text': '你好', 'reason': '已检查'})
    assert r.status_code == 201
    assert r.json()['reviewer'] == 'local-user'
    assert 'id="reviewer"' not in client.get('/').text
    for model in (ReferenceIn, ReviewIn, DisableIn, PlanIn):
        assert model.model_fields['reviewer'].default == 'local-user'
    ref = client.post('/api/references', json={
        'revision_id': rev['id'], 'character_id': char['id'],
        'start_sample': segment['start_sample'], 'end_sample': segment['end_sample'],
        'transcript': segment['text'], 'reason': '已检查'})
    assert ref.status_code == 201
    assert ref.json()['payload']['reviewer'] == 'local-user'


@pytest.mark.parametrize("error_type", ["OperationalError", "InterfaceError"])
def test_database_outage_returns_json_and_recovers(config, error_type):
    from pymysql import err
    database_error = getattr(err, error_type)
    unavailable = True
    def factory(c):
        if unavailable:
            raise database_error(2003, 'test connection refused')
        db = SQLiteProtocolDB(c)
        db.migrate()
        return db
    with TestClient(create_app(config, factory, allowed_hosts={'testserver'})) as client:
        response = client.get('/api/series')
        assert response.status_code == 503
        assert response.json()['type'] == 'database_unavailable'
        assert 'test connection refused' not in response.text
        unavailable = False
        assert client.get('/api/series').json() == []
