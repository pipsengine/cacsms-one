import React,{useEffect,useMemo,useState} from 'react';
import {useNavigate} from 'react-router-dom';
import {ChevronDown,Bot,Wallet,Database,Target,Trophy,BarChart3,Shield,Layers3,CheckCircle2,CheckCircle,CalendarDays,AlertTriangle,RefreshCcw,Clock3,Settings2,ListChecks,Zap,Info,Activity,ShieldCheck,TrendingUp,PauseCircle,PlayCircle,XCircle,Circle,Lock,Bell,X,ChevronRight,Search,Ticket,ScanSearch,BrainCircuit,Sparkles,Gauge,Image as ImageIcon} from 'lucide-react';
import useLiveResource from './useLiveResource';
import {post} from './api';
import './single-betting.css';

const API_BASE=import.meta.env.VITE_SPORTS_API_BASE||'/api/sports-ai';
const naira=(n,sign=false)=>n===null||n===undefined?'—':`${sign&&n>0?'+':n<0?'−':''}₦${Math.abs(Number(n)).toLocaleString('en-NG',{maximumFractionDigits:0})}`;
const pct=(n,d=1)=>n===null||n===undefined?'—':`${(Number(n)*100).toFixed(d)}%`;
const SPORT_ICON={football:'⚽',basketball:'🏀',tennis:'🎾',ice_hockey:'🏒',volleyball:'🏐',handball:'🤾',baseball:'⚾',frauenfussball:'⚽'};
const sportName=s=>String(s||'').replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
const hhmm=v=>{if(!v)return '—';const d=new Date(v);return Number.isNaN(d.getTime())?'—':d.toLocaleTimeString('en-GB',{timeZone:'Africa/Lagos',hour:'2-digit',minute:'2-digit'})};
const ago=v=>{if(!v)return 'never';const s=Math.round((Date.now()-new Date(v).getTime())/1000);if(s<60)return `${Math.max(s,0)}s ago`;if(s<3600)return `${Math.round(s/60)}m ago`;return `${Math.round(s/3600)}h ago`};
const eta=s=>s===null||s===undefined?'—':`${String(Math.floor(s/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`;
const shortDate=d=>new Date(d+'T12:00:00Z').toLocaleDateString('en-GB',{month:'short',day:'2-digit'});

/* Nice axis steps: 1, 2, 2.5, 5 × 10^n, so labels never repeat. */
function niceTicks(lo,hi,count=5){
  if(hi===lo){hi=lo+1}
  const raw=(hi-lo)/(count-1),mag=10**Math.floor(Math.log10(raw)),norm=raw/mag;
  const step=(norm<=1?1:norm<=2?2:norm<=2.5?2.5:norm<=5?5:10)*mag;
  const start=Math.floor(lo/step)*step,end=Math.ceil(hi/step)*step;
  const out=[];for(let v=start;v<=end+step/2;v+=step)out.push(Math.round(v*100)/100);return out;
}

/* ---------------------------------------------------------------- shared */

function Clock(){const[now,setNow]=useState(new Date());useEffect(()=>{const t=setInterval(()=>setNow(new Date()),1000);return()=>clearInterval(t)},[]);
  const date=now.toLocaleDateString('en-GB',{timeZone:'Africa/Lagos',weekday:'short',day:'numeric',month:'short',year:'numeric'});
  const time=now.toLocaleTimeString('en-GB',{timeZone:'Africa/Lagos',hour12:false});
  return <div className="sb-clock"><span>Africa/Lagos (WAT)</span><strong>{date} {time}</strong></div>}

function RunPill({auto,running='Running',label}){const paused=auto?.paused,ok=auto?.running;
  return <span className={'sb-pill '+(paused?'amber':ok?'green':'grey')}><i/>{paused?'Paused (Kill Switch)':ok?`${label} (${running})`:'Worker heartbeat stale'}</span>}

// Shown only when real money is on: that state must always be visible in the header.
function ModeTag({mode}){return mode==='LIVE'?<span className="sb-tag live" title="Real-money singles are submitted under an active authorization.">Live – real money</span>:null}

function PageHeader({icon,title,subtitle,auto,label,running,mode}){const navigate=useNavigate();
  return <header className="sb-header">
    <div className="sb-heading">{icon&&<span className="sb-head-icon">{icon}</span>}<div><h1>{title}</h1><p>{subtitle}</p></div></div>
    <div className="sb-header-right"><ModeTag mode={mode}/><span title={mode==='LIVE_REHEARSAL'?'Execution mode: live rehearsal (Place Bet never clicked)':mode==='LIVE'?'Execution mode: live, real money':'Execution mode: paper'}><RunPill auto={auto} label={label} running={running}/></span><span className="sb-vr"/><Clock/>
      <button className="sb-bell" aria-label="Alerts" onClick={()=>navigate('/sports/alerts')}><Bell size={21}/><i/></button>
      <span className="sb-account-chip" aria-label="Account"><span className="sb-avatar">CO</span><ChevronDown size={18}/></span></div>
  </header>}

function Loading({error,loading}){if(error)return <div className="sb-error"><AlertTriangle size={16}/>{error}</div>;if(loading)return <div className="sb-card sb-muted pad">Loading live engine state…</div>;return null}

function Bar({value,tone='green'}){return <div className={'sb-bar '+tone}><b style={{width:`${Math.min(100,Math.max(0,(value||0)*100))}%`}}/></div>}

function Kpi({icon:Icon,tone,label,value,valueTone,foot,progress,barTone,className=''}){
  return <article className={'sb-kpi '+className}>
    <span className={'sb-kpi-icon '+tone}><Icon size={26} strokeWidth={2.1}/></span>
    <div className="sb-kpi-body"><span className="sb-kpi-label">{label}</span><strong className={'sb-kpi-value '+(valueTone||'')}>{value}</strong>
      {progress===undefined&&<small className="sb-kpi-foot">{foot}</small>}</div>
    {progress!==undefined&&<div className="sb-kpi-progress"><Bar value={progress} tone={barTone||'green'}/><small>{Math.round(progress*100)}%</small></div>}
  </article>}

