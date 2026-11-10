import React,{useEffect,useState} from 'react';
import {Bot,Wallet,Database,Target,Trophy,BarChart3,Shield,Layers3,CheckCircle2,CalendarDays,AlertTriangle,RefreshCcw,Clock3,Settings2,ListChecks,Zap,Info,Search,Activity,ShieldCheck,TrendingUp,PauseCircle,PlayCircle,XCircle,Circle,Lock} from 'lucide-react';
import useLiveResource from './useLiveResource';
import {post} from './api';
import './single-betting.css';

const naira=(n,sign=false)=>n===null||n===undefined?'—':`${sign&&n>0?'+':n<0?'−':''}₦${Math.abs(Number(n)).toLocaleString('en-NG',{maximumFractionDigits:0})}`;
const pct=(n,d=1)=>n===null||n===undefined?'—':`${(Number(n)*100).toFixed(d)}%`;
const SPORT_ICON={football:'⚽',basketball:'🏀',tennis:'🎾',ice_hockey:'🏒',volleyball:'🏐',handball:'🤾',baseball:'⚾'};
const sportName=s=>String(s||'').replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
const hhmm=v=>{if(!v)return '—';const d=new Date(v);return Number.isNaN(d.getTime())?'—':d.toLocaleTimeString('en-GB',{timeZone:'Africa/Lagos',hour:'2-digit',minute:'2-digit'})};
const ago=v=>{if(!v)return 'never';const s=Math.round((Date.now()-new Date(v).getTime())/1000);if(s<60)return `${Math.max(s,0)}s ago`;if(s<3600)return `${Math.round(s/60)}m ago`;return `${Math.round(s/3600)}h ago`};
const eta=s=>s===null||s===undefined?'—':`${String(Math.floor(s/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`;

function Clock(){const[now,setNow]=useState(new Date());useEffect(()=>{const t=setInterval(()=>setNow(new Date()),1000);return()=>clearInterval(t)},[]);
  const date=now.toLocaleDateString('en-GB',{timeZone:'Africa/Lagos',weekday:'short',day:'numeric',month:'short',year:'numeric'});
  const time=now.toLocaleTimeString('en-GB',{timeZone:'Africa/Lagos',hour12:false});
  return <div className="sb-clock"><span>Africa/Lagos (WAT)</span><strong>{date} {time}</strong></div>}

function ModePill({auto,label}){const paused=auto?.paused,running=auto?.running;
  const kind=paused?'amber':running?'green':'grey';
  const text=paused?'Paused (Kill Switch)':running?`${label} (Running)`:'Worker Heartbeat Stale';
  return <span className={'sb-pill '+kind}><i/>{text}</span>}

function Header({title,subtitle,auto,label='Autonomous Mode',mode='PAPER'}){return <div className="sb-header">
  <div className="sb-heading"><div className="sb-robot"><Bot size={30}/></div><div><h1>{title}</h1><p>{subtitle}</p></div></div>
  <div className="sb-header-right"><span className={'sb-pill '+(mode==='LIVE'?'live':'paper')}><Shield size={13}/>{mode==='LIVE'?'Live – real money':mode==='LIVE_REHEARSAL'?'Live rehearsal':'Paper mode'}</span><ModePill auto={auto} label={label}/><Clock/></div></div>}

function Loading({error,loading}){if(error)return <div className="sb-error"><AlertTriangle size={16}/>{error}</div>;if(loading)return <div className="sb-panel sb-muted">Loading live engine state…</div>;return null}

function Kpi({icon:Icon,tone,label,value,sub,progress,valueTone}){return <article className="sb-kpi">
  <div className={'sb-kpi-icon '+tone}><Icon size={22}/></div>
  <div className="sb-kpi-body"><span>{label}</span><strong className={valueTone||''}>{value}</strong>
    {progress!==undefined?<div className="sb-progress-row"><div className={'sb-progress '+(tone==='violet'?'violet':'')}><b style={{width:`${Math.min(100,Math.max(0,progress*100))}%`}}/></div><small>{Math.round(progress*100)}%</small></div>:<small>{sub}</small>}
  </div></article>}

