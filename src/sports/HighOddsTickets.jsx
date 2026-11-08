import React,{useEffect,useRef,useState} from 'react';
import {Activity,ArrowUpRight,Check,ChevronRight,Clock3,Copy,Eye,Layers3,RefreshCw,ShieldCheck,SlidersHorizontal,Sparkles,Target,Ticket,TrendingUp,X} from 'lucide-react';
import {post} from './api';
import useLiveResource from './useLiveResource';
import './high-odds.css';

const pct=value=>value==null?'—':`${(Number(value)*100).toLocaleString('en-GB',{maximumFractionDigits:2})}%`;
const odds=value=>value==null?'—':Number(value).toLocaleString('en-GB',{minimumFractionDigits:2,maximumFractionDigits:2});
const stamp=value=>value?new Date(value).toLocaleString('en-GB',{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',timeZone:'Africa/Lagos'}):'—';
const label=value=>String(value||'pending').replaceAll('_',' ');

function State({value}) {return <span className={`ho-state ho-state-${value}`}><i/>{label(value)}</span>;}

export default function HighOddsTickets({compact=false,nav}) {
  const [category,setCategory]=useState(''),[status,setStatus]=useState(''),[from,setFrom]=useState(''),[to,setTo]=useState('');
  const [selected,setSelected]=useState(null),[tab,setTab]=useState('tickets'),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[rules,setRules]=useState(false);
  const closeRef=useRef(null),previousFocus=useRef(null);
  const params=new URLSearchParams();
  if(category)params.set('category_id',category);if(status)params.set('status',status);if(from)params.set('date_from',from);if(to)params.set('date_to',to);
  const {data,error,loading,refresh}=useLiveResource(`/high-odds?${params}`);
  useEffect(()=>{
    if(!selected)return undefined;
    previousFocus.current=document.activeElement;closeRef.current?.focus();
    const handler=e=>{
      if(e.key==='Escape')setSelected(null);
      if(e.key==='Tab'){
        const controls=[...document.querySelectorAll('.ho-dialog button,.ho-dialog a')];
        const first=controls[0],last=controls.at(-1);
        if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}
        else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}
      }
    };
    window.addEventListener('keydown',handler);
    return()=>{window.removeEventListener('keydown',handler);previousFocus.current?.focus();};
  },[selected]);
  async function generate(){
    setBusy(true);setNotice('');
    try{await post('/high-odds/generate',{idempotency_key:crypto.randomUUID()});setNotice('Optimization queued. The worker will check all eight categories.');refresh();}
    catch(e){setNotice(e.message);}finally{setBusy(false);}
  }
  async function copy(code){try{await navigator.clipboard.writeText(code);setNotice(`Copied ${code}`);}catch{setNotice('Clipboard access is unavailable. Select the code to copy it.');}}
  const categories=data?.categories||[],kpis=data?.kpis||{};
  return <section className={`ho-board ${compact?'ho-compact':''}`} aria-label="High Odds Tickets">
    <header className="ho-top ho:flex ho:items-center ho:justify-between ho:gap-4 ho:flex-wrap">
      <div><div className="ho-eyebrow"><span/>AUTONOMOUS INTELLIGENCE <ChevronRight size={12}/> ACCUMULATORS</div>
        <h1><Layers3 size={25}/>High Odds Tickets</h1><p>Eight odds categories. One disciplined portfolio.</p></div>
      <div className="ho:flex ho:items-center ho:gap-2 ho:flex-wrap">
        <button className="ho-button" onClick={()=>setRules(!rules)} aria-expanded={rules}><SlidersHorizontal size={15}/>Rules</button>
        <button className="ho-button" onClick={refresh} aria-label="Refresh high odds"><RefreshCw size={15}/></button>
        <button className="ho-button ho-primary" disabled={busy} onClick={generate}><Sparkles size={15}/>{busy?'Queuing…':'Run optimizer'}</button>
        {compact&&<button className="ho-button" onClick={()=>nav?.('high-odds')}>Open workspace<ArrowUpRight size={15}/></button>}
      </div>
    </header>
    <div className="ho-health"><span className={data?.worker?.healthy?'ho-dot':'ho-dot ho-amber'}/><b>{data?.worker?.healthy?'Worker online':'Worker awaiting heartbeat'}</b><span>Event-driven</span><span>Updated {stamp(data?.generated_at)} WAT</span><span className="ho:ml-auto"><ShieldCheck size={14}/>1,000.00 maximum odds</span></div>
    {(error||notice)&&<div role="status" className="ho-notice">{error||notice}</div>}
    {rules&&<div className="ho-rules"><ShieldCheck size={22}/><div><strong>Qualification before optimization</strong><p>Activated models, validated calibration, current verified SportyBet prices, positive expected value and sufficient source data are required. Flexible {data?.settings?.min_legs||2}–{data?.settings?.max_legs||16} selections. Each fixture belongs to one active ticket. Shared teams, competitions and unsupported settlement dependencies are excluded.</p><p>Category limits and optimizer thresholds are configurable through the authenticated High Odds settings API. Existing tickets and code evidence remain in the Ticket Builder.</p><a href="/sports/tickets">Open ticket history <ArrowUpRight size={13}/></a></div></div>}
    <div className="ho-kpis ho:grid ho:grid-cols-2 ho:xl:grid-cols-4 ho:gap-3">
      {[[Ticket,'Active tickets',kpis.active,'Across eight categories'],[Target,'Primary category','10–24.99','First generation priority'],[ShieldCheck,'Verified codes',kpis.registered,'Bookmaker display evidence'],[Clock3,'Registration queue',kpis.pending,'Pending, queued and retrying']].map(([Icon,title,value,note])=><article key={title}><span className="ho-kpi-icon"><Icon size={19}/></span><div><small>{title}</small><strong>{value??'—'}</strong><p>{note}</p></div></article>)}
    </div>
    <div className="ho-section-title"><h2>Combined odds categories</h2><span>Flexible selections · independent search budgets</span></div>
    <div className="ho-categories ho:grid ho:grid-cols-2 ho:xl:grid-cols-4 ho:gap-3">
      {categories.map(c=><button key={c.id} onClick={()=>setCategory(category===c.id?'':c.id)} className={`${c.primary?'ho-featured':''} ${category===c.id?'ho-chosen':''}`} aria-pressed={category===c.id}>
        <div className="ho:flex ho:items-center ho:justify-between"><small>COMBINED ODDS</small>{c.primary?<span className="ho-priority"><Sparkles size={11}/>Primary</span>:<span className="ho-category-icon"><TrendingUp size={15}/></span>}</div>
        <h3>{c.label}</h3><div className="ho-category-count"><strong>{c.active||0}</strong><span>active tickets</span><ChevronRight size={16}/></div>
        <div className="ho-category-footer"><span>{!c.enabled?'Disabled':c.last_run?.status==='created'?'Tickets finalized':c.last_run?.status==='capacity'?'Allocation complete':c.last_run?'Awaiting eligible selections':'Awaiting first event'}</span><small>{c.total||0} total</small></div>
      </button>)}
    </div>
    <div className="ho-workspace">
      <div className="ho-tabs" role="tablist" aria-label="High odds views">{[['tickets','Ticket portfolio',Ticket],['performance','Category performance',TrendingUp],['operations','Worker & audit',Activity]].map(([id,title,Icon])=><button key={id} role="tab" aria-selected={tab===id} className={tab===id?'on':''} onClick={()=>setTab(id)}><Icon size={15}/>{title}</button>)}</div>
      {tab==='tickets'&&<>
        <div className="ho-filters"><div><h2>All tickets <span>{data?.tickets?.length||0}</span></h2><p>Finalized selections and verified registration status</p></div><label>From<input type="date" value={from} onChange={e=>setFrom(e.target.value)}/></label><label>To<input type="date" value={to} onChange={e=>setTo(e.target.value)}/></label><label>Status<select value={status} onChange={e=>setStatus(e.target.value)}><option value="">All statuses</option>{['pending','queued','registered','retry','failed','expired','monitoring','settled'].map(s=><option key={s} value={s}>{label(s)}</option>)}</select></label><label>Category<select value={category} onChange={e=>setCategory(e.target.value)}><option value="">All categories</option>{categories.map(c=><option value={c.id} key={c.id}>{c.label}</option>)}</select></label></div>
        <div className="ho-table-scroll"><table><thead><tr>{['Ticket No','Category','Selections','Combined Odds','Joint AI Probability','Confidence','Start Date / Time','Booking Code','Status','Actions'].map(h=><th key={h}>{h}</th>)}</tr></thead><tbody>
          {(data?.tickets||[]).map(t=><tr key={t.ticket_id}><td><b className="ho-ticket-number">{t.ticket_no}</b></td><td><span className="ho-band">{categories.find(c=>c.id===t.category_id)?.label}</span></td><td>{t.leg_count}<small className="ho-sub">{new Set(t.legs.map(l=>l.sport)).size} sports</small></td><td><strong>{odds(t.combined_odds)}</strong></td><td><b>{pct(t.joint_probability)}</b><div className="ho-prob-track"><i style={{width:pct(t.joint_probability)}}/></div></td><td title="Model calibration reliability, not a guarantee of a ticket win">{pct(t.confidence)}</td><td>{stamp(t.start_at)}<small className="ho-sub">WAT · first kickoff</small></td><td>{t.booking_code?<button className="ho-copy" onClick={()=>copy(t.booking_code)}>{t.booking_code}<Copy size={12}/></button>:<span className="ho-muted">Awaiting code</span>}</td><td><State value={t.outcome&&t.outcome!=='pending'?t.outcome:t.status==='expired'?'expired':t.registration_state}/></td><td><button className="ho-view" aria-label={`View ${t.ticket_no}`} onClick={()=>setSelected(t)}><Eye size={16}/></button></td></tr>)}
        </tbody></table></div>
        {!data?.tickets?.length&&<div className="ho-empty"><div className="ho-empty-icon"><Layers3 size={27}/><ShieldCheck size={14}/></div><h3>{loading?'Loading ticket portfolio…':'Waiting for the right opportunities'}</h3><p>{category?'No stored tickets match this category and filters.':'Tickets appear when unused qualified selections meet a category’s odds and dependency checks.'}</p><span><Check size={13}/>No forced selections · no invented codes</span></div>}
        <footer className="ho-table-footer"><span>Showing up to 500 stored tickets · refreshes every 15 seconds</span><a href="/sports/codes">Booking Code Centre <ArrowUpRight size={13}/></a></footer>
      </>}
      {tab==='performance'&&<div className="ho-report"><h2>Category performance</h2><p>Official ticket settlements only. Profit uses the existing hypothetical unit-stake calculation.</p><div className="ho-table-scroll"><table><thead><tr><th>Category</th><th>Created</th><th>Settled</th><th>Won</th><th>Hit rate</th><th>Hypothetical profit</th><th>Latest search</th></tr></thead><tbody>{categories.map(c=><tr key={c.id}><td>{c.label}{c.primary&&<small className="ho-sub">Primary</small>}</td><td>{c.total}</td><td>{c.settled}</td><td>{c.wins||0}</td><td>{c.settled?pct((c.wins||0)/c.settled):'No settled sample'}</td><td>{c.hypothetical_profit==null?'—':Number(c.hypothetical_profit).toFixed(2)}</td><td>{c.last_run?.reason||label(c.last_run?.status||'not run')}</td></tr>)}</tbody></table></div></div>}
      {tab==='operations'&&<div className="ho-report"><h2>Worker & registration health</h2><div className="ho-queue-grid"><article><small>Event backlog</small><strong>{data?.queue?.queued||0}</strong></article><article><small>Failed events</small><strong>{data?.queue?.failed||0}</strong></article><article><small>Registration retries</small><strong>{data?.registration_queue?.retry||0}</strong></article><article><small>Heartbeat · WAT</small><strong className="ho-time">{stamp(data?.worker?.heartbeat_at)}</strong></article></div><h3>Recent audit trail</h3>{(data?.audit||[]).map((item,i)=><div className="ho-audit" key={`${item.created_at}-${i}`}><Activity size={14}/><b>{label(item.action)}</b><span>{stamp(item.created_at)} WAT</span><details><summary>Details</summary><pre>{JSON.stringify(JSON.parse(item.details_json),null,2)}</pre></details></div>)}{!data?.audit?.length&&<p>Audit events appear when the worker processes the first qualification event.</p>}</div>}
    </div>
    <p className="ho-footnote"><ShieldCheck size={14}/>{data?.note||'Only qualified opportunities from validated active models can enter the optimizer.'}</p>
    {selected&&<div className="ho-overlay" onClick={()=>setSelected(null)}><section className="ho-dialog" role="dialog" aria-modal="true" aria-labelledby="ho-ticket-title" onClick={e=>e.stopPropagation()}><header><div className="ho-eyebrow">IMMUTABLE SELECTION SNAPSHOT</div><h2 id="ho-ticket-title">{selected.ticket_no}</h2><button ref={closeRef} onClick={()=>setSelected(null)} aria-label="Close ticket"><X size={20}/></button></header><div className="ho-dialog-metrics"><span>Combined odds<b>{odds(selected.combined_odds)}</b></span><span>Joint estimate<b>{pct(selected.joint_probability)}</b></span><span>Ticket EV<b>{pct(selected.estimated_edge)}</b></span><span>Calibration sensitivity<b>{pct(selected.lower_joint_probability)}</b></span></div><p className="ho-muted">{selected.dependency_policy}. The sensitivity estimate reduces each individual probability by its recorded calibration error; it is not a confidence interval.</p><div className="ho-legs">{selected.legs.map((leg,i)=><article key={`${leg.fixture_id}-${leg.market}`}><span>{i+1}</span><div><h3>{leg.home} vs {leg.away}</h3><p>{leg.sport.replaceAll('_',' ')} · {leg.competition} · {stamp(leg.starts_at)} WAT</p><b>{leg.market.replaceAll('_',' ')}</b></div><div><strong>{odds(leg.decimal_odds)}</strong><small>{pct(leg.probability)} AI</small></div></article>)}</div><footer><State value={selected.registration_state}/>{selected.booking_code&&<button className="ho-button" onClick={()=>copy(selected.booking_code)}><Copy size={14}/>{selected.booking_code}</button>}<a href={`/api/sports-ai/high-odds/tickets/${selected.ticket_id}`} target="_blank" rel="noreferrer">Snapshot & audit <ArrowUpRight size={14}/></a></footer>{selected.last_error&&<p role="status" className="ho-notice">{selected.last_error}</p>}</section></div>}
  </section>;
}