function Card({icon,title,action,children,className='',sub}){return <section className={'sb-card '+className}>
  {title&&<div className="sb-card-head"><div className="sb-card-title">{icon&&<span className="sb-card-icon">{icon}</span>}<div><h3>{title}</h3>{sub&&<p>{sub}</p>}</div></div>{action}</div>}
  {children}</section>}

function Check({ok,children,value,tone}){return <li><span className={'sb-check '+(ok===null?'idle':ok?'ok':'off')}>{ok===null?<Circle size={13}/>:ok?<CheckCircle size={17} fill="currentColor" stroke="#fff"/>:<XCircle size={17}/>}</span><span>{children}</span>{value!==undefined&&<b className={tone||''}>{value}</b>}</li>}

/* ---------------------------------------------------------------- charts */

function TrendChart({points,total}){const W=540,H=200,L=60,R=12,T=16,B=26;
  const vals=points.map(p=>p.cumulative);const ticks=niceTicks(Math.min(0,...vals),Math.max(1000,...vals));const y0=ticks[0],y1=ticks[ticks.length-1];
  const x=i=>L+(i*(W-L-R))/Math.max(1,points.length-1);const y=v=>T+(1-(v-y0)/(y1-y0))*(H-T-B);
  const path=points.map((p,i)=>`${i?'L':'M'}${x(i).toFixed(1)},${y(p.cumulative).toFixed(1)}`).join(' ');
  const area=points.length?`${path} L${x(points.length-1)},${y(y0)} L${x(0)},${y(y0)} Z`:'';
  const labels=points.map((p,i)=>(i%4===0&&points.length-1-i>=3)||i===points.length-1?[i,shortDate(p.date)]:null).filter(Boolean);
  const last=points[points.length-1];
  return <div className="sb-chart-wrap">
    <span className={'sb-chart-badge '+(total<0?'neg':'')}>{naira(total,true)}</span>
    <svg viewBox={`0 0 ${W} ${H}`} className="sb-chart" role="img" aria-label="Cumulative profit over the last 30 days">
      <defs><linearGradient id="sbArea" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#22c55e" stopOpacity=".30"/><stop offset="1" stopColor="#22c55e" stopOpacity="0"/></linearGradient></defs>
      {ticks.map(t=><g key={t}><line x1={L} x2={W-R} y1={y(t)} y2={y(t)} className="sb-grid-line"/><text x={L-8} y={y(t)+4} textAnchor="end" className="sb-axis">{naira(t)}</text></g>)}
      <line x1={L} x2={L} y1={T} y2={H-B} className="sb-axis-line"/>
      <path d={area} fill="url(#sbArea)"/><path d={path} className="sb-line"/>
      {points.map((p,i)=><circle key={p.date} cx={x(i)} cy={y(p.cumulative)} r="4" className="sb-dot"><title>{`${p.date}: ${naira(p.cumulative,true)} cumulative (${naira(p.profit,true)} that day)`}</title></circle>)}
      {labels.map(([i,l])=><text key={i} x={x(i)} y={H-8} textAnchor="middle" className="sb-axis">{l}</text>)}
      {last&&<circle cx={x(points.length-1)} cy={y(last.cumulative)} r="5.5" className="sb-dot-end"/>}
    </svg></div>}

const DONUT={football:'#22c55e',basketball:'#f59e0b',other:'#8b5cf6'};
function SportMix({items}){
  const football=items.find(i=>i.sport==='football')?.count||0,basketball=items.find(i=>i.sport==='basketball')?.count||0;
  const other=items.filter(i=>!['football','basketball'].includes(i.sport)).reduce((a,b)=>a+b.count,0);
  const rows=[['football','Football',football,'⚽'],['basketball','Basketball',basketball,'🏀'],['other','Others',other,'◎']];
  const total=football+basketball+other,r=56,c=2*Math.PI*r;let acc=0;
  return <div className="sb-mix">
    <svg viewBox="0 0 150 150" className="sb-donut" role="img" aria-label={`${total} bets by sport today`}>
      <circle cx="75" cy="75" r={r} className="sb-donut-track"/>
      {rows.map(([k,,n])=>{const len=total?(n/total)*c:0;const el=n?<circle key={k} cx="75" cy="75" r={r} stroke={DONUT[k]} strokeDasharray={`${len} ${c-len}`} strokeDashoffset={-acc} className="sb-donut-seg"/>:null;acc+=len;return el})}
      <text x="75" y="76" textAnchor="middle" className="sb-donut-n">{total}</text><text x="75" y="98" textAnchor="middle" className="sb-donut-l">Bets</text>
    </svg>
    <ul className="sb-legend">{rows.map(([k,l,n,ic])=><li key={k}><span className={'sb-legend-ic '+k}>{ic}</span><span>{l}</span><b>{n} <small>({total?pct(n/total):'0.0%'})</small></b></li>)}</ul>
  </div>}

function WinLossBars({days}){const W=430,H=190,L=30,R=6,B=26,T=10;
  const ticks=niceTicks(0,Math.max(10,...days.flatMap(d=>[d.wins,d.losses])),6);const top=ticks[ticks.length-1];
  const slot=(W-L-R)/days.length,bw=Math.min(18,slot/3.4);const y=v=>T+(1-v/top)*(H-T-B);
  return <svg viewBox={`0 0 ${W} ${H}`} className="sb-chart" role="img" aria-label="Wins and losses over the last 7 days">
    {ticks.map(t=><g key={t}><line x1={L} x2={W-R} y1={y(t)} y2={y(t)} className="sb-grid-line"/><text x={L-8} y={y(t)+4} textAnchor="end" className="sb-axis">{t}</text></g>)}
    {days.map((d,i)=>{const cx=L+slot*i+slot/2;return <g key={d.date}>
      <rect x={cx-bw-2} y={y(d.wins)} width={bw} height={Math.max(0,y(0)-y(d.wins))} rx="2" className="sb-bar-win"><title>{`${d.date}: ${d.wins} won`}</title></rect>
      <rect x={cx+2} y={y(d.losses)} width={bw} height={Math.max(0,y(0)-y(d.losses))} rx="2" className="sb-bar-loss"><title>{`${d.date}: ${d.losses} lost`}</title></rect>
      <text x={cx} y={H-8} textAnchor="middle" className="sb-axis">{shortDate(d.date)}</text></g>})}
  </svg>}