/* ---------------- charts (inline SVG, no dependency) ---------------- */
function TrendChart({points}){const W=520,H=210,L=52,R=12,T=14,B=26;
  const vals=points.map(p=>p.cumulative);const lo=Math.min(0,...vals),hi=Math.max(100,...vals);const pad=(hi-lo)*0.1||100;const y0=lo-pad,y1=hi+pad;
  const x=i=>L+(i*(W-L-R))/Math.max(1,points.length-1);const y=v=>T+(1-(v-y0)/(y1-y0))*(H-T-B);
  const path=points.map((p,i)=>`${i?'L':'M'}${x(i).toFixed(1)},${y(p.cumulative).toFixed(1)}`).join(' ');
  const area=points.length?`${path} L${x(points.length-1)},${y(y0)} L${x(0)},${y(y0)} Z`:'';
  const ticks=Array.from({length:5},(_,i)=>y0+((y1-y0)*i)/4);
  const labels=points.map((p,i)=>(i%4===0&&points.length-1-i>=3)||i===points.length-1?[i,new Date(p.date+'T12:00:00Z').toLocaleDateString('en-GB',{month:'short',day:'2-digit'})]:null).filter(Boolean);
  const last=points[points.length-1];
  return <svg viewBox={`0 0 ${W} ${H}`} className="sb-chart" role="img" aria-label="Cumulative paper profit over the last 30 days">
    <defs><linearGradient id="sbArea" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#22c55e" stopOpacity=".28"/><stop offset="1" stopColor="#22c55e" stopOpacity="0"/></linearGradient></defs>
    {ticks.map((t,i)=><g key={i}><line x1={L} x2={W-R} y1={y(t)} y2={y(t)} className="sb-grid-line"/><text x={L-6} y={y(t)+3} textAnchor="end" className="sb-axis">{naira(Math.round(t/100)*100)}</text></g>)}
    {lo<0&&<line x1={L} x2={W-R} y1={y(0)} y2={y(0)} className="sb-zero"/>}
    <path d={area} fill="url(#sbArea)"/><path d={path} className="sb-line"/>
    {points.map((p,i)=><circle key={p.date} cx={x(i)} cy={y(p.cumulative)} r="3" className="sb-dot"><title>{`${p.date}: ${naira(p.cumulative,true)} cumulative (${naira(p.profit,true)} that day)`}</title></circle>)}
    {labels.map(([i,l])=><text key={i} x={x(i)} y={H-6} textAnchor="middle" className="sb-axis">{l}</text>)}
    {last&&<circle cx={x(points.length-1)} cy={y(last.cumulative)} r="5" className="sb-dot-end"/>}
  </svg>}

const DONUT=['#22c55e','#f59e0b','#8b5cf6','#0ea5e9','#ef4444','#14b8a6'];
function Donut({items,total}){const r=54,c=2*Math.PI*r;let acc=0;
  return <svg viewBox="0 0 140 140" className="sb-donut" role="img" aria-label="Bets by sport today">
    <circle cx="70" cy="70" r={r} className="sb-donut-track"/>
    {items.map((it,i)=>{const len=total?(it.count/total)*c:0;const el=<circle key={it.sport} cx="70" cy="70" r={r} stroke={DONUT[i%DONUT.length]} strokeDasharray={`${len} ${c-len}`} strokeDashoffset={-acc} className="sb-donut-seg"><title>{`${sportName(it.sport)}: ${it.count}`}</title></circle>;acc+=len;return el})}
    <text x="70" y="68" textAnchor="middle" className="sb-donut-n">{total}</text><text x="70" y="88" textAnchor="middle" className="sb-donut-l">Bets</text></svg>}

function Bars({days}){const W=420,H=200,L=26,B=24,T=10;const max=Math.max(2,...days.flatMap(d=>[d.wins,d.losses]));const top=Math.ceil(max/2)*2;
  const slot=(W-L)/days.length,bw=Math.min(16,slot/3.2);const y=v=>T+(1-v/top)*(H-T-B);
  return <svg viewBox={`0 0 ${W} ${H}`} className="sb-chart" role="img" aria-label="Wins and losses over the last 7 days">
    {[0,top/2,top].map(t=><g key={t}><line x1={L} x2={W} y1={y(t)} y2={y(t)} className="sb-grid-line"/><text x={L-6} y={y(t)+3} textAnchor="end" className="sb-axis">{t}</text></g>)}
    {days.map((d,i)=>{const cx=L+slot*i+slot/2;return <g key={d.date}>
      <rect x={cx-bw-1} y={y(d.wins)} width={bw} height={y(0)-y(d.wins)} rx="2" className="sb-bar win"><title>{`${d.date}: ${d.wins} won`}</title></rect>
      <rect x={cx+1} y={y(d.losses)} width={bw} height={y(0)-y(d.losses)} rx="2" className="sb-bar loss"><title>{`${d.date}: ${d.losses} lost`}</title></rect>
      <text x={cx} y={H-6} textAnchor="middle" className="sb-axis">{new Date(d.date+'T12:00:00Z').toLocaleDateString('en-GB',{month:'short',day:'2-digit'})}</text></g>})}
  </svg>}

