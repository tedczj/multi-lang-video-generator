"""Local browser-test fixture server. NOT a production/demo data importer."""
import json
import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests')]
os.environ['PYTHONPATH']=str(ROOT/'src')
from studio.sqlite_protocol import SQLiteProtocolDB
from studio.helpers import seed_episode, run_next
from mlvideo.studio.catalog import Catalog,REFERENCE_CHECKS
from mlvideo.studio.api import create_app
from mlvideo.util import atomic_json

base=Path(sys.argv[1]).resolve();base.mkdir(parents=True,exist_ok=True)
config={'data_root':str(base/'data'),'test_sqlite':str(base/'test.sqlite'),'worker_timeout':90,
    'database':{'host':'TEST','port':0,'database':'UI_FIXTURE_NOT_MYSQL'},
    'models':{'N11/cosyvoice3_zero_shot':[{'model_id':'FunAudioLLM/Fun-CosyVoice3-0.5B-2512','python':sys.executable,
        'source_dir':'/TEST-NO-MODEL','model_dir':'/TEST-NO-WEIGHTS'}]}}
Path(config['data_root']).mkdir(exist_ok=True)
db=SQLiteProtocolDB(config);db.migrate();cat=Catalog(db,config)
series,ep,rev=seed_episode(cat,title='Episode 01 · 界面测试素材')
cat.rename('series',series['id'],'Rocket Girl · 界面测试')
cat.create_series('Journey to the West · 界面测试','Little Fox')
char=cat.create_character(series['id'],'Rocket Girl','界面测试角色。音频是合成提示音，不是真实人物原声。')
cat.create_character(series['id'],'Narrator','界面测试旁白')
s=rev['segments'][0]
cat.annotate(rev['id'],s['id'],0,char['id'],'CONFIRMED',s['text'],'你好呀！','UI 测试','界面测试，不是人声鉴定')
ref=cat.create_reference(rev['id'],char['id'],s['start_sample'],s['end_sample'],s['text'],'UI 测试','合成提示音，测试参考审批交互')
run_next(cat)
cat.approve_reference(ref['id'],'UI 测试','仅测试状态机，不代表克隆质量',dict.fromkeys(REFERENCE_CHECKS,True))
cat.create_profile(char['id'],ref['id'],'标准语气 · 界面测试')
atomic_json(base/'ids.json',{'series':series['id'],'episode':ep['id'],'character':char['id'],'revision':rev['id']})
db.close()
import uvicorn
uvicorn.run(create_app(config,SQLiteProtocolDB),host='127.0.0.1',port=18787,log_level='warning')