/* ---------------------------------------------------------------- League coverage */

const BLOCKER_LABEL={insufficient_history:'Team history < 3 games',awaiting_analysis:'Awaiting analysis',no_validated_model:'Model not validated (history depth)',not_priced:'Not priced by SportyBet'};
function LeagueCoverage(){
  const {data}=useLiveResource('/coverage/leagues',60000);
  if(!data||!data.sports?.length)return null;
  const target=data.target||0.9;
  return <Card className="sb-coverage" icon={<Gauge size={20}/>} title="League Coverage (Next 7 Days)" sub={`Leagues with upcoming matches predicted by the active model · target ${Math.round(target*100)}% · updated ${hhmm(data.computed_at)}`}
    action={<span className={'sb-cov-total '+(data.coverage>=target?'ok':'')}>{data.predicted} / {data.leagues} leagues · {pct(data.coverage,0)}</span>}>
    <div className="sb-table-scroll"><table className="sb-table sb-cov-table"><thead><tr><th>Sport</th><th>Model</th><th>Leagues</th><th>Priced</th><th>Predicted</th><th className="w">Coverage vs {Math.round(target*100)}%</th><th>Qualified</th><th>History</th><th>Main blocker</th></tr></thead>
    <tbody>{data.sports.map(r=>{const top=Object.entries(r.blockers||{}).sort((a,b)=>b[1]-a[1])[0];const cov=r.coverage||0;
      return <tr key={r.sport}><td><b>{r.label}</b></td><td>{r.model?`v${r.model.version}`:<span className="muted">none</span>}</td><td className="num">{r.leagues}</td><td className="num">{r.priced}</td><td className="num"><b>{r.predicted}</b></td>
        <td className="w"><div className="sb-cov-bar"><Bar value={cov} tone={cov>=target?'green':'violet'}/><i style={{left:`${target*100}%`}}/><small>{pct(r.coverage,0)}</small></div></td>
        <td className="num">{r.qualified}</td><td className="num">{r.history_days==null?'—':`${r.history_days}d`}</td>
        <td>{top&&top[1]?<span className="sb-chip-soft">{BLOCKER_LABEL[top[0]]||top[0]} · {top[1]}</span>:<span className="pos">On track</span>}</td></tr>})}</tbody></table></div>
  </Card>}

/* ---------------------------------------------------------------- Dashboard */

export function SingleBettingDashboard(){
  const {data,error,loading,refresh}=useLiveResource('/single-betting/dashboard',15000);
  const t=data?.today,r=data?.rules,th=data?.thirty_day,bal=data?.balance;
  return <div className="sb">
    <PageHeader title="Single Betting AI – Dashboard" subtitle="Executive overview • Results • Profitability • Trends • Risk • No manual input" auto={data?.autonomous} label="Autonomous Mode" mode={data?.mode}/>
    <Loading error={error} loading={loading&&!data}/>
    {data&&<>
    <div className="sb-kpis six">
      <Kpi icon={Wallet} tone="blue" label="SportyBet Balance" value={bal.value==null?'Not connected':naira(bal.value)} valueTone={bal.value==null?'muted small':'navy'}
        foot={bal.status==='SYNCED'?<span className="pos" title={`${bal.note} Read at ${hhmm(bal.observed_at)} WAT.`}><CheckCircle2 size={13}/> Synced (Live)</span>:bal.status==='STALE'?<span className="amber" title={bal.note}><Clock3 size={13}/> Last read {hhmm(bal.observed_at)}</span>:<span className="muted" title={bal.note}><Info size={13}/> Never simulated</span>}/>
      <Kpi icon={Database} tone="green" label="Daily Target" value={`${r.daily_target[0]} – ${r.daily_target[1]}`} valueTone="green" foot="Singles"/>
      <Kpi icon={Target} tone="violet" label="Bets Executed Today" value={`${t.executed} / ${r.daily_limit}`} valueTone="violet" progress={t.executed/r.daily_limit} barTone="green"/>
      <Kpi icon={Trophy} tone="orange" label="Wins / Losses" value={`${t.wins} / ${t.losses}`} valueTone="maroon" className="peach" foot={`Win Rate: ${pct(t.win_rate)}${t.pending?` · ${t.pending} pending`:''}`}/>
      <Kpi icon={BarChart3} tone="green" label="Net Profit (Today)" value={naira(t.profit,true)} valueTone={t.profit<0?'red':'green'} className="mint" foot={`ROI: ${t.roi===null?'—':(t.roi>0?'+':'')+pct(t.roi)}`}/>
      <Kpi icon={Shield} tone="violet" label="Daily Risk (Stake)" value={`${naira(t.staked)} / ${naira(r.daily_stake_limit)}`} valueTone="violet fit" progress={t.risk_ratio} barTone="violet"/>
    </div>
    <div className="sb-dash-charts">
      <Card icon={<TrendingUp size={20}/>} title="Performance Trend (Last 30 Days)"><TrendChart points={data.trend} total={th.profit}/></Card>
      <Card icon={<Target size={20}/>} title="Bets by Sport (Today)"><SportMix items={data.by_sport}/></Card>
      <Card icon={<BarChart3 size={20}/>} title="Last 7 Days Summary"><div className="sb-key center"><span><i className="win"/>Wins</span><span><i className="loss"/>Losses</span></div><WinLossBars days={data.last7}/></Card>
    </div>
    <section className="sb-insights">
      <div className="sb-insight lead"><span className="sb-ins-robot"><Bot size={30}/></span><div><b>Key Insights (Autonomous)</b>
        <div className="sb-ins-target"><span className={'sb-ins-icon '+(data.insights.on_target?'green':'amber')}>{data.insights.on_target?<CheckCircle2 size={20}/>:<Clock3 size={18}/>}</span><div><strong>{data.insights.on_target?'On target':'Building toward target'}</strong><small>{data.insights.on_target?'Execution within daily plan':`${t.executed} of ${r.daily_target[0]}–${r.daily_target[1]} planned singles`}</small></div></div></div></div>
      <div className="sb-insight"><span className="sb-ins-icon green sq"><TrendingUp size={20}/></span><div><b>Profitability</b><small>{naira(th.profit,true)} (30 days)</small></div></div>
      <div className="sb-insight"><span className="sb-ins-icon violet"><Target size={20}/></span><div><b>Consistency</b><small>{th.win_rate===null?'Awaiting settled results':`${pct(th.win_rate)} win rate`}</small></div></div>
      <div className="sb-insight"><span className="sb-ins-icon violet sq"><Layers3 size={20}/></span><div><b>Risk Status</b><small>{data.insights.within_risk?'Within limit':'Limit reached'} ({pct(t.risk_ratio,0)})</small></div></div>
      <div className="sb-insight"><span className="sb-ins-icon red"><Zap size={20}/></span><div><b>System Health</b><small>{data.insights.system_healthy?'Running optimally':'Needs attention'}</small></div></div>
      <button className="sb-icon-btn" onClick={refresh} title="Refresh" aria-label="Refresh"><RefreshCcw size={16}/></button>
    </section>
    <LeagueCoverage/>
    <p className="sb-footnote">{data.mode==='LIVE'?'Profit and win rate are computed from settled singles; real-money submissions are reconciled separately against SportyBet Bet History.':'Paper records: figures come from stored singles settled against verified final results. The balance is read from your signed-in SportyBet account and is never simulated.'}</p>
    </>}
  </div>}