/* ---------------- Dashboard ---------------- */
export function SingleBettingDashboard(){
  const {data,error,loading,refresh}=useLiveResource('/single-betting/dashboard',15000);
  const t=data?.today,r=data?.rules,th=data?.thirty_day;
  const total=(data?.by_sport||[]).reduce((a,b)=>a+b.count,0);
  return <div className="sb">
    <Header title="Single Betting AI — Dashboard" subtitle="Executive overview • Results • Profitability • Trends • Risk • No manual input" auto={data?.autonomous} mode={data?.mode}/>
    <Loading error={error} loading={loading&&!data}/>
    {data&&<>
    <div className="sb-kpis six">
      <Kpi icon={Wallet} tone="blue" label="SportyBet Balance" value={data.balance.value==null?'Not connected':naira(data.balance.value)} valueTone={data.balance.value==null?'sb-small-value':''} sub={data.balance.status==='SYNCED'?<span className="pos" title={data.balance.note}><CheckCircle2 size={11}/> Synced {hhmm(data.balance.observed_at)}</span>:data.balance.status==='STALE'?<span className="sb-warn-text" title={data.balance.note}><Clock3 size={11}/> Last read {hhmm(data.balance.observed_at)}</span>:<span className="sb-warn-text" title={data.balance.note}><Info size={11}/> Never simulated</span>}/>
      <Kpi icon={Database} tone="green" label="Daily Target" value={`${r.daily_target[0]} – ${r.daily_target[1]}`} sub="Singles (target, not a minimum)"/>
      <Kpi icon={Target} tone="violet" label="Bets Executed Today" value={`${t.executed} / ${r.daily_limit}`} progress={t.executed/r.daily_limit}/>
      <Kpi icon={Trophy} tone="orange" label="Wins / Losses" value={<><span className="pos">{t.wins}</span> / <span className="neg">{t.losses}</span></>} sub={`Win rate: ${pct(t.win_rate)}${t.pending?` · ${t.pending} pending`:''}`}/>
      <Kpi icon={BarChart3} tone="green" label="Net Profit (Today)" value={naira(t.profit,true)} valueTone={t.profit>0?'pos':t.profit<0?'neg':''} sub={`ROI: ${t.roi===null?'—':(t.roi>0?'+':'')+pct(t.roi)}`}/>
      <Kpi icon={Shield} tone="violet" label="Daily Risk (Stake)" value={<>{naira(t.staked)}<em> / {naira(r.daily_stake_limit)}</em></>} progress={t.risk_ratio}/>
    </div>
    <div className="sb-row three">
      <section className="sb-panel"><div className="sb-panel-head"><h3><TrendingUp size={18}/>Performance Trend (Last 30 Days)</h3><span className={'sb-badge '+(th.profit>=0?'green':'red')}>{naira(th.profit,true)}</span></div><TrendChart points={data.trend}/></section>
      <section className="sb-panel"><div className="sb-panel-head"><h3><Target size={18}/>Bets by Sport (Today)</h3></div>
        {total?<div className="sb-donut-wrap"><Donut items={data.by_sport} total={total}/><ul className="sb-legend">{data.by_sport.map((s,i)=><li key={s.sport}><i style={{background:DONUT[i%DONUT.length]}}/><span>{SPORT_ICON[s.sport]||'🎯'} {sportName(s.sport)}</span><b>{s.count} ({pct(s.count/total)})</b></li>)}</ul></div>:<div className="sb-empty">No paper bets placed yet today.</div>}</section>
      <section className="sb-panel"><div className="sb-panel-head"><h3><BarChart3 size={18}/>Last 7 Days Summary</h3><div className="sb-key"><span><i className="win"/>Wins</span><span><i className="loss"/>Losses</span></div></div><Bars days={data.last7}/></section>
    </div>
    <section className="sb-insights">
      <div className="sb-insight lead"><Bot size={30}/><div><b>Key Insights (Autonomous)</b><span className={data.insights.on_target?'pos':''}>{data.insights.on_target?<CheckCircle2 size={14}/>:<Clock3 size={14}/>} {data.insights.on_target?'On target':'Building toward target'}</span><small>{t.executed} of {r.daily_target[0]}–{r.daily_target[1]} planned singles</small></div></div>
      <div className="sb-insight"><TrendingUp size={24} className="pos"/><div><b>Profitability</b><small>{naira(th.profit,true)} (30 days)</small></div></div>
      <div className="sb-insight"><Target size={24} className="violet"/><div><b>Consistency</b><small>{th.win_rate===null?'Awaiting settled results':`${pct(th.win_rate)} win rate`}</small></div></div>
      <div className="sb-insight"><Layers3 size={24} className="violet"/><div><b>Risk Status</b><small>{data.insights.within_risk?'Within limit':'Limit reached'} ({pct(t.risk_ratio,0)})</small></div></div>
      <div className="sb-insight"><Zap size={24} className="neg"/><div><b>System Health</b><small>{data.insights.system_healthy?'Running optimally':'Needs attention'}</small></div></div>
      <button className="sb-icon-btn" onClick={refresh} title="Refresh"><RefreshCcw size={16}/></button>
    </section>
    <p className="sb-footnote">Paper records only. Figures are computed from stored single bets settled against verified final results; no SportyBet balance, ticket or wager is simulated or submitted.</p>
    </>}
  </div>}

