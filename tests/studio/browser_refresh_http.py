"""Live HTTP browser regressions; job transitions are explicit network fixtures."""
import json
import os
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

base, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
ids = json.loads((base / 'ids.json').read_text())
job = {'id': 'job_refresh_fixture', 'kind': 'PREPARE', 'state': 'QUEUED',
       'payload': {}, 'result': None, 'error_text': None, 'created_at': '2026-09-14'}
checks = []
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get('MLVIDEO_TEST_BROWSER'), headless=True)
    page = browser.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.route('**/api/jobs', lambda route: route.fulfill(json=[job]))
    page.goto('http://127.0.0.1:18787/#jobs', wait_until='networkidle')
    expect(page.locator('#content .badge')).to_have_text('排队中')
    job['state'] = 'RUNNING'
    expect(page.locator('#content .badge')).to_have_text('处理中', timeout=12000)
    job['state'] = 'SUCCEEDED'
    expect(page.locator('#content .badge')).to_have_text('执行完成', timeout=12000)
    checks.append('Queued/running/completed cards update without clicking refresh')

    page.goto('http://127.0.0.1:18787/#episode/' + ids['episode'], wait_until='networkidle')
    field = page.locator('.segment-row').first.locator('textarea[data-field=chinese]')
    field.fill('尚未保存的中文')
    page.locator('#breadcrumbs').click()
    job['state'] = 'RUNNING'
    expect(page.locator('#job-count')).to_have_text('1', timeout=12000)
    expect(field).to_have_value('尚未保存的中文')
    page.locator('.segment-row').first.get_by_role('button', name='保存标注', exact=True).click()
    page.locator('#modal [name=reason]').fill('尚未提交的审核说明')
    job['state'] = 'SUCCEEDED'
    expect(page.locator('#job-count')).to_have_text('0', timeout=12000)
    expect(page.locator('#modal')).to_be_visible()
    expect(page.locator('#modal [name=reason]')).to_have_value('尚未提交的审核说明')
    checks.append('Polling preserves unsaved segment drafts and open review forms')

    page.locator('#modal').get_by_role('button', name='取消', exact=True).click()
    page.goto('http://127.0.0.1:18787/#character/' + ids['character'], wait_until='networkidle')
    preview = page.locator('textarea[aria-label="中文测试句"]')
    preview.fill('自定义测试句，请保留。')
    page.locator('#breadcrumbs').click()
    page.evaluate("window.beforePreview = document.querySelector('textarea[aria-label=中文测试句]')")
    job['state'] = 'RUNNING'
    page.wait_for_function("() => document.querySelector('textarea[aria-label=中文测试句]') !== window.beforePreview", timeout=12000)
    expect(preview).to_have_value('自定义测试句，请保留。')
    checks.append('Automatic rerender preserves the custom voice-preview text')

    offline = [True]
    def series_response(route):
        if offline[0]:
            route.fulfill(status=500, content_type='text/plain', body='Internal Server Error')
        else:
            route.continue_()
    page.route('**/api/series', series_response)
    page.goto('http://127.0.0.1:18787/', wait_until='networkidle')
    expect(page.locator('#content')).to_contain_text('HTTP 500')
    assert 'Unexpected token' not in page.locator('#content').inner_text()
    offline[0] = False
    expect(page.locator('#content')).to_contain_text('为每个角色', timeout=12000)
    checks.append('Non-JSON 500 is readable and the workspace recovers automatically')
    assert not errors, errors
    page.screenshot(path=str(out / 'refresh-smoke.png'))
    browser.close()
(out / 'browser-refresh.json').write_text(json.dumps({'status': 'PASS', 'checks': checks,
    'console_errors': errors, 'boundary': 'Real HTTP/UI with explicit job/outage network fixtures'}, ensure_ascii=False, indent=2))
print('PASS:', len(checks), 'browser refresh checks')