/* ---------------------------------------------------------------- Engine */

const STAGE_ICON={Discover:ScanSearch,'Collect Odds':RefreshCcw,Validate:ShieldCheck,Analyse:BrainCircuit,Qualify:CheckCircle2,'Tickets (Single)':Ticket,Settle:Clock3,Learn:BarChart3,Optimize:Settings2};
function Pipeline({stages}){
  return <Card className="sb-pipeline">
    <div className="sb-pipe-intro"><span className="sb-pipe-icon"><Layers3 size={30}/></span><div><b>Intelligence Pipeline (Shared – Existing System)</b><small>Feeds qualified opportunities to the Single Betting Engine</small></div></div>
    <div className="sb-pipe">{stages.length?stages.map((s,i)=>{const state=s.status==='Completed'?'done':s.status==='Running'?'run':s.status==='Attention'||s.status==='Failed'?'warn':'idle';const Glyph=STAGE_ICON[s.name]||Circle;
      return <React.Fragment key={s.name}>{i>0&&<span className="sb-arrow" aria-hidden>➜</span>}
        <div className={'sb-stage '+state} title={`${s.name}: ${s.status} · ${s.count}`}>
          <span className="sb-stage-badge">{state==='done'?<CheckCircle size={22} fill="currentColor" stroke="#fff"/>:state==='warn'?<AlertTriangle size={20}/>:state==='run'?<CheckCircle size={22} fill="currentColor" stroke="#fff"/>:<Clock3 size={20}/>}</span>
          <span className="sb-stage-name">{s.name}</span>
          <span className="sb-stage-glyph">{state==='run'?<RefreshCcw size={14}/>:state==='done'?<Glyph size={14}/>:<Glyph size={14}/>}</span>
        </div></React.Fragment>}):<span className="sb-muted">Pipeline status unavailable.</span>}</div>
  </Card>}

function KillSwitch({paused,onDone}){const[open,setOpen]=useState(false),[key,setKey]=useState(''),[busy,setBusy]=useState(false),[msg,setMsg]=useState('');
  async function act(){setBusy(true);setMsg('');try{await post(`/single-betting/execution/${paused?'resume':'kill-switch'}`,{},{'X-Sports-Admin-Key':key});setOpen(false);setKey('');onDone()}catch(e){setMsg(e.message)}finally{setBusy(false)}}
  if(!open)return <button className={'sb-kill '+(paused?'resume':'')} title={paused?'Resume the engine':'Kill switch: pause all selection and placement'} aria-label={paused?'Resume engine':'Kill switch'} onClick={()=>setOpen(true)}>{paused?<PlayCircle size={22}/>:<PauseCircle size={22}/>}</button>;
  return <div className="sb-inline-form"><input type="password" name="sports-admin-key" autoComplete="new-password" aria-label="Admin key" placeholder="Admin key" value={key} onChange={e=>setKey(e.target.value)} autoFocus/><button className="sb-btn sm danger" disabled={!key||busy} onClick={act}>{busy?'…':paused?'Resume':'Pause'}</button><button className="sb-btn sm" onClick={()=>setOpen(false)}>Cancel</button>{msg&&<small className="red">{msg}</small>}</div>}

function useEvidence(stamp){const[url,setUrl]=useState(null);
  useEffect(()=>{let cancelled=false,made=null;
    fetch(`${API_BASE}/single-betting/account/evidence/latest`,{credentials:'include'}).then(r=>r.ok?r.blob():null).then(b=>{if(b&&!cancelled){made=URL.createObjectURL(b);setUrl(made)}}).catch(()=>{});
    return()=>{cancelled=true;if(made)URL.revokeObjectURL(made)}},[stamp]);
  return url}