/* ---------------- Engine ---------------- */
const STAGE_TONE={Completed:'done',Running:'run',Attention:'warn',Failed:'warn'};
function Pipeline({stages}){return <section className="sb-panel sb-pipeline-panel">
  <div className="sb-pipe-intro"><div className="sb-pipe-icon"><Layers3 size={26}/></div><div><b>Intelligence Pipeline (Shared – Existing System)</b><small>Feeds qualified opportunities to the Single Betting Engine</small></div></div>
  <div className="sb-pipe">{stages.length?stages.map((s,i)=><React.Fragment key={s.name}>{i>0&&<span className="sb-arrow">→</span>}
    <div className={'sb-stage '+(STAGE_TONE[s.status]||'idle')} title={`${s.status} · ${s.count}`}><span>{s.name}</span>{STAGE_TONE[s.status]==='done'?<CheckCircle2 size={15}/>:STAGE_TONE[s.status]==='run'?<RefreshCcw size={14}/>:STAGE_TONE[s.status]==='warn'?<AlertTriangle size={14}/>:<Clock3 size={14}/>}</div></React.Fragment>):<span className="sb-muted">Pipeline status unavailable.</span>}</div></section>}

function Check({ok,children,value,tone}){return <li><span className={'sb-check '+(ok===null?'idle':ok?'ok':'off')}>{ok===null?<Circle size={12}/>:ok?<CheckCircle2 size={16}/>:<XCircle size={16}/>}</span><span>{children}</span>{value!==undefined&&<b className={tone||''}>{value}</b>}</li>}

