"""Browser checks use intercepted fixtures and never write test records to the live backend."""
import asyncio,json
from datetime import datetime,timedelta,timezone
from pathlib import Path
from playwright.async_api import async_playwright
ROOT=Path(__file__).resolve().parents[1]
IDS=['2-4.99','5-9.99','10-24.99','25-49.99','50-99.99','100-249.99','250-499.99','500-1000']
async def main():
 now=datetime.now(timezone.utc);today=now.astimezone(timezone(timedelta(hours=1))).date().isoformat()
 tickets=[]
 for i,cat in enumerate(IDS):
  tickets.append(dict(ticket_id=f'isolated-{i}',ticket_no=f'HO-TEST-{i}',category_id=cat,created_at=now.isoformat(),booking_code=f'TEST{i}CODE',registration_state='registered',code_status='verified',leg_count=3,combined_odds=[3.24,7.23,18.60,42.75,76.8,186.4,428.9,936.8][i],joint_probability=.648/(i+1),confidence=.85 if i%2==0 else .7,status='qualified',outcome='won' if i==0 else 'pending',hypothetical_profit=2.24 if i==0 else None,stake=100,start_at=now.isoformat(),legs=[dict(home='Test Home',away='Test Away',competition='Isolated browser fixture',market='home_win',starts_at=now.isoformat(),decimal_odds=1.48,model_reliability=.9)]))
 tickets.append({**tickets[0],'ticket_id':'unverified','ticket_no':'HO-UNVERIFIED','booking_code':'UNVERIFIED','registration_state':'pending'})
 tickets.append({**tickets[0],'ticket_id':'yesterday','booking_code':'PRIOR-DAY','created_at':(now-timedelta(days=1)).isoformat()})
 fixture=dict(worker={'healthy':True},tickets=tickets,categories=[dict(id=c,total=1,active=1,settled=1 if i==0 else 0,wins=1 if i==0 else 0,hypothetical_profit=2.24 if i==0 else None) for i,c in enumerate(IDS)],audit=[dict(action='event.completed',created_at=now.isoformat(),details_json='{"isolated_browser_fixture": true}')])
 overview=dict(status='running',next_cycle=(now+timedelta(minutes=5)).isoformat(),tasks=[dict(kind=k,enabled=True,status='Running',started=now.isoformat(),next_run_at=(now+timedelta(minutes=5)).isoformat()) for k in ['collection','browser_queue','prediction','accumulator','booking_queue','settlement']])
 results={};errors=[];requests=[]
 async with async_playwright() as p:
  browser=await p.chromium.launch(headless=True)
  context=await browser.new_context(viewport={'width':1536,'height':1024},permissions=['clipboard-read','clipboard-write'])
  page=await context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
  async def route(r):
   requests.append((r.request.method,r.request.url))
   if r.request.url.endswith('/generate'):
    assert r.request.method=='POST';results['optimizer_request']=json.loads(r.request.post_data);await r.fulfill(json={'status':'queued'});return
   await r.fulfill(json=overview if '/autonomous/overview' in r.request.url else fixture if '/high-odds' in r.request.url else [])
  await page.route('**/api/sports-ai/**',route)
  await page.goto('http://localhost:5173/sports/high-odds')
  await page.locator('.bcd-portfolio tbody tr').nth(7).wait_for()
  assert await page.locator('.bcd-category').count()==8
  assert await page.locator('.bcd-jackpot').count()==1
  assert await page.locator('.bcd-portfolio tbody tr').count()==8
  assert await page.locator('.bcd-portfolio').get_by_text('UNVERIFIED',exact=True).count()==0
  assert await page.locator('.bcd-portfolio').get_by_text('PRIOR-DAY',exact=True).count()==0
  await page.add_style_tag(content='body::after{content:"ISOLATED BROWSER TEST DATA";position:fixed;bottom:0;left:0;background:#fff1b8;padding:4px 10px;z-index:9999;font:10px Arial;color:#633c00}')
  await page.screenshot(path=str(ROOT/'reports/booking-dashboard-fixture-desktop.png'),full_page=True)
  await page.get_by_label('Category',exact=True).select_option(IDS[3]);assert await page.locator('.bcd-portfolio tbody tr').count()==1
  await page.locator('.bcd-category').nth(3).click();assert await page.locator('.bcd-portfolio tbody tr').count()==8
  await page.get_by_label('Search booking code',exact=True).fill('TEST2');assert await page.locator('.bcd-portfolio tbody tr').count()==1
  await page.get_by_label('Search booking code',exact=True).fill('')
  await page.get_by_label('Status',exact=True).select_option('won');assert await page.locator('.bcd-portfolio tbody tr').count()==1
  await page.get_by_label('Status',exact=True).select_option('')
  await page.get_by_role('button',name='View HO-TEST-0',exact=True).click();await page.get_by_role('dialog').wait_for()
  await page.get_by_role('button',name='Copy booking code',exact=True).click();assert await page.evaluate('navigator.clipboard.readText()')=='TEST0CODE'
  await page.keyboard.press('Escape');assert await page.get_by_role('dialog').count()==0
  await page.get_by_role('button',name='More actions for HO-TEST-0',exact=True).click();await page.get_by_role('button',name='View selections',exact=True).click();await page.get_by_role('dialog').wait_for();await page.get_by_role('button',name='Close ticket').click()
  await page.get_by_label('Booking date',exact=True).fill((now.astimezone(timezone(timedelta(hours=1))).date()-timedelta(days=1)).isoformat());assert await page.locator('.bcd-portfolio tbody tr').count()==1
  await page.get_by_label('Booking date',exact=True).fill(today)
  for name in ['Category Performance','Generation Log','Automation Workers','Settlements','Analytics']:
   await page.get_by_role('tab',name=name,exact=True).click();assert await page.get_by_role('tabpanel').is_visible()
  await page.get_by_role('button',name='View Engine Logs',exact=True).click();await page.get_by_role('button',name='Run optimizer',exact=True).click();await page.get_by_role('status').get_by_text('Optimizer queued',exact=False).wait_for()
  assert results['optimizer_request']['idempotency_key']
  await page.get_by_role('tab',name='SportyBet Booking Codes',exact=True).click()
  await page.get_by_role('tab',name='SportyBet Booking Codes',exact=True).focus();await page.keyboard.press('ArrowRight');assert await page.get_by_role('tab',name='Category Performance',exact=True).get_attribute('aria-selected')=='true';await page.keyboard.press('Home')
  before=len(requests);await page.get_by_role('button',name='Refresh dashboard').click();await page.wait_for_timeout(300);assert len(requests)>before
  layouts=[]
  for width in [1536,1280,1024,768,390,360]:
   await page.set_viewport_size({'width':width,'height':844});await page.wait_for_timeout(100)
   overflow=await page.evaluate('document.documentElement.scrollWidth>innerWidth')
   if overflow:
    await page.screenshot(path=str(ROOT/'reports/booking-dashboard-overflow.png'),full_page=True)
    print(await page.evaluate("[...document.querySelectorAll('body *')].map(e=>({tag:e.tagName,c:e.className,x:e.getBoundingClientRect().x,right:e.getBoundingClientRect().right,w:e.getBoundingClientRect().width})).filter(e=>e.right>innerWidth+1 && e.w>0).slice(0,20)"))
   assert not overflow,width
   layouts.append({'width':width,'overflow':overflow})
  await page.set_viewport_size({'width':390,'height':844})
  await page.get_by_role('button',name='Open navigation').click();await page.wait_for_function("Math.abs(document.querySelector('.bcd-sidebar').getBoundingClientRect().left)<1")
  await page.get_by_role('button',name='Close navigation',exact=True).click(position={'x':350,'y':400});await page.wait_for_function("document.querySelector('.bcd-sidebar').getBoundingClientRect().right<1")
  await page.screenshot(path=str(ROOT/'reports/booking-dashboard-mobile.png'),full_page=True)
  before=len(requests);await page.wait_for_timeout(15500);assert len(requests)>before
  await page.get_by_role('link',name='Notifications',exact=True).click();assert page.url.endswith('/sports/alerts');await page.wait_for_timeout(500)
  assert not errors,errors
  results.update(layouts=layouts,page_errors=errors,verified_only=True,filters=True,date_filter=True,detail_dialog=True,clipboard=True,tabs=True,refresh=True,automatic_refresh=True,navigation=True,data_source='isolated browser fixtures; no live writes')
  await browser.close()
 (ROOT/'reports/booking-dashboard-check.json').write_text(json.dumps(results,indent=2));print(json.dumps(results,indent=2))
asyncio.run(main())