function AutomationCard({live,onManage}){
  const last=live?.orders?.[0];const shot=useEvidence(last?.id||'none');
  if(!live)return null;
  const acc=live.account,ex=live.execution,connected=acc.status==='CONNECTED';
  const lastPrep=(live.orders||[]).find(o=>['REHEARSED','PLACED','SETTLED_WON','SETTLED_LOST','SETTLED_VOID','FAILED','REJECTED'].includes(o.status));
  const navOk=lastPrep?['REHEARSED','PLACED','SETTLED_WON','SETTLED_LOST','SETTLED_VOID'].includes(lastPrep.status):null;
  const pill=connected&&acc.session_fresh?['green','Connected & Active']:acc.status==='EXPIRED'?['red','Session expired']:acc.status==='AWAITING_LOGIN'?['amber','Waiting for login']:connected?['amber','Connected · check due']:['grey','Not connected'];
  return <Card className="sb-auto" icon={<span className="sb-s">S</span>} title="SportyBet Automation (Browser)">
    <span className={'sb-pill '+pill[0]}><i/>{pill[1]}</span>
    <div className="sb-auto-body">
      <button className="sb-shot" onClick={onManage} title={last?`Latest betslip screenshot · ${hhmm(last.created_at)}`:'No betslip screenshot yet'}>
        {shot?<img src={shot} alt="Latest betslip prepared in your SportyBet account"/>:<span className="sb-shot-empty"><ImageIcon size={22}/>No screenshot yet</span>}</button>
      <ul className="sb-checks tight">
        <Check ok={connected}>Site access: <em className={connected?'pos':'muted'}>{connected?'Online':'Offline'}</em></Check>
        <Check ok={acc.session_fresh}>Session: <em className={acc.session_fresh?'pos':'amber'}>{acc.session_fresh?'Active':connected?'Check due':acc.status==='EXPIRED'?'Expired':'None'}</em></Check>
        <Check ok={navOk}>Navigation: <em className={navOk?'pos':navOk===false?'red':'muted'}>{navOk?'Ready':navOk===false?'Last run failed':'Untested'}</em></Check>
        <Check ok={ex.mode!=='PAPER'}>Bet placement: <em className={ex.submission_allowed?'red':ex.mode!=='PAPER'?'pos':'muted'}>{ex.submission_allowed?'Enabled (real)':ex.mode==='LIVE_REHEARSAL'?'Rehearsal':ex.mode==='LIVE'?'Awaiting auth':'Paper only'}</em></Check>
        <Check ok={false}>Code capture: <em className="muted">Disabled</em></Check>
      </ul>
    </div>
    <div className="sb-auto-foot"><span>Balance <b>{live.balance.value==null?'—':naira(live.balance.value)}</b>{live.balance.observed_at&&<small> · {hhmm(live.balance.observed_at)}</small>}</span><button className="sb-link" onClick={onManage}>Manage account <ChevronRight size={14}/></button></div>
  </Card>}

const MODE_LABEL={PAPER:'Paper only',LIVE_REHEARSAL:'Live rehearsal',LIVE:'Live – real money'};
function AccountDrawer({live,open,onClose,refresh}){
  const[key,setKey]=useState(''),[busy,setBusy]=useState(''),[msg,setMsg]=useState(''),[ack,setAck]=useState(''),[hours,setHours]=useState(24);
  useEffect(()=>{if(!open)return;const esc=e=>e.key==='Escape'&&onClose();window.addEventListener('keydown',esc);return()=>window.removeEventListener('keydown',esc)},[open,onClose]);
  if(!open||!live)return null;
  const acc=live.account,bal=live.balance,ex=live.execution,rec=live.reconciliation;const admin={'X-Sports-Admin-Key':key};
  async function act(label,path,body={},needsKey=true){setBusy(label);setMsg('');try{const r=await post('/single-betting/account'+path,body,needsKey?admin:{});setMsg(label+(r&&r.status?`: ${r.status}`:' done')+(r&&r.detail?` – ${r.detail}`:'.'));refresh()}catch(e){setMsg(e.message)}finally{setBusy('')}}
  return <div className="sb-drawer-wrap" role="dialog" aria-modal="true" aria-label="SportyBet account and authorization">
    <button className="sb-drawer-scrim" aria-label="Close" onClick={onClose}/>
    <aside className="sb-drawer">
      <div className="sb-drawer-head"><div className="sb-card-title"><span className="sb-s">S</span><div><h3>SportyBet Account</h3><p>Session, balance, rehearsals and real-money authorization</p></div></div><button className="sb-icon-btn" onClick={onClose} aria-label="Close"><X size={18}/></button></div>
      <div className="sb-drawer-body">
        <ul className="sb-checks">
          <Check ok={acc.status==='CONNECTED'} value={acc.status.replace('_',' ')} tone={acc.status==='CONNECTED'?'pos':'amber'}>Account connectivity</Check>
          <Check ok={acc.session_fresh} value={acc.last_ok_at?ago(acc.last_ok_at):'never'} tone={acc.session_fresh?'pos':'amber'}>Session health</Check>
          <Check ok={bal.status==='SYNCED'} value={bal.value==null?'—':naira(bal.value)} tone={bal.status==='SYNCED'?'pos':'amber'}>Balance {bal.observed_at?`(read ${hhmm(bal.observed_at)})`:''}</Check>
          <Check ok={!!rec.last_history_capture} value={rec.last_history_capture?ago(rec.last_history_capture):'never'}>Bet history read</Check>
          <Check ok={rec.unclear===0} value={rec.unclear?`${rec.unclear} unclear`:'Clear'} tone={rec.unclear?'red':'pos'}>Transaction reconciliation</Check>
        </ul>
        {acc.detail&&<p className="sb-note">{acc.detail}</p>}
        <label className="sb-field"><span>Admin key</span><input type="password" name="sports-admin-key" autoComplete="new-password" placeholder="Required for account actions" value={key} onChange={e=>setKey(e.target.value)}/></label>
        <div className="sb-btn-row">
          {acc.status!=='CONNECTED'?<button className="sb-btn primary" disabled={!key||!!busy||acc.login_window_open} onClick={()=>act('Login window','/connect')}>{acc.login_window_open?'Login window open…':'Connect account'}</button>
            :<button className="sb-btn" disabled={!key||!!busy} onClick={()=>act('Disconnect','/disconnect')}>Disconnect</button>}
          <button className="sb-btn" disabled={!!busy||acc.status==='NOT_CONNECTED'} onClick={()=>act('Session check','/check',{},false)}>{busy==='Session check'?'Checking…':'Check session & sync'}</button>
          <button className="sb-btn" title="Prepares a ₦100 single on a currently priced match and checks the betslip. Place Bet is never clicked. Does not count toward authorization." disabled={!key||!!busy||acc.status!=='CONNECTED'} onClick={()=>act('Test rehearsal','/test-rehearsal')}>{busy==='Test rehearsal'?'Rehearsing…':'Test rehearsal'}</button>
        </div>
        <h4>Execution mode</h4>
        <div className="sb-seg">{ex.modes.map(m=><button key={m} className={ex.mode===m?'on '+m.toLowerCase():''} disabled={!key||!!busy||ex.mode===m} onClick={()=>act('Mode '+m,'/mode',{mode:m})}>{MODE_LABEL[m]}</button>)}</div>
        <h4>Real-money authorization</h4>
        {ex.authorization?<div className="sb-auth-on"><CheckCircle2 size={16}/><span>Active until {new Date(ex.authorization.expires_at).toLocaleString('en-GB',{timeZone:'Africa/Lagos'})} WAT · ₦100 × max {ex.authorization.daily_count}/day · ₦{ex.authorization.daily_stake.toLocaleString()} ceiling</span><button className="sb-btn sm danger" disabled={!key||!!busy} onClick={()=>act('Revoke','/revoke')}>Revoke</button></div>
        :<><p className="sb-note">Rehearsals passed: <b>{ex.rehearsals_passed}/{ex.rehearsals_required}</b> in the last 7 days (test rehearsals excluded). A balance never grants authorization. Type exactly:</p><code className="sb-code">{ex.acknowledgement}</code>
          <div className="sb-btn-row"><input className="grow" value={ack} onChange={e=>setAck(e.target.value)} placeholder="Acknowledgement"/><input type="number" min="1" max="168" value={hours} onChange={e=>setHours(Number(e.target.value))} className="hours" aria-label="Hours"/><span className="sb-note">h</span>
          <button className="sb-btn danger" disabled={!key||!!busy||ack!==ex.acknowledgement||ex.rehearsals_passed<ex.rehearsals_required} onClick={()=>act('Authorization','/authorize',{acknowledgement:ack,hours})}>Authorize</button></div></>}
        <p className="sb-note">Live today: {ex.live_today.count}/{ex.live_today.limit_count} · {naira(ex.live_today.staked)} / {naira(ex.live_today.limit_stake)}. {ex.submission_allowed?<b className="red">Real-money submission is ON.</b>:<>Submission blocked: {ex.blocked_by.join('; ')||'—'}.</>}</p>
        {msg&&<p className="sb-msg">{msg}</p>}
      </div>
    </aside></div>}