const ACCOUNT_TONE={CONNECTED:'pos',AWAITING_LOGIN:'amber',EXPIRED:'neg',ERROR:'neg',NOT_CONNECTED:'muted'};
const MODE_LABEL={PAPER:'Paper only',LIVE_REHEARSAL:'Live rehearsal (no Place Bet)',LIVE:'Live – real money'};
function AccountPanel({live,refresh}){
  const[key,setKey]=useState(''),[busy,setBusy]=useState(''),[msg,setMsg]=useState(''),[ack,setAck]=useState(''),[hours,setHours]=useState(24);
  if(!live)return null;
  const acc=live.account,bal=live.balance,ex=live.execution,rec=live.reconciliation;
  const admin={'X-Sports-Admin-Key':key};
  async function act(label,path,body={},needsKey=true){setBusy(label);setMsg('');try{await post('/single-betting/account'+path,body,needsKey?admin:{});setMsg(label+' done.');refresh()}catch(e){setMsg(e.message)}finally{setBusy('')}}
  return <section className="sb-panel sb-account"><div className="sb-panel-head"><h3><span className="sb-s">S</span>SportyBet Account</h3><span className={'sb-pill '+(acc.status==='CONNECTED'?'mint':acc.status==='AWAITING_LOGIN'?'amber':'grey')}><i/>{acc.status.replace('_',' ').toLowerCase()}</span></div>
    <ul className="sb-checks tight">
      <Check ok={acc.status==='CONNECTED'} value={acc.status} tone={ACCOUNT_TONE[acc.status]}>Account connectivity</Check>
      <Check ok={acc.session_fresh} value={acc.last_ok_at?ago(acc.last_ok_at):'never'} tone={acc.session_fresh?'pos':'amber'}>Session health</Check>
      <Check ok={bal.status==='SYNCED'} value={bal.value==null?'—':naira(bal.value)} tone={bal.status==='SYNCED'?'pos':'amber'}>Balance {bal.status==='SYNCED'?'(synced '+hhmm(bal.observed_at)+')':bal.status==='STALE'?'(stale)':''}</Check>
      <Check ok={!!rec.last_history_capture} value={rec.last_history_capture?ago(rec.last_history_capture):'never'}>Bet history read</Check>
      <Check ok={rec.unclear===0} value={rec.unclear?`${rec.unclear} unclear`:'Clear'} tone={rec.unclear?'neg':'pos'}>Transaction reconciliation</Check>
      <Check ok={ex.mode!=='PAPER'} value={MODE_LABEL[ex.mode]} tone={ex.mode==='LIVE'?'neg':''}>Browser execution</Check>
    </ul>
    {acc.detail&&<p className="sb-note">{acc.detail}</p>}
    <div className="sb-acct-actions">
      <input type="password" name="sports-admin-key" autoComplete="new-password" aria-label="Admin key" placeholder="Admin key" value={key} onChange={e=>setKey(e.target.value)}/>
      {acc.status!=='CONNECTED'?<button className="sb-btn primary" disabled={!key||!!busy||acc.login_window_open} onClick={()=>act('Login window','/connect')}>{acc.login_window_open?'Login window open…':'Connect account'}</button>
        :<button className="sb-btn" disabled={!key||!!busy} onClick={()=>act('Disconnect','/disconnect')}>Disconnect</button>}
      <button className="sb-btn" disabled={!!busy||acc.status==='NOT_CONNECTED'} onClick={()=>act('Session check','/check',{},false)}>{busy==='Session check'?'Checking…':'Check session & sync'}</button>
    </div>
    <div className="sb-mode"><small>Execution mode</small><div className="sb-seg">{ex.modes.map(m=><button key={m} className={ex.mode===m?'on '+m.toLowerCase():''} disabled={!key||!!busy||ex.mode===m} onClick={()=>act('Mode '+m,'/mode',{mode:m})}>{MODE_LABEL[m]}</button>)}</div></div>
    <div className="sb-auth">
      <b>Real-money authorization</b>
      {ex.authorization?<p className="pos">Active until {new Date(ex.authorization.expires_at).toLocaleString('en-GB',{timeZone:'Africa/Lagos'})} WAT · ₦100 × max {ex.authorization.daily_count}/day · ₦{ex.authorization.daily_stake.toLocaleString()} ceiling <button className="sb-btn danger" disabled={!key||!!busy} onClick={()=>act('Revoke','/revoke')}>Revoke</button></p>
      :<><p className="sb-note">Rehearsals passed: <b>{ex.rehearsals_passed}/{ex.rehearsals_required}</b> (last 7 days). A balance never grants authorization. Type exactly: <code>{ex.acknowledgement}</code></p>
        <div className="sb-acct-actions"><input value={ack} onChange={e=>setAck(e.target.value)} placeholder="Acknowledgement"/><input type="number" min="1" max="168" value={hours} onChange={e=>setHours(Number(e.target.value))} style={{width:70,flex:'0 0 70px'}}/><span className="sb-note">hours</span>
        <button className="sb-btn danger" disabled={!key||!!busy||ack!==ex.acknowledgement||ex.rehearsals_passed<ex.rehearsals_required} onClick={()=>act('Authorization','/authorize',{acknowledgement:ack,hours})}>Authorize</button></div></>}
      <p className="sb-note">Live today: {ex.live_today.count}/{ex.live_today.limit_count} · {naira(ex.live_today.staked)} / {naira(ex.live_today.limit_stake)}. {ex.submission_allowed?<b className="neg">Real-money submission is ON.</b>:<>Submission blocked: {ex.blocked_by.join('; ')||'—'}.</>}</p>
    </div>
    {msg&&<p className="sb-note">{msg}</p>}
  </section>}

