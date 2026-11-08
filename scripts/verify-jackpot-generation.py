"""Verify Jackpot UI states using intercepted browser fixtures; no live writes."""
import asyncio,json
from datetime import datetime,timezone
from pathlib import Path
from playwright.async_api import async_playwright
ROOT=Path(__file__).resolve().parents[1]
async def main():
 now=datetime.now(timezone.utc).isoformat();errors=[];generated=[]
 jackpot={'id':'jackpot','enabled':1,'daily_target':5,'daily_created':2,'daily_verified':2,'total':2,'settled':0,'min_odds':2000000,'max_odds':2200000,'last_run':{'status':'insufficient_eligible_data','reason':'No unused qualified combination currently reaches the target.'}}
 payload={'worker':{'healthy':True},'categories':[jackpot],'audit':[],'tickets':[{'ticket_id':str(i),'ticket_no':f'HO-TEST-{i}','category_id':'jackpot','created_at':now,'booking_code':f'TESTJACK{i}','registration_state':'registered','combined_odds':2097152,'joint_probability':.0000436,'leg_count':21,'stake_assumption':100,'start_at':now,'status':'qualified','legs':[]} for i in range(2)]}
 async with async_playwright() as p:
  b=await p.chromium.launch();page=await b.new_page(viewport={'width':1536,'height':1024});page.on('pageerror',lambda e:errors.append(str(e)))
  async def route(r):
   if r.request.url.endswith('/generate'):generated.append(json.loads(r.request.post_data));await r.fulfill(json={'status':'queued'})
   elif '/autonomous/overview' in r.request.url:await r.fulfill(json={'status':'running','tasks':[]})
   else:await r.fulfill(json=payload)
  await page.route('**/api/sports-ai/**',route)
  await page.goto('http://localhost:5173/sports/high-odds')
  card=page.locator('.bcd-jackpot');await card.get_by_text('Awaiting qualified selections',exact=True).wait_for()
  assert '2 / 5 Codes (40%)' in await card.inner_text()
  assert await card.locator('.bcd-progress>span').evaluate('(e)=>e.style.width')=='40%'
  await card.click();assert await page.get_by_label('Category',exact=True).input_value()=='jackpot';assert await page.locator('.bcd-portfolio tbody tr').count()==2
  assert '209,715,200.00' in await page.locator('.bcd-portfolio tbody').inner_text()
  jackpot['enabled']=0;await page.get_by_role('button',name='Refresh dashboard').click();await card.get_by_text('Disabled in settings',exact=True).wait_for()
  jackpot['enabled']=1;jackpot['daily_created']=5;await page.get_by_role('button',name='Refresh dashboard').click();await card.get_by_text('Daily ticket limit reached',exact=True).wait_for()
  jackpot['daily_created']=2;await page.get_by_role('button',name='Refresh dashboard').click();await card.get_by_text('Awaiting qualified selections',exact=True).wait_for()
  for width in [1536,1280,768,390,360]:
   await page.set_viewport_size({'width':width,'height':1024});await page.wait_for_timeout(250);assert not await page.evaluate('document.documentElement.scrollWidth>innerWidth'),width
  await page.set_viewport_size({'width':1536,'height':1024});await page.wait_for_timeout(250)
  await card.screenshot(path=str(ROOT/'reports/jackpot-enabled-card.png'))
  await page.get_by_role('tab',name='Analytics',exact=True).click();assert await page.locator('.bcd-analytics article').count()==9
  await page.get_by_role('tab',name='Generation Log',exact=True).click();await page.get_by_role('button',name='Run optimizer',exact=True).click();await page.get_by_role('status').get_by_text('Optimizer queued for all nine',exact=False).wait_for();assert generated
  assert not errors,errors
  await b.close()
 result={'enabled_status':True,'daily_progress':True,'jackpot_filter':True,'hypothetical_payout':True,'disabled_setting':True,'daily_limit_status':True,'nine_category_analytics':True,'optimizer_request':True,'responsive_widths':[1536,1280,768,390,360],'page_errors':errors,'data_source':'isolated browser fixtures'}
 (ROOT/'reports/jackpot-ui-check.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
asyncio.run(main())