function QueueTable({queue,auto,c,s,filters}){
  if(!queue.length){const reasons=Object.entries(filters||{}).sort((a,b)=>b[1]-a[1]).slice(0,4);
    return <div className="sb-empty"><Search size={22}/><b>{auto.paused?'Kill switch active':c.remaining===0?'Daily cap reached':'No selection passes every rule right now'}</b>
      <span>{auto.paused?'No selections are being queued.':c.remaining===0?'Selection resumes on the next Africa/Lagos day.':`Qualified · fresh SportyBet price · odds ≥ ${(s.min_odds??1.2).toFixed(2)} · EV ≥ +${s.min_ev.toFixed(2)} · kickoff > 60s`}</span>
      {reasons.length>0&&<div className="sb-chips">{reasons.map(([k,v])=><span key={k}>{k.replace(/_/g,' ').toLowerCase()} <b>{v}</b></span>)}</div>}</div>}
  return <table className="sb-table"><thead><tr><th>#</th><th>Sport</th><th>Match / Market</th><th>Selection</th><th>Odds</th><th>Stake</th><th>Stage</th><th>ETA</th></tr></thead>
    <tbody>{queue.map((q,i)=><tr key={q.id}><td className="num">{i+1}</td><td><span className={'sb-sport '+q.sport}><span>{SPORT_ICON[q.sport]||'🎯'}</span>{sportName(q.sport)}</span></td><td><b>{q.kind==='COMBO'&&<span className="sb-stage-chip queued">Combined ×{q.legs.length}</span>}{q.kind==='FAVOURITE'&&<span className="sb-stage-chip fav" title="No positive edge · paper only">Favourite · paper</span>}{q.kind==='TICKET'&&<span className="sb-stage-chip ticket" title="Booked accumulator followed as a paper bet">Ticket ×{q.legs.length}{q.booking_code?' · '+q.booking_code:''}</span>} {q.fixture}</b><small className="blue">{q.competition}</small></td><td>{q.selection}</td><td><b>{q.decimal_odds.toFixed(2)}</b></td><td>{naira(q.stake)}</td>
      <td>{q.stage==='Placing'?<span className="sb-stage-chip placing"><RefreshCcw size={13}/>Placing Bet</span>:<span className="sb-stage-chip queued"><Clock3 size={13}/>Queued</span>}</td><td className="num">{eta(q.eta_seconds)}</td></tr>)}</tbody></table>}

function PlacedTable({rows}){if(!rows.length)return <div className="sb-empty"><Ticket size={22}/><b>No singles placed today</b><span>Placed singles and their results appear here.</span></div>;
  return <table className="sb-table"><thead><tr><th>Time</th><th>Match</th><th>Selection</th><th>Odds</th><th>Status</th><th>P/L</th></tr></thead><tbody>{rows.map(b=><tr key={b.id}><td className="num">{hhmm(b.placed_at||b.created_at)}</td><td><b>{SPORT_ICON[b.sport]||'🎯'} {b.kind==='COMBO'&&<span className="sb-stage-chip queued">Combined ×{b.legs.length}</span>}{b.kind==='FAVOURITE'&&<span className="sb-stage-chip fav" title="No positive edge · paper only">Favourite · paper</span>}{b.kind==='TICKET'&&<span className="sb-stage-chip ticket" title="Booked accumulator followed as a paper bet">Ticket ×{b.legs.length}{b.booking_code?' · '+b.booking_code:''}</span>} {b.fixture}</b></td><td>{b.selection}</td><td><b>{b.decimal_odds.toFixed(2)}</b></td><td><span className={'sb-status '+b.status.toLowerCase()} title={b.reason||''}>{b.status==='PLACED'?'Pending result':b.status.charAt(0)+b.status.slice(1).toLowerCase()}</span></td><td className={b.profit>0?'pos':b.profit<0?'red':''}>{b.profit===null?'—':naira(b.profit,true)}</td></tr>)}</tbody></table>}