function LiveOrders({live}){if(!live||!live.orders.length)return null;return <section className="sb-panel"><div className="sb-panel-head"><div><h3><ShieldCheck size={18} className="violet"/>SportyBet Executions</h3><p>Rehearsals prepare the betslip in your account without clicking Place Bet. Submissions are real money and are attempted once.</p></div></div>
  <div className="sb-table-scroll"><table className="sb-table compact"><thead><tr><th>Time</th><th>Kind</th><th>Match</th><th>Odds (page)</th><th>Status</th><th>Ticket</th><th>Detail</th></tr></thead><tbody>
  {live.orders.map(o=><tr key={o.id}><td>{hhmm(o.created_at)}</td><td>{o.kind==='SUBMISSION'?<b className="neg">Real</b>:'Rehearsal'}</td><td>{o.home?`${o.home} vs ${o.away}`:o.bet_id}</td><td>{o.page_odds?o.page_odds.toFixed(2):'—'}</td><td><span className={'sb-status '+(['PLACED','REHEARSED','SETTLED_WON'].includes(o.status)?'won':['UNKNOWN','FAILED','SETTLED_LOST'].includes(o.status)?'lost':'cancelled')}>{o.status.replace('SETTLED_','').toLowerCase()}</span></td><td>{o.bookmaker_ref||'—'}</td><td title={o.error||''}>{o.error?o.error.slice(0,70):o.checks?Object.entries(o.checks).filter(([,v])=>!v).map(([k])=>k).join(', ')||'All checks passed':'—'}</td></tr>)}
  </tbody></table></div></section>}

function KillSwitch({paused,onDone}){const[open,setOpen]=useState(false),[key,setKey]=useState(''),[busy,setBusy]=useState(false),[msg,setMsg]=useState('');
  async function act(){setBusy(true);setMsg('');try{await post(`/single-betting/execution/${paused?'resume':'kill-switch'}`,{},{'X-Sports-Admin-Key':key});setOpen(false);setKey('');onDone()}catch(e){setMsg(e.message)}finally{setBusy(false)}}
  if(!open)return <button className={'sb-btn '+(paused?'primary':'danger')} onClick={()=>setOpen(true)}>{paused?<PlayCircle size={15}/>:<PauseCircle size={15}/>}{paused?'Resume engine':'Kill switch'}</button>;
  return <div className="sb-kill"><input type="password" name="sports-admin-key" autoComplete="new-password" aria-label="Admin key" placeholder="Admin key" value={key} onChange={e=>setKey(e.target.value)} autoFocus/><button className="sb-btn danger" disabled={!key||busy} onClick={act}>{busy?'…':paused?'Resume':'Pause'}</button><button className="sb-btn" onClick={()=>setOpen(false)}>Cancel</button>{msg&&<small className="neg">{msg}</small>}</div>}

