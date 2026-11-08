"""UI + HTTP + database verification using an isolated temporary test database.

Never writes test selections or test booking codes to the running application.
Run from the repository root with the backend virtualenv Python.
"""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'tests')]
from fastapi import FastAPI
from fastapi.testclient import TestClient
from playwright.async_api import async_playwright
import db
import high_odds_service as service
from high_odds_api import build_high_odds_router
from test_high_odds_service import HighOddsServiceTests


async def verify(client,category_ids):
    requests=[];errors=[]
    async with async_playwright() as playwright:
        browser=await playwright.chromium.launch(headless=True)
        page=await browser.new_page(viewport={'width':1600,'height':1100})
        page.on('pageerror',lambda error:errors.append(str(error)))
        async def route_api(route):
            request=route.request;url=urlsplit(request.url)
            if url.path.startswith('/api/sports-ai/high-odds'):
                requests.append(url.path)
                response=client.request(request.method,url.path+('?' + url.query if url.query else ''),content=request.post_data,
                    headers={'content-type':'application/json'})
                await route.fulfill(status=response.status_code,content_type='application/json',body=response.text)
            else:
                await route.fulfill(status=200,content_type='application/json',body='[]' if any(url.path.endswith(p) for p in ('/predictions','/tickets','/fixtures')) else '{}')
        await page.route('**/api/sports-ai/**',route_api)
        await page.goto('http://127.0.0.1:5173/sports/high-odds')
        await page.locator('.ho-workspace tbody tr').first.wait_for()
        await page.add_style_tag(content='body::before{content:"ISOLATED TEST DATA — NOT LIVE RECOMMENDATIONS";position:fixed;bottom:0;left:0;right:0;z-index:9999;background:#fff2bd;color:#614a09;text-align:center;padding:6px;font:12px Arial}')
        assert await page.locator('.ho-categories>button').count()==8
        checked=[]
        for category in category_ids:
            response=page.expect_response(lambda r:'/high-odds?' in r.url and 'category_id='+category in r.url)
            async with response:
                await page.locator('.ho-filters select').nth(1).select_option(category)
            await page.get_by_role('button',name='View HO-',exact=False).first.wait_for()
            count=await page.locator('.ho-workspace tbody tr').count()
            assert count>0,category
            await page.get_by_role('button',name='View HO-',exact=False).first.click()
            await page.get_by_role('dialog').wait_for()
            assert await page.locator('.ho-legs article').count()>=2
            await page.keyboard.press('Escape')
            checked.append({'category':category,'visible_tickets':count,'snapshot_opened':True})
        async with page.expect_response(lambda r:'/high-odds?' in r.url and 'category_id=' not in r.url):
            await page.locator('.ho-filters select').nth(1).select_option('')
        await page.screenshot(path=str(ROOT/'reports/high-odds-e2e-tickets.png'),full_page=True)
        await page.get_by_role('tab',name='Category performance').click()
        assert await page.locator('.ho-report tbody tr').count()==8
        await page.get_by_role('tab',name='Worker & audit').click()
        assert await page.locator('.ho-audit').count()>0
        await page.get_by_role('tab',name='Ticket portfolio').click()
        before=len(requests)
        await page.wait_for_timeout(16000)
        assert len(requests)>before,'Automatic refresh did not run'
        await page.set_viewport_size({'width':390,'height':844})
        assert not await page.evaluate('document.documentElement.scrollWidth>innerWidth')
        await page.screenshot(path=str(ROOT/'reports/high-odds-e2e-mobile.png'),full_page=True)
        await browser.close()
    assert not errors,errors
    return {'categories':checked,'page_errors':errors,'automatic_refresh':True,'mobile_overflow':False,
        'api_requests':len(requests),'data_source':'isolated temporary test database','bookmaker_codes_created':0}


def main():
    case=HighOddsServiceTests('test_all_eight_categories_end_to_end_and_no_shared_matches')
    case.setUp()
    try:
        with db.connect() as c:
            case.seed(c,150)
            c.execute('UPDATE sports_ai_high_odds_categories SET max_active=2')
            reports=service.generate_event(c,case.event(c),case.now)
            assert len(reports)==8 and all(r['created_ids'] for r in reports)
            service.queue_registration(c,case.now)
        app=FastAPI();app.include_router(build_high_odds_router(lambda:'isolated-test-operator',lambda key:None))
        with patch.object(service,'now_utc',side_effect=lambda now=None:now or case.now):
            with TestClient(app) as client:
                ids=[c['id'] for c in client.get('/api/sports-ai/high-odds').json()['categories']]
                result=asyncio.run(verify(client,ids))
        (ROOT/'reports/high-odds-e2e-check.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result,indent=2))
    finally:case.tearDown()


if __name__=='__main__':main()