function ExecutionsTable({orders}){if(!orders.length)return <div className="sb-empty"><ShieldCheck size={22}/><b>No SportyBet executions yet</b><span>Rehearsals prepare the betslip in your account without clicking Place Bet. Submissions are real money and are attempted once.</span></div>;
  return <table className="sb-table"><thead><tr><th>Time</th><th>Kind</th><th>Match</th><th>Odds</th><th>Status</th><th>Ticket</th><th>Detail</th></tr></thead><tbody>
  {orders.map(o=><tr key={o.id}><td className="num">{hhmm(o.created_at)}</td><td>{o.kind==='SUBMISSION'?<b className="red">Real</b>:o.test?'Test':'Rehearsal'}</td><td><b>{o.home?`${o.home} vs ${o.away}`:'—'}</b></td><td>{o.page_odds?o.page_odds.toFixed(2):'—'}</td>
    <td><span className={'sb-status '+(['PLACED','REHEARSED','SETTLED_WON'].includes(o.status)?'won':['UNKNOWN','FAILED','SETTLED_LOST'].includes(o.status)?'lost':'cancelled')}>{o.status.replace('SETTLED_','').toLowerCase()}</span></td><td>{o.bookmaker_ref||'—'}</td>
    <td className="detail" title={o.error||''}>{o.error?o.error.slice(0,80):o.checks?Object.entries(o.checks).filter(([,v])=>!v).map(([k])=>k).join(', ')||'All checks passed':'—'}</td></tr>)}
  </tbody></table>}

function ActivityLog({events,refresh}){const[all,setAll]=useState(false);const shown=events;
  return <Card className="sb-log" icon={<ListChecks size={20}/>} title="Live Activity Log" action={<button className="sb-link" onClick={()=>setAll(!all)}>{all?'Show less':'View All'}</button>}>
    <ul className={'sb-scroll sb-log-list'+(all?' tall':'')}>{shown.length?shown.map((e,i)=>{const bad=['CANCELLED','EXCEPTION','SESSION_EXPIRED'].includes(e.action);
      return <li key={i} title={e.detail}><span className={'sb-check '+(bad?'off':'ok')}>{bad?<XCircle size={17}/>:<CheckCircle size={17} fill="currentColor" stroke="#fff"/>}</span><time>{hhmm(e.created_at)}</time><span className="txt">{e.detail}</span></li>})
      :<li className="sb-muted">No engine activity recorded yet.</li>}</ul></Card>}