export function SingleBettingEngine(){
  const {data,error,loading,refresh}=useLiveResource('/single-betting/engine',10000);
  const s=data?.strategy,c=data?.cards,h=data?.health,a=data?.automation,auto=data?.autonomous;
  const f=data?.focus;
  return <div className="sb">
    <Header title="Single Betting AI Engine" subtitle="Autonomous Selection • Validation • Execution • Monitoring • Settlement • Learning (No Human Input)" auto={auto} label="Engine" mode={data?.mode}/>
    <Loading error={error} loading={loading&&!data}/>
    {data&&<>
    <Pipeline stages={data.pipeline}/>
    <div className="sb-engine">
        <section className="sb-panel sb-strategy sb-a-strat">
          <div className="sb-robot small"><Bot size={26}/></div>
          <div className="sb-strategy-text"><b>Single Betting Execution Engine</b>
            <small>Strategy: Single Selection (1 per bet) <i>|</i> Stake: {naira(s.stake)} (Fixed) <i>|</i> Daily Target: {s.daily_target[0]} – {s.daily_target[1]} <i>|</i> Max Bets: {s.daily_limit} <i>|</i> Min EV: +{s.min_ev.toFixed(2)}</small></div>
          <span className={'sb-pill '+(auto.paused?'amber':auto.running?'mint':'grey')}><i/>{auto.paused?'Paused':auto.running?'Autonomous Operation':'Waiting for worker'}</span>
          <KillSwitch paused={auto.paused} onDone={refresh}/>
        </section>
        <div className="sb-kpis five sb-a-kpis">
          <Kpi icon={Target} tone="blue" label="Selections Ready" value={c.ready} sub="for Execution"/>
          <Kpi icon={Layers3} tone="violet" label="In Execution" value={c.in_execution} sub="(Queued for placement)"/>
          <Kpi icon={CheckCircle2} tone="green" label="Executed Today" value={`${c.executed} / ${s.daily_limit}`} progress={c.executed/s.daily_limit}/>
          <Kpi icon={CalendarDays} tone="orange" label="Remaining Capacity" value={c.remaining} sub="(Within Daily Target)"/>
          <Kpi icon={AlertTriangle} tone="red" label="Exceptions" value={c.exceptions} valueTone={c.exceptions?'neg':''} sub={c.exceptions?'(Queue entries removed today)':'(No Action Required)'}/>
        </div>
      <aside className="sb-engine-side sb-a-side">
        <section className="sb-panel sb-realmoney"><div><b>Real Money Execution</b><small>{data.real_money_enabled?'ON – authorized, LIVE mode, session healthy':'Requires explicit authorization'}</small></div><span className={'sb-switch'+(data.real_money_enabled?' on':'')} role="switch" aria-checked={data.real_money_enabled} aria-readonly="true" title={data.real_money_enabled?'Real money is being submitted under an active authorization.':'Set in the SportyBet Account panel: rehearsals, authorization, then LIVE mode.'}><Lock size={11}/></span></section>
        <section className="sb-panel"><div className="sb-panel-head"><h3><Zap size={18} className="violet"/>Engine Health</h3></div><ul className="sb-checks">
          <Check ok={h.intelligence_feed==='Active'} value={h.intelligence_feed} tone="pos">Intelligence feed:</Check>
          <Check ok={h.selection_engine==='Running'} value={h.selection_engine} tone={h.selection_engine==='Running'?'pos':'amber'}>Selection engine:</Check>
          <Check ok={h.execution_worker==='Running'} value={h.execution_worker} tone={h.execution_worker==='Running'?'pos':'neg'}>Execution worker:</Check>
          <Check ok={null} value="Not used (paper)">Browser session:</Check>
          <Check ok={!!h.data_sync} value={h.data_sync?ago(h.data_sync):'No fresh price'} tone={h.data_sync?'pos':'amber'}>Data sync:</Check>
          <Check ok={!!h.last_update} value={h.last_update?hhmm(h.last_update):'—'}>Last update:</Check>
        </ul></section>
      </aside>
      <div className="sb-engine-main sb-a-queue">
        <section className="sb-panel"><div className="sb-panel-head"><div><h3><Target size={18} className="violet"/>Execution Queue (Live – Single Selections)</h3><p>Qualified selections from the shared intelligence pipeline, re-validated and placed as paper singles ({s.place_per_tick} per {s.tick_seconds}s cycle).</p></div></div>
          <div className="sb-table-scroll"><table className="sb-table"><thead><tr><th>#</th><th>Sport</th><th>Match / Market</th><th>Selection</th><th>Odds</th><th>Stake</th><th>Stage</th><th>ETA</th></tr></thead>
          <tbody>{data.queue.length?data.queue.map((q,i)=><tr key={q.id}><td>{i+1}</td><td><span className={'sb-sport '+q.sport}>{SPORT_ICON[q.sport]||'🎯'} {sportName(q.sport)}</span></td><td><b>{q.fixture}</b><small>{q.competition}</small></td><td>{q.selection}</td><td><b>{q.decimal_odds.toFixed(2)}</b></td><td>{naira(q.stake)}</td><td>{q.stage==='Placing'?<span className="sb-stage-chip placing"><RefreshCcw size={12}/>Placing Bet</span>:<span className="sb-stage-chip queued"><Clock3 size={12}/>Queued</span>}</td><td>{eta(q.eta_seconds)}</td></tr>)
            :<tr><td colSpan="8" className="sb-empty">{auto.paused?'Kill switch active — no selections are being queued.':c.remaining===0?'Daily cap reached. Selection resumes on the next Africa/Lagos day.':`No selection currently passes every rule (qualified, fresh SportyBet price, EV ≥ +${s.min_ev.toFixed(2)}, kickoff > 60s).`}</td></tr>}</tbody></table></div>
          {data.recent.length>0&&<><h4 className="sb-subhead">Placed today</h4><div className="sb-table-scroll"><table className="sb-table compact"><thead><tr><th>Time</th><th>Match</th><th>Selection</th><th>Odds</th><th>Status</th><th>P/L</th></tr></thead><tbody>{data.recent.map(b=><tr key={b.id}><td>{hhmm(b.placed_at||b.created_at)}</td><td>{SPORT_ICON[b.sport]||'🎯'} {b.fixture}</td><td>{b.selection}</td><td>{b.decimal_odds.toFixed(2)}</td><td><span className={'sb-status '+b.status.toLowerCase()} title={b.reason||''}>{b.status==='PLACED'?'Pending result':b.status.charAt(0)+b.status.slice(1).toLowerCase()}</span></td><td className={b.profit>0?'pos':b.profit<0?'neg':''}>{b.profit===null?'—':naira(b.profit,true)}</td></tr>)}</tbody></table></div></>}
        </section>
        <LiveOrders live={data.live}/>
        {f&&<section className="sb-panel"><div className="sb-panel-head"><h3><Target size={18} className="violet"/>Current Selection Detail (In Focus)</h3></div>
          <div className="sb-focus"><div className="sb-focus-match"><span className="sb-focus-icon">{SPORT_ICON[f.sport]||'🎯'}</span><div><b>{f.fixture}</b><small>{f.competition} · kickoff {hhmm(f.starts_at)} WAT</small></div></div>
            <div><small>Selection</small><b>{f.selection}</b></div><div><small>Odds</small><b>{f.decimal_odds.toFixed(2)}</b></div><div><small>Stake</small><b>{naira(f.stake)}</b></div><div><small>EV</small><b className="pos">{f.expected_value>=0?'+':''}{f.expected_value.toFixed(3)}</b></div>
            <div className="sb-focus-prob"><small>AI Probability</small><b>{pct(f.probability)}</b><div className="sb-progress"><b style={{width:`${f.probability*100}%`}}/></div></div></div></section>}
      </div>
      <div className="sb-engine-mid sb-a-mid">
        <AccountPanel live={data.live} refresh={refresh}/>
        <section className="sb-panel"><div className="sb-panel-head"><h3><Settings2 size={18} className="violet"/>Automation Controls</h3></div><ul className="sb-checks">
          {[['Auto-select (from pipeline)',a.auto_select],['Auto-place paper bet (₦100)',a.auto_place_paper],['Auto-monitor & settle',a.auto_settle],['Auto-learn (shared Learn stage)',a.auto_learn],['Auto-capture booking code',a.booking_code_capture],['Real-money submission',a.real_money]].map(([l,v])=><Check key={l} ok={v} value={v?'ON':'OFF'} tone={v?'pos':'muted'}>{l}</Check>)}
        </ul></section>
        <section className="sb-panel"><div className="sb-panel-head"><h3><ShieldCheck size={18} className="violet"/>Execution Rules (Active)</h3></div><ul className="sb-checks">
          {['Single selection per bet',`${naira(s.stake)} fixed stake`,`${s.daily_target[0]} – ${s.daily_target[1]} bets per day (Max ${s.daily_limit})`,`${naira(s.daily_stake_limit)} daily stake cap`,'One single per fixture (correlation check)',`Fresh SportyBet price, EV ≥ +${s.min_ev.toFixed(2)}`,'Only QUALIFIED opportunities','No human input · paper only'].map(r=><Check key={r} ok>{r}</Check>)}
        </ul></section>
      </div>
      <aside className="sb-engine-side sb-a-log">
        <section className="sb-panel sb-log"><div className="sb-panel-head"><h3><ListChecks size={18} className="violet"/>Live Activity Log</h3><button className="sb-link" onClick={refresh}>Refresh</button></div>
          <ul>{data.events.length?data.events.map((e,i)=><li key={i}><span className={'sb-check '+(e.action==='CANCELLED'?'off':e.action==='PAUSED'?'idle':'ok')}>{e.action==='CANCELLED'?<XCircle size={15}/>:<CheckCircle2 size={15}/>}</span><time>{hhmm(e.created_at)}</time><span>{e.detail}</span></li>):<li className="sb-muted">No engine activity recorded yet.</li>}</ul></section>
        {data.last_cycle?.rejected&&Object.keys(data.last_cycle.rejected).length>0&&<section className="sb-panel"><div className="sb-panel-head"><h3><Search size={18} className="violet"/>Last Cycle Filters</h3></div><ul className="sb-checks">{Object.entries(data.last_cycle.rejected).sort((x,y)=>y[1]-x[1]).map(([k,v])=><li key={k}><span className="sb-check idle"><Activity size={13}/></span><span>{k.replace(/_/g,' ').toLowerCase()}</span><b>{v}</b></li>)}</ul></section>}
      </aside>
    </div>
    </>}
  </div>}
