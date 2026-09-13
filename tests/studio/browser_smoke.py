"""Chromium DOM + real API through in-process transport; no browser HTTP claim.
Use browser_smoke_http.py in an unrestricted local environment for native HTTP.
"""
import json
import os
import base64
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

base=Path(sys.argv[1]);out=Path(sys.argv[2]);out.mkdir(parents=True,exist_ok=True)
ids=json.loads((base/'ids.json').read_text());errors=[];checks=[];requests=[]
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests')]
from studio.sqlite_protocol import SQLiteProtocolDB
from mlvideo.studio.api import create_app
from fastapi.testclient import TestClient
config={'data_root':str(base/'data'),'test_sqlite':str(base/'test.sqlite'),
    'database':{'host':'TEST','port':0,'database':'UI_FIXTURE_NOT_MYSQL'}}
client=TestClient(create_app(config,SQLiteProtocolDB),base_url='http://127.0.0.1:18787')
def transport(url, options=None):
    options=options or {}
    method=options.get('method','GET')
    response=client.request(method,url,headers=options.get('headers',{}),content=options.get('body'))
    requests.append({'path':url,'method':method,'status':response.status_code})
    return {'status':response.status_code,'headers':dict(response.headers),'body':base64.b64encode(response.content).decode()}

with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
    page=browser.new_page(viewport={'width':1440,'height':1060},device_scale_factor=1)
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.expose_function('__apiTransport',transport)
    page.goto('about:blank')
    html=(ROOT/'src/mlvideo/studio/static/index.html').read_text()
    html=html.replace('<link rel="stylesheet" href="/static/studio.css">','').replace('<script src="/static/studio.js" defer></script>','')
    page.set_content(html)
    page.add_style_tag(content=(ROOT/'src/mlvideo/studio/static/studio.css').read_text())
    page.evaluate("""() => {
        const store=new Map();
        Object.defineProperty(window,'localStorage',{value:{getItem:k=>store.get(k)||null,setItem:(k,v)=>store.set(k,String(v)),removeItem:k=>store.delete(k)}});
        window.fetch=async (url,options={})=>{const r=await window.__apiTransport(String(url),options);return new Response(Uint8Array.from(atob(r.body),c=>c.charCodeAt(0)),{status:r.status,headers:r.headers});};
        const original=Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype,'src');
        Object.defineProperty(HTMLMediaElement.prototype,'src',{get:original.get,set:function(url){if(String(url).startsWith('/api/')){fetch(url).then(r=>r.blob()).then(blob=>{original.set.call(this,URL.createObjectURL(blob));this.load();this.play().catch(()=>{});});}else original.set.call(this,url);}});
    }""")
    page.add_script_tag(content=(ROOT/'src/mlvideo/studio/static/studio.js').read_text())
    page.get_by_role('heading',name='为每个角色，固定一种声音。',exact=True).wait_for()
    page.evaluate('(x)=>location.hash=x','series/'+ids['series'])
    page.get_by_role('heading',name='Rocket Girl · 界面测试',exact=True).wait_for()
    page.screenshot(path=str(out/'studio-series.png'),full_page=True)
    page.get_by_role('button',name='＋ 新建角色',exact=True).click()
    page.locator('#modal [name=name]').fill('新角色（浏览器测试）')
    page.locator('#modal [name=notes]').fill('通过浏览器创建，未运行模型')
    page.locator('#modal-submit').click()
    page.get_by_role('heading',name='新角色（浏览器测试）',exact=True).wait_for()
    checks.append('Create character through the real browser/API')
    page.get_by_role('button',name='打开审核页',exact=True).click()
    page.locator('.segment-row').nth(1).wait_for()
    row=page.locator('.segment-row').nth(1)
    row.locator('select[data-field=role]').select_option(ids['character'])
    row.locator('textarea[data-field=chinese]').fill('我们一起出发吧。')
    row.get_by_role('button',name='保存标注',exact=True).click()
    page.locator('#modal [name=reason]').fill('浏览器自动化验证保存标注，不是听感鉴定')
    page.locator('#modal-submit').click()
    page.locator('.segment-row').nth(1).get_by_text('已确认',exact=True).wait_for()
    checks.append('Select role, edit translation, save versioned annotation')
    page.locator('.segment-row').first.get_by_role('button',name='▶ 试听',exact=True).click()
    page.wait_for_function("document.querySelector('#audio-player').readyState >= 2")
    duration=page.locator('#audio-player').evaluate('(x)=>x.duration')
    assert abs(duration-1.5)<.02,duration
    page.locator('#audio-player').evaluate('(x)=>x.pause()')
    checks.append('Real WAV streaming playback / duration')
    page.locator('#segment-search').fill('Hello')
    assert page.locator('.segment-row:not(.hidden)').count()==1
    page.locator('#segment-search').fill('')
    checks.append('Client-side filtering without losing edited rows')
    page.screenshot(path=str(out/'studio-episode.png'),full_page=True)
    # Exercise actual split control and revision creation (no direct DB/API writes).
    page.locator('.segment-row').nth(1).get_by_role('button',name='拆分 / 改边界',exact=True).click()
    page.locator('#modal [name=split]').fill('4.8')
    page.locator('#modal [name=first]').fill("Let's")
    page.locator('#modal [name=second]').fill('go!')
    page.locator('#modal [name=reason]').fill('分段操作的浏览器测试')
    page.locator('#modal-submit').click()
    page.wait_for_function("document.querySelectorAll('.segment-row').length === 3")
    checks.append('Split creates a new revision and refreshes the review list')
    page.evaluate('(x)=>location.hash=x','character/'+ids['character'])
    page.get_by_role('heading',name='优选参考音频',exact=True).wait_for()
    page.screenshot(path=str(out/'studio-character.png'),full_page=True)
    assert page.get_by_role('button',name='生成测试音',exact=True).count()==1
    checks.append('Character references and voice-profile controls render')
    assert errors==[],errors
    assert not [r for r in requests if r['status']>=400],requests
    browser.close()
(out/'browser-smoke.json').write_text(json.dumps({'status':'PASS','checks':checks,'console_errors':errors,
    'browser':'system Chromium DOM, Playwright + FastAPI TestClient transport (native browser HTTP blocked)', 'requests':requests,'data':'EXPLICIT GENERATED TONE/UI FIXTURES; NOT real speech or MySQL'},ensure_ascii=False,indent=2))
print(json.dumps({'status':'PASS','checks':len(checks),'console_errors':errors},ensure_ascii=False))
