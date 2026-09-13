"""Browser workflow test against browser_seed_server.py; no real model is invoked."""
import json
import os
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

base=Path(sys.argv[1]);out=Path(sys.argv[2]);out.mkdir(parents=True,exist_ok=True)
ids=json.loads((base/'ids.json').read_text());errors=[];checks=[]
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('MLVIDEO_TEST_BROWSER'),headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
    page=browser.new_page(viewport={'width':1440,'height':1060},device_scale_factor=1)
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('http://127.0.0.1:18787',wait_until='networkidle')
    page.goto('http://127.0.0.1:18787/#series/'+ids['series'])
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
    with page.expect_response(lambda r:'/annotation' in r.url and r.request.method=='POST') as response:
        page.locator('#modal-submit').click()
    assert response.value.status==201
    page.locator('.segment-row').nth(1).get_by_text('已确认',exact=True).wait_for()
    checks.append('Select role, edit translation, save versioned annotation')
    page.locator('.segment-row').first.get_by_role('button',name='▶ 试听',exact=True).click()
    page.wait_for_function("() => document.querySelector('#audio-player').readyState >= 2")
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
    with page.expect_response(lambda r:'/revisions' in r.url and r.request.method=='POST') as response:
        page.locator('#modal-submit').click()
    assert response.value.status==201,response.value.text()
    page.wait_for_function("() => document.querySelectorAll('.segment-row').length === 3")
    checks.append('Split creates a new revision and refreshes the review list')
    page.goto('http://127.0.0.1:18787/#character/'+ids['character'])
    page.get_by_role('heading',name='优选参考音频',exact=True).wait_for()
    page.screenshot(path=str(out/'studio-character.png'),full_page=True)
    assert page.get_by_role('button',name='生成测试音',exact=True).count()==1
    checks.append('Character references and voice-profile controls render')
    assert errors==[],errors
    browser.close()
(out/'browser-smoke.json').write_text(json.dumps({'status':'PASS','checks':checks,'console_errors':errors,
    'browser':'system Chromium, Playwright','data':'EXPLICIT GENERATED TONE/UI FIXTURES; NOT real speech or MySQL'},ensure_ascii=False,indent=2))
print(json.dumps({'status':'PASS','checks':len(checks),'console_errors':errors},ensure_ascii=False))
