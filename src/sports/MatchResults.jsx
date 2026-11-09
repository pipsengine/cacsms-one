import React,{useEffect,useMemo,useState} from 'react';
import {Activity,AlertTriangle,ArrowRight,BarChart3,BrainCircuit,CalendarDays,Check,CheckCircle2,Clock3,Database,ExternalLink,Filter,RefreshCw,Settings,Settings2,ShieldCheck,Target,TrendingUp,Trophy,XCircle,ArrowUp,ArrowDown,Minus} from 'lucide-react';
import {get} from './api';
import './match-results.css';

const pct=v=>v==null?'—':`${(v*100).toFixed(1)}%`;
const signed=v=>v==null?'—':`${v>=0?'+':''}${(v*100).toFixed(1)}%`;
const num=(v,d=3)=>v==null?'—':Number(v).toFixed(d);
const day=s=>s?new Date(`${String(s).slice(0,10)}T12:00:00`).toLocaleDateString('en-GB',{day:'2-digit',month:'short',year:'numeric'}):'—';
const short=s=>s?new Date(`${String(s).slice(0,10)}T12:00:00`).toLocaleDateString('en-GB',{month:'short',day:'numeric'}):'';
const clock=s=>s?new Date(s).toLocaleTimeString('en-GB',{timeZone:'Africa/Lagos',hour:'2-digit',minute:'2-digit',second:'2-digit'}):'—';
const stamp=s=>s?new Date(s).toLocaleString('en-GB',{timeZone:'Africa/Lagos',day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit'}):'Not available';
const SPORT={football:'⚽',frauenfussball:'⚽',basketball:'🏀',ice_hockey:'🏒',tennis:'🎾',baseball:'⚾',volleyball:'🏐',handball:'🤾',americanfootball:'🏈',rugby:'🏉',cricket:'🏏',darts:'🎯',table_tennis:'🏓',snooker:'🎱'};
const sportIcon=s=>SPORT[s]||(/^(e|v)|counterstrike|lol|dota/.test(s||'')?'🎮':'🏆');
const human=s=>String(s||'').replaceAll('_',' ').replace(/\b\w/g,c=>c.toUpperCase());
const KIND={qualified:'AI qualified pick',ticket:'Booking-code ticket leg',prediction:'Match-winner prediction'};
const isoDay=d=>d.toISOString().slice(0,10);

function Delta({value,previous,good='up',format}){
 if(value==null||previous==null)return null;
 const diff=value-previous;if(Math.abs(diff)<1e-9)return <div className="mr-delta"><Minus size={12}/>no change</div>;
 const better=good==='up'?diff>0:diff<0;const Icon=diff>0?ArrowUp:ArrowDown;
 return <div className={`mr-delta ${better?'is-good':'is-bad'}`}><Icon size={12}/>{format(diff)}</div>;
}
function Kpi({icon:Icon,label,value,tone='blue',delta,big}){return <div className={`mr-kpi ${big?'is-big':''}`}><div className={`mr-kpi-icon is-${tone}`}><Icon size={big?26:20}/></div><div><div className="mr-kpi-label">{label}</div><div className={`mr-kpi-value ${tone==='return-pos'?'is-pos':tone==='return-neg'?'is-neg':''}`}>{value}</div>{delta}</div></div>}
function Panel({title,icon:Icon,extra,children,className=''}){return <section className={`mr-panel ${className}`}><div className="mr-panel-heading"><h2>{Icon&&<span className="mr-panel-icon"><Icon size={18}/></span>}{title}</h2>{extra}</div>{children}</section>}

function Trend({points}){
 const W=560,H=190,L=46,R=46,T=14,B=26,iw=W-L-R,ih=H-T-B;
 const acc=points.map(p=>p.accuracy),ret=points.map(p=>p.realized_return);
 const accVals=acc.filter(v=>v!=null),retVals=ret.filter(v=>v!=null);
 if(!accVals.length)return <p className="mr-empty">No settled picks in this period yet.</p>;
 const aMin=Math.max(0,Math.floor((Math.min(...accVals)-0.05)*20)/20),aMax=Math.min(1,Math.ceil((Math.max(...accVals)+0.05)*20)/20);
 const rMin=Math.floor((Math.min(0,...retVals)-0.05)*20)/20,rMax=Math.ceil((Math.max(0,...retVals)+0.05)*20)/20;
 const x=i=>L+i*(iw/Math.max(1,points.length-1));
 const ya=v=>T+ih-(v-aMin)/Math.max(1e-6,aMax-aMin)*ih,yr=v=>T+ih-(v-rMin)/Math.max(1e-6,rMax-rMin)*ih;
 const path=(vals,y)=>vals.map((v,i)=>v==null?null:`${x(i)},${y(v)}`).filter(Boolean).map((p,i)=>`${i?'L':'M'}${p}`).join(' ');
 const ticks=4,accTicks=[...Array(ticks+1)].map((_,i)=>aMin+(aMax-aMin)*i/ticks),retTicks=[...Array(ticks+1)].map((_,i)=>rMin+(rMax-rMin)*i/ticks);
 const retLine=path(ret,yr),first=ret.findIndex(v=>v!=null),last=ret.length-1-[...ret].reverse().findIndex(v=>v!=null);
 const area=retLine&&first>=0?`${retLine} L${x(last)},${T+ih} L${x(first)},${T+ih} Z`:'';
 return <div className="mr-chart">
  <div className="mr-legend"><span><i className="mr-ring is-blue"/>Accuracy</span><span><i className="mr-ring is-green"/>Realized return</span></div>
  <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Cumulative accuracy and realized return per day">
   <defs><linearGradient id="mr-area" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#16a34a" stopOpacity=".28"/><stop offset="100%" stopColor="#16a34a" stopOpacity="0"/></linearGradient></defs>
   {accTicks.map((v,i)=><g key={i}><line x1={L} x2={W-R} y1={ya(v)} y2={ya(v)} className="mr-gridline"/><text x={L-8} y={ya(v)+4} textAnchor="end" className="mr-axis">{Math.round(v*100)}%</text><text x={W-R+8} y={ya(v)+4} className="mr-axis is-green">{`${retTicks[i]>=0?'+':''}${Math.round(retTicks[i]*100)}%`}</text></g>)}
   {area&&<path d={area} fill="url(#mr-area)"/>}
   {retLine&&<path d={retLine} fill="none" stroke="#16a34a" strokeWidth="2.5" strokeLinejoin="round"/>}
   <path d={path(acc,ya)} fill="none" stroke="#1763f2" strokeWidth="2.5" strokeLinejoin="round"/>
   {points.map((p,i)=><g key={p.date}>{p.accuracy!=null&&<circle cx={x(i)} cy={ya(p.accuracy)} r="4.5" className="mr-dot is-blue"><title>{`${short(p.date)}: accuracy ${pct(p.accuracy)} (${p.settled} settled)`}</title></circle>}{p.realized_return!=null&&<circle cx={x(i)} cy={yr(p.realized_return)} r="4.5" className="mr-dot is-green"><title>{`${short(p.date)}: realized ${signed(p.realized_return)}`}</title></circle>}<text x={x(i)} y={H-6} textAnchor="middle" className="mr-axis">{short(p.date)}</text></g>)}
  </svg>
  <p className="mr-hint">Cumulative figures for the filtered picks up to each day. Return is per 1-unit stake on priced picks.</p>
 </div>;
}

function Countdown({target}){
 const [now,setNow]=useState(Date.now());
 useEffect(()=>{const id=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(id)},[]);
 if(!target)return 'Not scheduled';
 const s=Math.round((new Date(target).getTime()-now)/1000);
 if(s<=0)return 'Due now';
 return `in ${Math.floor(s/60)}m ${String(s%60).padStart(2,'0')}s`;
}

export default function MatchResults(){
 const today=new Date(),weekAgo=new Date(Date.now()-8*864e5);
 const [form,setForm]=useState({sport:'',market:'',competition:'',model:'',date_from:isoDay(weekAgo),date_to:isoDay(today)});
 const [applied,setApplied]=useState(form);
 const [view,setView]=useState(null),[error,setError]=useState(''),[loading,setLoading]=useState(true);
 const [expanded,setExpanded]=useState(false),[selected,setSelected]=useState(null),[settings,setSettings]=useState(false),[updated,setUpdated]=useState(null);
 const query=useMemo(()=>new URLSearchParams(Object.entries({...applied,limit:2000}).filter(([,v])=>v)).toString(),[applied]);
 async function load(){setLoading(true);try{const data=await get(`/autonomy/results?${query}`);setView(data);setError('');setUpdated(new Date())}catch(e){setError(e.message)}finally{setLoading(false)}}
 useEffect(()=>{load();const id=setInterval(load,60000);return()=>clearInterval(id)},[query]);
 const s=view?.stats||{},p=view?.previous,o=view?.options||{},v=view?.verification||{},sched=view?.schedule||{};
 const rows=view?.rows||[],shown=expanded?rows:rows.slice(0,8);
 const set=k=>e=>setForm({...form,[k]:e.target.value});
 const pp=d=>`${Math.abs(d*100).toFixed(1)} pp`,dec=d=>`${d>0?'+':'−'}${Math.abs(d).toFixed(3)}`,cnt=d=>`${d>0?'+':'−'}${Math.abs(d).toLocaleString()}`;
 return <div className="mr-page">
  <div className="mr-hero">
   <div className="mr-hero-left"><div className="mr-hero-icon"><Trophy size={34}/></div><div><h1>Match Results <span>(Autonomous)</span></h1><p>Automatic settlement of predictions, verified match outcomes and model performance.</p></div></div>
   <div className="mr-hero-right">
    <div className="mr-hero-stat"><Clock3 size={22}/><div><b>Last Update</b><small>{stamp(updated)}</small></div></div>
    <div className="mr-hero-stat"><RefreshCw size={22}/><div><b>Next Settlement</b><small><Countdown target={sched.next}/></small></div></div>
    <div className="mr-hero-stat"><Clock3 size={22}/><div><b>Auto-Settlement</b><small className={sched.active?'is-active':'is-idle'}><i/>{sched.active?'Active':'Idle'}</small></div></div>
   </div>
   <button className="mr-settings" onClick={()=>setSettings(x=>!x)} aria-label="Settlement details"><Settings size={24}/></button>
  </div>
  {settings&&<div className="mr-notice"><Settings2 size={16}/><span>Settlement runs with the worker{sched.interval_seconds?` about every ${Math.round(sched.interval_seconds/60)} minutes`:''}; last run {stamp(sched.last)}. Results are linked from official sources; picks are scored only on probabilities stored before kickoff.</span></div>}
  {error&&<div className="mr-notice is-error"><AlertTriangle size={16}/><span><b>Live results unavailable.</b> {error}. No figures are shown in their place.</span><button onClick={load}>Retry</button></div>}
  <div className="mr-kpis">
   <Kpi big icon={CheckCircle2} tone="green" label="Settled Matches" value={(s.settled??0).toLocaleString()} delta={p&&<Delta value={s.settled} previous={p.settled} format={d=>`${cnt(d)}${p.settled?` (${(Math.abs(d)/p.settled*100).toFixed(1)}%)`:''}`}/>}/>
   <Kpi big icon={Target} tone="purple" label="Accuracy" value={pct(s.accuracy)} delta={p&&<Delta value={s.accuracy} previous={p.accuracy} format={pp}/>}/>
   <Kpi big icon={Database} label="Brier Score" value={num(s.brier)} delta={p&&<Delta value={s.brier} previous={p.brier} good="down" format={dec}/>}/>
   <Kpi big icon={TrendingUp} label="Log Loss" value={num(s.log_loss)} delta={p&&<Delta value={s.log_loss} previous={p.log_loss} good="down" format={dec}/>}/>
   <Kpi big icon={BarChart3} label="Expected Return" tone={s.expected_return>=0?'return-pos':'return-neg'} value={signed(s.expected_return)}/>
   <Kpi big icon={Database} label="Realized Return" tone={s.realized_return>=0?'return-pos':'return-neg'} value={signed(s.realized_return)}/>
  </div>
  <form className="mr-filters" onSubmit={e=>{e.preventDefault();setApplied(form)}}>
   <label>Sport<select value={form.sport} onChange={set('sport')}><option value="">All sports</option>{(o.sports||[]).map(x=><option key={x} value={x}>{human(x)}</option>)}</select></label>
   <label>Market<select value={form.market} onChange={set('market')}><option value="">All markets</option>{(o.markets||[]).map(x=><option key={x}>{x}</option>)}</select></label>
   <label>Competition<select value={form.competition} onChange={set('competition')}><option value="">All competitions</option>{(o.competitions||[]).map(x=><option key={x}>{x}</option>)}</select></label>
   <label>Model Version<select value={form.model} onChange={set('model')}><option value="">All versions</option>{(o.models||[]).map(x=><option key={x}>{x}</option>)}</select></label>
   <label className="is-date">From<span><CalendarDays size={16}/><input type="date" value={form.date_from} onChange={set('date_from')}/></span></label>
   <label className="is-date">To<span><CalendarDays size={16}/><input type="date" value={form.date_to} onChange={set('date_to')}/></span></label>
   <button className="mr-primary" type="submit"><Filter size={16}/>Apply Filters</button>
  </form>
  <div className="mr-grid">
   <Panel title={<>Settled Match Results <span className="mr-count">({(view?.total??0).toLocaleString()})</span></>} icon={CheckCircle2} className="is-results" extra={<div className="mr-inline"><span className="mr-pill is-success"><RefreshCw size={12}/>Auto-updated</span><button className="mr-link" onClick={()=>setExpanded(x=>!x)}>View: <b>{expanded?'All':'Latest'}</b> <ArrowRight size={14}/></button></div>}>
    <div className="mr-table-scroll"><table><thead><tr><th>Date</th><th>Fixture</th><th>Market</th><th>Prediction</th><th>Odds</th><th>Result</th><th>Status</th><th/></tr></thead><tbody>
     {shown.map(r=><tr key={r.id}><td>{day(r.date)}</td><td className="is-fixture"><span className="mr-sport" title={human(r.sport)}>{sportIcon(r.sport)}</span>{r.fixture}</td><td>{r.market}</td><td className="is-strong">{r.prediction}</td><td className="is-strong">{r.odds?Number(r.odds).toFixed(2):'—'}</td><td><span className={`mr-chip is-${r.result==='Won'?'won':r.result==='Lost'?'lost':'void'}`}>{r.result==='Won'?<CheckCircle2 size={13}/>:r.result==='Lost'?<XCircle size={13}/>:<Minus size={13}/>}{r.result}</span></td><td><span className="mr-chip is-settled">Settled</span></td><td><button className="mr-open" onClick={()=>setSelected(r)} aria-label={`Open ${r.fixture}`}><ExternalLink size={16}/></button></td></tr>)}
     {!rows.length&&<tr><td colSpan="8" className="mr-empty">{loading?'Loading verified match results…':'No settled picks match the selected filters.'}</td></tr>}
    </tbody></table></div>
   </Panel>
   <div className="mr-stack">
    <Panel title="Result Verification" icon={ShieldCheck}>
     <div className="mr-verify">{[['Official results collected',v.official_results],['Predictions matched',v.predictions_matched],['Settlements completed',v.settlements_completed],['Discrepancies / exceptions',v.exceptions]].map(([label,n],i)=><div key={label}><CheckCircle2 size={19} className={i===3&&n?'is-warn':''}/><span>{label}</span><b className={i===3&&n?'is-warn':''}>{(n??0).toLocaleString()}</b></div>)}</div>
     {v.awaiting_result>0&&<p className="mr-hint">{v.awaiting_result.toLocaleString()} played fixtures with a prediction are still waiting for an official result (last 30 days).</p>}
    </Panel>
    <Panel title="Performance Trend" icon={TrendingUp}><Trend points={view?.trend||[]}/></Panel>
   </div>
  </div>
  <div className="mr-bottom">
   <Panel title="Settlement Activity (Live)" icon={Activity}>
    <div className="mr-activity">
     {(view?.activity||[]).map((a,i)=><div key={i}><CheckCircle2 size={18} className={a.result==='Lost'?'is-lost':''}/><span>Match settled: {a.fixture} — {a.prediction} ({a.result}){a.odds?<> | Odds {Number(a.odds).toFixed(2)}</>:null}</span><small>{clock(a.at)}</small></div>)}
     {view&&<div><Database size={18} className="is-blue"/><span><b>{(s.settled??0).toLocaleString()} matches settled</b> | Accuracy: {pct(s.accuracy)} | ROI: <b className={s.realized_return>=0?'is-pos':'is-neg'}>{signed(s.realized_return)}</b></span><small>{clock(view.generated_at)}</small></div>}
     {view&&!view.activity?.length&&<p className="mr-empty">No settlement activity for the selected filters.</p>}
    </div>
   </Panel>
   <Panel title="Model Performance (Selected Period)" icon={BrainCircuit}>
    <div className="mr-model">
     <Kpi icon={Target} tone="purple" label="Accuracy" value={pct(s.accuracy)} delta={p&&<Delta value={s.accuracy} previous={p.accuracy} format={pp}/>}/>
     <Kpi icon={Database} label="Brier Score" value={num(s.brier)} delta={p&&<Delta value={s.brier} previous={p.brier} good="down" format={dec}/>}/>
     <Kpi icon={TrendingUp} label="Log Loss" value={num(s.log_loss)} delta={p&&<Delta value={s.log_loss} previous={p.log_loss} good="down" format={dec}/>}/>
    </div>
    <p className="mr-hint">{(s.scored??0).toLocaleString()} picks scored on pre-kickoff probabilities; {(s.priced??0).toLocaleString()} with a stored SportyBet price count towards returns.{p?' Changes compare with the previous period of equal length.':''}</p>
   </Panel>
  </div>
  {selected&&<div className="mr-modal-backdrop" onClick={()=>setSelected(null)}><div className="mr-modal" role="dialog" aria-modal="true" onClick={e=>e.stopPropagation()}><button className="mr-modal-close" onClick={()=>setSelected(null)} aria-label="Close">×</button><h2>{selected.fixture}</h2><p>Match result and prediction evidence</p><dl>{[['Sport',human(selected.sport)],['Competition',selected.competition],['Date',day(selected.date)],['Final score',selected.score],['Market',`${selected.market} (${selected.market_code})`],['Selection',selected.prediction],['Pick source',KIND[selected.kind]||selected.kind],['Observed odds',selected.odds?Number(selected.odds).toFixed(2):'Not stored'],['Pre-kickoff probability',pct(selected.probability)],['Decided at',stamp(selected.decided_at)],['Outcome',selected.result],['Model',selected.model],['Result source',selected.source||'—'],['Settled at',stamp(selected.settled_at)]].map(([k,val])=><React.Fragment key={k}><dt>{k}</dt><dd>{val}</dd></React.Fragment>)}</dl></div></div>}
 </div>;
}
