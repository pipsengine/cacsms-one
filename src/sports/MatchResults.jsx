import React,{useEffect,useMemo,useState} from 'react';
import {Activity,AlertTriangle,Banknote,ChevronLeft,ChevronRight,Ticket,CalendarDays,CheckCircle2,Clock3,ExternalLink,Filter,Hourglass,Layers,RefreshCw,ShieldCheck,Target,TrendingUp,Trophy,Wallet,XCircle,Minus} from 'lucide-react';
import {get} from './api';
import './match-results.css';

const pct=v=>v==null?'—':`${(v*100).toFixed(1)}%`;
const signed=v=>v==null?'—':`${v>=0?'+':''}${(v*100).toFixed(1)}%`;
const naira=v=>v==null?'—':`₦${Number(v).toLocaleString('en-NG',{maximumFractionDigits:2})}`;
const nairaSigned=v=>v==null?'—':`${v>=0?'+':'−'}₦${Math.abs(Number(v)).toLocaleString('en-NG',{maximumFractionDigits:2})}`;
const day=s=>s?new Date(`${String(s).slice(0,10)}T12:00:00`).toLocaleDateString('en-GB',{day:'2-digit',month:'short',year:'numeric'}):'—';
const short=s=>s?new Date(`${String(s).slice(0,10)}T12:00:00`).toLocaleDateString('en-GB',{month:'short',day:'numeric'}):'';
const clock=s=>s?new Date(s).toLocaleTimeString('en-GB',{timeZone:'Africa/Lagos',hour:'2-digit',minute:'2-digit'}):'—';
const stamp=s=>s?new Date(s).toLocaleString('en-GB',{timeZone:'Africa/Lagos',day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'}):'Not available';
const kickoff=s=>s?new Date(s).toLocaleString('en-GB',{timeZone:'Africa/Lagos',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'}):'—';
const SPORT={football:'⚽',frauenfussball:'⚽',basketball:'🏀',ice_hockey:'🏒',tennis:'🎾',baseball:'⚾',volleyball:'🏐',handball:'🤾',americanfootball:'🏈',rugby:'🏉',cricket:'🏏',darts:'🎯',table_tennis:'🏓',snooker:'🎱'};
const sportIcon=s=>SPORT[s]||(/^(e|v)|counterstrike|lol|dota/.test(s||'')?'🎮':'🏆');
const human=s=>String(s||'').replaceAll('_',' ').replace(/\b\w/g,c=>c.toUpperCase());
const isoDay=d=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
const PAGE_SIZES=[10,25,50,100];
const KINDS=[['single','Singles'],['combined','Combined'],['ticket','Tickets (booking codes)']];
const RESULT_CLASS={Won:'won',Lost:'lost',Void:'void','Awaiting result':'open',Unconfirmed:'warn'};
const RESULT_ICON={Won:CheckCircle2,Lost:XCircle,Void:Minus,'Awaiting result':Hourglass,Unconfirmed:AlertTriangle};

function Codes({codes}){
 if(!codes?.length)return <span className="mr-muted">—</span>;
 const head=codes.slice(0,2),rest=codes.length-head.length;
 return <span className="mr-codes" title={codes.join(', ')}>{head.map(c=><code key={c}>{c}</code>)}{rest>0&&<small>+{rest}</small>}</span>;
}

function Result({value}){const Icon=RESULT_ICON[value]||Minus;return <span className={`mr-chip is-${RESULT_CLASS[value]||'void'}`}><Icon size={13}/>{value}</span>}

function Pager({page,pages,total,size,onPage,onSize}){
 if(!total)return null;
 const from=(page-1)*size+1,to=Math.min(total,page*size);
 const nums=[...new Set([1,page-1,page,page+1,pages].filter(n=>n>=1&&n<=pages))].sort((a,b)=>a-b);
 return <div className="mr-pager">
  <span>Showing <b>{from.toLocaleString()}–{to.toLocaleString()}</b> of <b>{total.toLocaleString()}</b></span>
  <div className="mr-pager-buttons">
   <button type="button" onClick={()=>onPage(page-1)} disabled={page<=1} aria-label="Previous page"><ChevronLeft size={16}/></button>
   {nums.map((n,i)=><React.Fragment key={n}>{i>0&&n-nums[i-1]>1&&<span className="mr-pager-gap">…</span>}<button type="button" className={n===page?'is-current':''} aria-current={n===page?'page':undefined} onClick={()=>onPage(n)}>{n}</button></React.Fragment>)}
   <button type="button" onClick={()=>onPage(page+1)} disabled={page>=pages} aria-label="Next page"><ChevronRight size={16}/></button>
  </div>
  <label>Rows<select value={size} onChange={e=>onSize(Number(e.target.value))}>{PAGE_SIZES.map(n=><option key={n} value={n}>{n}</option>)}</select></label>
 </div>;
}

function Kpi({icon:Icon,label,value,tone='blue',sub,big}){return <div className={`mr-kpi ${big?'is-big':''}`}><div className={`mr-kpi-icon is-${tone}`}><Icon size={big?26:20}/></div><div><div className="mr-kpi-label">{label}</div><div className={`mr-kpi-value ${tone==='return-pos'?'is-pos':tone==='return-neg'?'is-neg':''}`}>{value}</div>{sub&&<div className="mr-delta">{sub}</div>}</div></div>}
function Panel({title,icon:Icon,extra,children,className=''}){return <section className={`mr-panel ${className}`}><div className="mr-panel-heading"><h2>{Icon&&<span className="mr-panel-icon"><Icon size={18}/></span>}{title}</h2>{extra}</div>{children}</section>}
const tone=v=>v==null?'blue':v>=0?'return-pos':'return-neg';

function Trend({points}){
 const W=560,H=190,L=46,R=46,T=14,B=26,iw=W-L-R,ih=H-T-B;
 const acc=points.map(p=>p.accuracy),ret=points.map(p=>p.realized_return);
 const accVals=acc.filter(v=>v!=null),retVals=ret.filter(v=>v!=null);
 if(!accVals.length&&!retVals.length)return <p className="mr-empty">No settled bets in these 7 days yet.</p>;
 const aMin=accVals.length?Math.max(0,Math.floor((Math.min(...accVals)-0.05)*20)/20):0,aMax=accVals.length?Math.min(1,Math.ceil((Math.max(...accVals)+0.05)*20)/20):1;
 const rMin=Math.floor((Math.min(0,...retVals)-0.05)*20)/20,rMax=Math.ceil((Math.max(0,...retVals)+0.05)*20)/20;
 const x=i=>L+i*(iw/Math.max(1,points.length-1));
 const ya=v=>T+ih-(v-aMin)/Math.max(1e-6,aMax-aMin)*ih,yr=v=>T+ih-(v-rMin)/Math.max(1e-6,rMax-rMin)*ih;
 const path=(vals,y)=>vals.map((v,i)=>v==null?null:`${x(i)},${y(v)}`).filter(Boolean).map((p,i)=>`${i?'L':'M'}${p}`).join(' ');
 const ticks=4,accTicks=[...Array(ticks+1)].map((_,i)=>aMin+(aMax-aMin)*i/ticks),retTicks=[...Array(ticks+1)].map((_,i)=>rMin+(rMax-rMin)*i/ticks);
 return <div className="mr-chart">
  <div className="mr-legend"><span><i className="mr-ring is-blue"/>Win rate</span><span><i className="mr-ring is-green"/>Return on stake</span></div>
  <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Cumulative win rate and return on stake per day">
   {accTicks.map((v,i)=><g key={i}><line x1={L} x2={W-R} y1={ya(v)} y2={ya(v)} className="mr-gridline"/><text x={L-8} y={ya(v)+4} textAnchor="end" className="mr-axis">{Math.round(v*100)}%</text><text x={W-R+8} y={ya(v)+4} className="mr-axis is-green">{`${retTicks[i]>=0?'+':''}${Math.round(retTicks[i]*100)}%`}</text></g>)}
   <path d={path(ret,yr)} fill="none" stroke="#16a34a" strokeWidth="2.5" strokeLinejoin="round"/>
   <path d={path(acc,ya)} fill="none" stroke="#1763f2" strokeWidth="2.5" strokeLinejoin="round"/>
   {points.map((p,i)=><g key={p.date}>{p.accuracy!=null&&<circle cx={x(i)} cy={ya(p.accuracy)} r="4.5" className="mr-dot is-blue"><title>{`${short(p.date)}: win rate ${pct(p.accuracy)} (${p.settled} settled)`}</title></circle>}{p.realized_return!=null&&<circle cx={x(i)} cy={yr(p.realized_return)} r="4.5" className="mr-dot is-green"><title>{`${short(p.date)}: return ${signed(p.realized_return)}`}</title></circle>}<text x={x(i)} y={H-6} textAnchor="middle" className="mr-axis">{short(p.date)}</text></g>)}
  </svg>
  <p className="mr-hint">Cumulative over the 7 days ending on the To date. Return is profit divided by the stake of settled bets.</p>
 </div>;
}

function Fixture({r}){return <><span className="mr-sport" title={human(r.sport)}>{sportIcon(r.sport)}</span>{r.fixture}{r.extra_legs>0&&<small className="mr-legs"> + {r.extra_legs} more leg{r.extra_legs>1?'s':''}</small>}</>}

function BetDetail({bet,onClose}){
 const facts=[['Bet',bet.kind_label],['Selection',bet.selection],['Odds',bet.odds?bet.odds.toFixed(2):'—'],['Stake',naira(bet.stake)],
  ['Result',bet.result],['Payout',naira(bet.payout)],['Profit / loss',nairaSigned(bet.profit)],['Result source',bet.result_source||'Not settled yet'],
  ['SportyBet booking code',bet.codes?.length?bet.codes.join(', '):'—'],['SportyBet ticket ref',bet.bookmaker_ref||'—'],['Placed at',stamp(bet.placed_at)],['Settled at',stamp(bet.settled_at)]];
 return <div className="mr-modal-backdrop" onClick={onClose}><div className="mr-modal" role="dialog" aria-modal="true" onClick={e=>e.stopPropagation()}>
  <button className="mr-modal-close" onClick={onClose} aria-label="Close">×</button>
  <h2>{bet.fixture}{bet.extra_legs>0?` + ${bet.extra_legs} more`:''}</h2><p>{bet.competition}</p>
  {bet.mismatch&&<div className="mr-notice is-error"><AlertTriangle size={16}/><span>SportyBet settled this bet differently from the verified final score. SportyBet's result is shown.</span></div>}
  <dl>{facts.map(([k,val])=><React.Fragment key={k}><dt>{k}</dt><dd>{val}</dd></React.Fragment>)}</dl>
  {bet.legs?.length>1&&<div className="mr-table-scroll"><table className="mr-breakdown"><thead><tr><th>Leg</th><th>Selection</th><th>Odds</th><th>Kick-off</th><th>Leg result</th></tr></thead><tbody>
   {bet.legs.map((l,i)=><tr key={i}><td>{sportIcon(l.sport)} {l.fixture}</td><td>{human(l.selection)}</td><td>{l.odds?l.odds.toFixed(2):'—'}</td><td>{kickoff(l.starts_at)}</td><td>{l.status==='OPEN'?'Open':human(l.status||'')}</td></tr>)}
  </tbody></table></div>}
 </div></div>;
}

export default function MatchResults(){
 const today=isoDay(new Date());
 const [form,setForm]=useState({kind:'',sport:'',date_from:today,date_to:today});
 const [applied,setApplied]=useState(form);
 const [page,setPage]=useState(1),[pageSize,setPageSize]=useState(25);
 const [view,setView]=useState(null),[error,setError]=useState(''),[loading,setLoading]=useState(true);
 const [selected,setSelected]=useState(null),[updated,setUpdated]=useState(null);
 const query=useMemo(()=>new URLSearchParams(Object.entries({...applied,page,page_size:pageSize}).filter(([,v])=>v)).toString(),[applied,page,pageSize]);
 async function load(){setLoading(true);try{const data=await get(`/autonomy/placed-results?${query}`);setView(data);setError('');setUpdated(new Date());if(data.page&&data.page!==page)setPage(data.page)}catch(e){setError(e.message)}finally{setLoading(false)}}
 useEffect(()=>{load();const id=setInterval(load,60000);return()=>clearInterval(id)},[query]);
 const s=view?.stats||{},v=view?.verification||{},bd=view?.breakdown||{},acct=view?.account||{},totals=view?.totals||{};
 const rows=view?.rows||[];
 const isToday=applied.date_from===today&&applied.date_to===today;
 const set=k=>e=>setForm({...form,[k]:e.target.value});
 const apply=f=>{setForm(f);setApplied(f);setPage(1)};
 const liveOn=acct.mode==='LIVE'&&acct.authorized;
 const empty=loading?'Loading bets…':
  !totals.real?`The system has not placed a bet yet${liveOn?'.':' – real-money betting is off. Turn it on in Single Betting → SportyBet Account (Authorize, then Live – real money).'}`:
  isToday?"No bets on today's games. Bets are listed by the kick-off date of their last game.":'No bets match the selected filters.';
 return <div className="mr-page">
  <div className="mr-hero">
   <div className="mr-hero-left"><div className="mr-hero-icon"><Trophy size={34}/></div><div><h1>Match Results <span>(Placed Bets)</span></h1><p>Results of the bets the system placed on your SportyBet account – nothing else.</p></div></div>
   <div className="mr-hero-right">
    <div className="mr-hero-stat"><Clock3 size={22}/><div><b>Last Update</b><small>{stamp(updated)}</small></div></div>
    <div className="mr-hero-stat"><Wallet size={22}/><div><b>Real-Money Betting</b><small className={liveOn?'is-active':'is-idle'}><i/>{liveOn?`On until ${stamp(acct.authorization_expires)}`:acct.mode==='LIVE_REHEARSAL'?'Off – rehearsal only':'Off'}</small></div></div>
    <div className="mr-hero-stat"><Banknote size={22}/><div><b>Bets Placed (all time)</b><small>{(totals.real??0).toLocaleString()}</small></div></div>
   </div>
  </div>
  {error&&<div className="mr-notice is-error"><AlertTriangle size={16}/><span><b>Results unavailable.</b> {error}. No figures are shown in their place.</span><button onClick={load}>Retry</button></div>}
  <div className="mr-kpis">
   <Kpi big icon={Ticket} tone="purple" label="Bets Placed" value={(s.placed??0).toLocaleString()} sub={`${s.settled??0} settled · ${s.awaiting??0} awaiting${s.unconfirmed?` · ${s.unconfirmed} unconfirmed`:''}`}/>
   <Kpi big icon={Target} tone="green" label="Win Rate" value={pct(s.win_rate)} sub={`${s.won??0} won · ${s.lost??0} lost${s.void?` · ${s.void} void`:''}`}/>
   <Kpi big icon={Wallet} label="Total Staked" value={naira(s.staked)}/>
   <Kpi big icon={Banknote} label="Total Returned" value={naira(s.returned)}/>
   <Kpi big icon={TrendingUp} tone={tone(s.profit)} label="Profit / Loss" value={nairaSigned(s.profit)}/>
   <Kpi big icon={Activity} tone={tone(s.roi)} label="Return on Stake" value={signed(s.roi)}/>
  </div>
  <form className="mr-filters" onSubmit={e=>{e.preventDefault();apply(form)}}>
   <label>Bet Type<select value={form.kind} onChange={set('kind')}><option value="">All types</option>{KINDS.map(([k,l])=><option key={k} value={k}>{l}</option>)}</select></label>
   <label>Sport<select value={form.sport} onChange={set('sport')}><option value="">All sports</option>{(view?.options?.sports||[]).map(x=><option key={x} value={x}>{human(x)}</option>)}</select></label>
   <label className="is-date">From<span><CalendarDays size={16}/><input type="date" value={form.date_from} onChange={set('date_from')}/></span></label>
   <label className="is-date">To<span><CalendarDays size={16}/><input type="date" value={form.date_to} onChange={set('date_to')}/></span></label>
   <button className="mr-primary" type="submit"><Filter size={16}/>Apply Filters</button>
   {!isToday&&<button className="mr-secondary" type="button" onClick={()=>apply({...form,date_from:today,date_to:today})}>Today</button>}
  </form>
  <div className="mr-grid">
   <Panel title={<>Bet Results <span className="mr-count">({(view?.total??0).toLocaleString()})</span></>} icon={CheckCircle2} className="is-results" extra={<div className="mr-inline"><span className="mr-pill is-success"><RefreshCw size={12}/>Auto-updated</span>{isToday&&<span className="mr-pill">Today</span>}</div>}>
    <div className="mr-table-scroll"><table><thead><tr><th>Date</th><th>Fixture</th><th>Bet</th><th>Selection</th><th>Odds</th><th>Stake</th><th>SportyBet Code</th><th>Result</th><th>Profit / Loss</th><th/></tr></thead><tbody>
     {rows.map(r=><tr key={r.id}>
      <td>{day(r.date)}</td>
      <td className="is-fixture"><Fixture r={r}/></td>
      <td>{r.kind_label}</td>
      <td className="is-strong">{r.selection}</td>
      <td className="is-strong">{r.odds?r.odds.toFixed(2):'—'}</td>
      <td>{naira(r.stake)}</td>
      <td><Codes codes={r.codes}/></td>
      <td><Result value={r.result}/>{r.mismatch&&<span title="SportyBet settled this differently from the verified score" className="mr-mismatch"><AlertTriangle size={13}/></span>}</td>
      <td className={r.profit==null?'':r.profit>=0?'is-pos':'is-neg'}>{nairaSigned(r.profit)}</td>
      <td><button className="mr-open" onClick={()=>setSelected(r)} aria-label={`Open ${r.fixture}`}><ExternalLink size={16}/></button></td>
     </tr>)}
     {!rows.length&&<tr><td colSpan="10" className="mr-empty">{empty}</td></tr>}
    </tbody></table></div>
    <Pager page={view?.page||page} pages={view?.pages||1} total={view?.total||0} size={pageSize} onPage={setPage} onSize={n=>{setPageSize(n);setPage(1)}}/>
   </Panel>
   <div className="mr-stack">
    <Panel title="Result Verification" icon={ShieldCheck}>
     <div className="mr-verify">{[
       ['Confirmed by SportyBet Bet History',v.confirmed_by_sportybet,false],['Placed, awaiting SportyBet settlement',v.awaiting_sportybet,false],
       ['Unconfirmed submissions',v.unconfirmed,true],['SportyBet vs verified score mismatches',v.mismatches,true]].map(([label,n,warn])=><div key={label}><CheckCircle2 size={19} className={warn&&n?'is-warn':''}/><span>{label}</span><b className={warn&&n?'is-warn':''}>{(n??0).toLocaleString()}</b></div>)}</div>
     <p className="mr-hint">Results and payouts are taken from your SportyBet Bet History. Until SportyBet settles a bet, the verified final score is shown. An unconfirmed submission may or may not have been placed; it is checked against Bet History.</p>
    </Panel>
    <Panel title="Performance Trend" icon={TrendingUp}><Trend points={view?.trend||[]}/></Panel>
   </div>
  </div>
  <div className="mr-bottom">
   <Panel title="Settlement Activity" icon={Activity}>
    <div className="mr-activity">
     {(view?.activity||[]).map((a,i)=><div key={i}>{a.result==='Lost'?<XCircle size={18} className="is-lost"/>:<CheckCircle2 size={18}/>}<span>Bet settled: {a.fixture}{a.extra_legs>0?` + ${a.extra_legs} more`:''} — {a.selection} ({a.result}) | {nairaSigned(a.profit)}</span><small>{clock(a.at)}</small></div>)}
     {view&&!view.activity?.length&&<p className="mr-empty">No bets settled for the selected filters yet.</p>}
    </div>
   </Panel>
   <Panel title="By Bet Type" icon={Layers}>
    <div className="mr-table-scroll"><table className="mr-breakdown"><thead><tr><th>Type</th><th>Placed</th><th>Won / Lost</th><th>Win rate</th><th>Profit / Loss</th></tr></thead><tbody>
     {KINDS.map(([k,label])=>{const m=bd[k]||{};return <tr key={k}><td>{label}</td><td>{(m.placed??0).toLocaleString()}</td><td>{m.won??0} / {m.lost??0}</td><td>{pct(m.win_rate)}</td><td className={m.profit==null?'':m.profit>=0?'is-pos':'is-neg'}>{nairaSigned(m.profit)}</td></tr>})}
    </tbody></table></div>
    <p className="mr-hint">A combined bet or ticket wins only when every leg wins.</p>
   </Panel>
  </div>
  {selected&&<BetDetail bet={selected} onClose={()=>setSelected(null)}/>}
 </div>;
}