export function SingleBettingEngine(){
  const {data,error,loading,refresh}=useLiveResource('/single-betting/engine',10000);
  const[tab,setTab]=useState('queue'),[drawer,setDrawer]=useState(false);
  const s=data?.strategy,c=data?.cards,h=data?.health,a=data?.automation,auto=data?.autonomous,f=data?.focus,live=data?.live;
  const tabs=useMemo(()=>data?[['queue','Queue',data.queue.length],['placed','Placed today',data.recent.length],['exec','SportyBet executions',live?.orders?.length||0]]:[],[data,live]);
  return <div className="sb">
    <PageHeader icon={<Bot size={40}/>} title="Single Betting AI Engine" subtitle="Autonomous Selection • Validation • Execution • Monitoring • Settlement • Learning (No Human Input)" auto={auto} label="Engine Running" running="Autonomous" mode={data?.mode}/>
    <Loading error={error} loading={loading&&!data}/>
    {data&&<>
    <Pipeline stages={data.pipeline}/>
    <div className="sb-engine">
      <Card className="sb-strategy sb-a-strat">
        <span className="sb-strat-icon"><Bot size={36}/></span>
        <div className="sb-strat-text"><b>Single Betting Execution Engine</b>
          <span>Strategy: Single Selection (1 per bet)<i>|</i>Stake: {naira(s.stake)} (Fixed)<i>|</i>Daily Target: {s.daily_target[0]} – {s.daily_target[1]}<i>|</i>Max Bets: {s.daily_limit}</span></div>
        <span className={'sb-pill '+(auto.paused?'amber':auto.running?'green':'grey')}><i/>{auto.paused?'Paused':auto.running?'Autonomous Operation':'Waiting for worker'}</span>
        <span className="sb-info" tabIndex={0} aria-label="Engine rules"><Info size={22}/><span className="sb-tip">Fixed ₦{s.stake} single per bet, at most {s.daily_limit} a day and ₦{s.daily_stake_limit.toLocaleString()} staked. Only QUALIFIED selections with a fresh SportyBet price, odds ≥ {(s.min_odds??1.2).toFixed(2)} and EV ≥ +{s.min_ev.toFixed(2)} are queued; each is re-validated before placement. High-probability selections priced below {(s.min_odds??1.2).toFixed(2)} may be combined ({s.combo_max_legs??3} legs max, combined probability ≥ {Math.round((s.combo_min_probability??0.8)*100)}%) to reach the minimum, and replace a single whenever they are more likely to win. Selections are ranked by chance of winning, then EV. When no edge selection is available, up to {s.favourite_daily_limit??10} strong favourites a day are placed as paper bets only (no edge; never submitted to SportyBet).</span></span>
        <KillSwitch paused={auto.paused} onDone={refresh}/>
      </Card>
      <div className="sb-a-right">
      <button className="sb-card sb-realmoney" onClick={()=>setDrawer(true)} aria-label="Real money execution settings">
        <div><b>Real Money Execution</b><small>{data.real_money_enabled?'ON – authorized, LIVE mode, session healthy':'Requires explicit authorization'}</small></div>
        <span className={'sb-switch'+(data.real_money_enabled?' on':'')} role="switch" aria-checked={data.real_money_enabled}><Lock size={11}/></span></button>
      <Card className="sb-health" icon={<Zap size={22}/>} title="Engine Health"><ul className="sb-checks">
        <Check ok={h.intelligence_feed==='Active'} value={h.intelligence_feed} tone="pos">Intelligence feed:</Check>
        <Check ok={h.selection_engine==='Running'} value={h.selection_engine==='Waiting for first cycle'?'Starting':h.selection_engine} tone={h.selection_engine==='Running'?'pos':'amber'}>Selection engine:</Check>
        <Check ok={h.execution_worker==='Running'} value={h.execution_worker} tone={h.execution_worker==='Running'?'pos':'red'}>Execution worker:</Check>
        <Check ok={h.browser_session==='CONNECTED'} value={h.browser_session==='CONNECTED'?'Connected':sportName(String(h.browser_session).toLowerCase())} tone={h.browser_session==='CONNECTED'?'pos':'amber'}>Browser session:</Check>
        <Check ok={!!h.data_sync} value={h.data_sync?'Live':'No fresh price'} tone={h.data_sync?'pos':'amber'}>Data sync:</Check>
        <Check ok={!!h.last_update} value={h.last_update?new Date(h.last_update).toLocaleTimeString('en-GB',{timeZone:'Africa/Lagos'}):'—'}>Last update:</Check>
      </ul></Card>
      <ActivityLog events={data.events} refresh={refresh}/>
      </div>
      <div className="sb-kpis five sb-a-kpis">
        <Kpi icon={Target} tone="blue" label="Selections Ready" value={c.ready} valueTone="blue" foot="for Execution" className="center"/>
        <Kpi icon={Layers3} tone="violet" label="In Execution" value={c.in_execution} valueTone="violet" foot="(Placing Bets)" className="center"/>
        <Kpi icon={CheckCircle2} tone="green" label="Executed Today" value={`${c.executed} / ${s.daily_limit}`} valueTone="navy" progress={c.executed/s.daily_limit} className="center"/>
        <Kpi icon={CalendarDays} tone="orange" label="Remaining Capacity" value={c.remaining} valueTone="maroon" foot="(Within Daily Target)" className="center peach"/>
        <Kpi icon={AlertTriangle} tone="red" label="Exceptions" value={c.exceptions} valueTone="red" foot={c.exceptions?'(Removed from queue today)':'(No Action Required)'} className="center"/>
      </div>
      <div className="sb-a-main">
        <Card className="sb-queue" icon={<Target size={22}/>} title="Execution Queue (Live – Single Selections)" sub="Qualified selections from the shared intelligence pipeline, executed autonomously.">
          <div className="sb-tabs" role="tablist">{tabs.map(([k,l,n])=><button key={k} role="tab" aria-selected={tab===k} className={tab===k?'on':''} onClick={()=>setTab(k)}>{l}<span>{n}</span></button>)}</div>
          <div className="sb-table-scroll sb-scroll">{tab==='queue'?<QueueTable queue={data.queue} auto={auto} c={c} s={s} filters={data.last_cycle?.rejected}/>:tab==='placed'?<PlacedTable rows={data.recent}/>:<ExecutionsTable orders={live?.orders||[]}/>}</div>
        </Card>
      </div>
      <div className="sb-a-mid">
        <AutomationCard live={live} onManage={()=>setDrawer(true)}/>
        <Card className="sb-focus-card" icon={<Target size={22}/>} title="Current Selection Detail (In Focus)">
          {f?<div className="sb-focus"><span className="sb-focus-icon">{SPORT_ICON[f.sport]||'🎯'}</span><div className="sb-focus-match"><b>{f.fixture}</b><small className="blue">{f.competition} · {hhmm(f.starts_at)} WAT</small></div>
            <div><small>Selection</small><b>{f.selection}</b></div><div><small>Odds</small><b>{f.decimal_odds.toFixed(2)}</b></div><div><small>Stake</small><b>{naira(f.stake)}</b></div>
            <div className="sb-focus-prob"><small>AI Probability</small><b>{pct(f.probability)}</b><Bar value={f.probability} tone="green"/></div></div>
          :<div className="sb-empty slim"><Sparkles size={20}/><b>Nothing in focus</b><span>The next queued single appears here with its probability and price.</span></div>}
        </Card>
      </div>
      <div className="sb-a-rules">
        <Card icon={<Settings2 size={22}/>} title="Automation Controls"><ul className="sb-checks pills">
          {[['Auto-select (from pipeline)',a.auto_select],[`Auto-place ${data.mode==='LIVE'?'bet':'paper bet'} (₦100)`,a.auto_place_paper],['Auto-capture booking code',a.booking_code_capture],['Auto-monitor & settle',a.auto_settle],['Auto-learn & optimize',a.auto_learn],['Real-money submission',a.real_money]].map(([l,v])=><Check key={l} ok={v} value={v?'ON':'OFF'} tone={v?'on':'off'}>{l}</Check>)}
        </ul></Card>
        <Card icon={<ShieldCheck size={22}/>} title="Execution Rules (Active)"><ul className="sb-checks ruled">
          {['Single selection per bet',`${naira(s.stake)} fixed stake`,`${s.daily_target[0]} – ${s.daily_target[1]} bets per day (Max ${s.daily_limit})`,'Risk & correlation checks',`Only qualified opportunities (EV ≥ +${s.min_ev.toFixed(2)})`,`Minimum odds ${(s.min_odds??1.2).toFixed(2)} · lower prices combined when probability ≥ ${Math.round((s.combo_min_probability??0.8)*100)}%`,'Highest chance of winning first · combined preferred when more likely',`Favourites (no edge): paper only, max ${s.favourite_daily_limit??10}/day`,'Booked tickets followed as ₦100 paper bets',data.mode==='LIVE'?'No human input · real money under authorization':data.mode==='LIVE_REHEARSAL'?'No human input · rehearsal, Place Bet not clicked':'No human input · paper only'].map(r=><Check key={r} ok>{r}</Check>)}
        </ul></Card>
      </div>
    </div>
    <AccountDrawer live={live} open={drawer} onClose={()=>setDrawer(false)} refresh={refresh}/>
    </>}
  </div>}
