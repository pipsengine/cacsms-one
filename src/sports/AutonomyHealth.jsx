import React,{useState} from 'react';
import {AlertCircle,CheckCircle2,Cpu,RefreshCw,ShieldCheck,ShieldX,Wrench,BrainCircuit,Ticket} from 'lucide-react';
import useLiveResource from './useLiveResource';
import {post} from './api';
import './autonomy-health.css';

const stamp=v=>{if(!v)return '—';const d=new Date(v);return Number.isNaN(d.getTime())?String(v):d.toLocaleString('en-GB',{timeZone:'Africa/Lagos',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'})+' WAT'};
const num=(v,d=3)=>v==null?'—':Number(v).toFixed(d);
const pct=v=>v==null?'—':`${(Number(v)*100).toFixed(1)}%`;
const human=v=>String(v||'—').replaceAll('_',' ');
const TONE={escalated:'danger',retried:'warning',open:'warning',resolved:'success'};

function Pill({tone='neutral',children}){return <span className={`ah-pill is-${tone}`}>{children}</span>}
function Kpi({icon:Icon,label,value,note,tone='blue'}){return <article className="ah-kpi"><span className={`ah-kpi-icon is-${tone}`}><Icon size={20}/></span><div><strong>{value}</strong><span>{label}</span>{note&&<small>{note}</small>}</div></article>}
function Table({headers,rows,empty}){return rows.length?<div className="sp-table-scroll"><table className="sp-table"><thead><tr>{headers.map(h=><th key={h}>{h}</th>)}</tr></thead><tbody>{rows.map((r,i)=><tr key={i}>{r.map((c,j)=><td key={j}>{c}</td>)}</tr>)}</tbody></table></div>:<p className="ah-empty">{empty}</p>}

function Overview({data}){
 const slots=data.slots||[],busy=slots.filter(s=>s.busy).length;
 const open=(data.recovery||[]).filter(c=>c.status!=='resolved');
 const failing=(data.stages||[]).filter(s=>s.failed>0);
 const b=data.bookings||{};
 return <>
  <div className="ah-kpis">
   <Kpi icon={Cpu} label="Browser slots in use" value={`${busy} / ${slots.length}`} note="Shared by API, worker and bookings" tone={busy===slots.length?'orange':'blue'}/>
   <Kpi icon={Wrench} label="Open recovery cases" value={open.length} note={`${open.filter(c=>c.status==='escalated').length} escalated`} tone={open.length?'red':'green'}/>
   <Kpi icon={Ticket} label="Booking success (7d)" value={pct(b.success_rate)} note={`${b.codes||0} codes from ${b.finished||0} finished jobs`} tone="green"/>
   <Kpi icon={BrainCircuit} label="Prices awaiting a prediction" value={Number(data.learning?.awaiting_model||0).toLocaleString()} note="Re-assessed each worker cycle" tone="violet"/>
  </div>
  <section className="sp-panel"><div className="sp-panel-head"><div><h3>Browser slots</h3><p>Every Chromium launch holds a slot. A crashed holder's lease expires on its own.</p></div></div>
   <Table headers={['Slot','State','Purpose','Since','Lease expires']} empty="No browser slots are configured." rows={slots.map(s=>[s.slot,<Pill tone={s.busy?'warning':'success'}>{s.busy?'In use':'Free'}</Pill>,s.busy?human(s.purpose):'—',s.busy?stamp(s.acquired_at):'—',s.busy?stamp(s.expires_at):'—'])}/>
  </section>
  <section className="sp-panel"><div className="sp-panel-head"><div><h3>Worker stages · last 24 hours</h3><p>Failed runs keep their error so the cause is visible here.</p></div></div>
   <Table headers={['Stage','Last status','Runs','Failed','Last error','Last run']} empty="No worker stages ran in the last 24 hours." rows={(data.stages||[]).map(s=>[human(s.stage),<Pill tone={s.last_status==='failed'?'danger':'neutral'}>{human(s.last_status)}</Pill>,s.runs,s.failed?<b className="ah-bad">{s.failed}</b>:0,s.last_error?<code className="ah-code">{s.last_error}</code>:'—',stamp(s.last_at)])}/>
  </section>
  {data.booking_policy&&<section className="sp-panel"><div className="sp-panel-head"><div><h3>Booking policy</h3><p>What may stop a code request. No wager is placed, started matches are never booked and access challenges always stop a run.</p></div></div>
   <Table headers={['Rule','Setting']} empty="" rows={[
    ['Require a live QUALIFIED decision on every leg',data.booking_policy.require_live_qualification?'Yes':'No'],
    ['Book at the price SportyBet displays',data.booking_policy.accept_displayed_odds?'Yes — changes are stored on the ticket':'No — a changed price stops the booking'],
    ['Drop legs SportyBet does not offer or that kicked off',data.booking_policy.drop_unavailable_legs?`Yes, keeping at least ${data.booking_policy.min_legs}`:'No'],
    ['Block tickets that share a match',data.booking_policy.enforce_match_exposure?'Yes':'No'],
    ['Lowest leg price',data.booking_policy.min_leg_odds],
    ['Gap between code requests',`${data.booking_policy.request_gap_minutes} min`],
   ]}/>
  </section>}
  <section className="sp-panel"><div className="sp-panel-head"><div><h3>Booking outcomes · last 7 days</h3><p>{failing.length?`${failing.length} stage(s) failed in the last day.`:'Codes are stored only when SportyBet displayed them.'}</p></div></div>
   <Table headers={['Outcome','Status','Jobs']} empty="No booking jobs finished in the last 7 days." rows={(b.outcomes||[]).map(o=>[human(o.reason),<Pill tone={o.status==='CODE_GENERATED'?'success':o.status==='FAILED'?'danger':'warning'}>{human(o.status)}</Pill>,o.n])}/>
  </section>
 </>;
}

function Recovery({cases}){
 return <section className="sp-panel"><div className="sp-panel-head"><div><h3>Recovery cases</h3><p>One case per failing stage. It escalates after five failures in a row and resolves on the next successful run.</p></div></div>
  {cases.length?<div className="ah-cases">{cases.map(c=><article key={c.id} className={`ah-case is-${TONE[c.status]||'neutral'}`}>
   <header><b>{human(c.stage)}</b><Pill tone={TONE[c.status]}>{c.status}</Pill><span>{c.failures} failure{c.failures===1?'':'s'}</span></header>
   {c.error&&<code className="ah-code">{c.error_type?`${c.error_type}: `:''}{c.error}</code>}
   {c.where?.length>0&&<small>Where: {c.where.join(' → ')}</small>}
   <small>Opened {stamp(c.opened_at)} · updated {stamp(c.updated_at)}{c.resolved_by?` · resolved by ${c.resolved_by}`:''}{c.error_class?` · ${c.error_class}`:''}</small>
  </article>)}</div>:<p className="ah-empty"><CheckCircle2 size={16}/> No stage has failed since recovery tracking started.</p>}
 </section>;
}

function Sources({sources,reload}){
 const [form,setForm]=useState({host:'',terms_url:'',approved:true,notes:''}),[key,setKey]=useState(''),[note,setNote]=useState(''),[busy,setBusy]=useState(false);
 async function submit(e){e.preventDefault();setBusy(true);setNote('');try{const saved=await post('/autonomy/sources/review',form,key?{'X-Sports-Admin-Key':key}:{});setNote(`${saved.hostname} recorded as ${saved.approved?'approved':'not approved'}.`);reload()}catch(err){setNote(err.message)}finally{setBusy(false)}}
 return <>
  <section className="sp-panel"><div className="sp-panel-head"><div><h3>Source permissions</h3><p>A collector opens a page only when the host is approved and its robots.txt allows the path. Hosts are never approved automatically.</p></div></div>
   <Table headers={['Host','Terms','Approved by','robots.txt','Allowed (24h)','Denied (24h)','Last check']} empty="No source has been checked yet." rows={(sources.hosts||[]).map(h=>[<b>{h.host}</b>,h.approved?<Pill tone="success"><ShieldCheck size={11}/>Approved</Pill>:<Pill tone="danger"><ShieldX size={11}/>Not approved</Pill>,h.approved_by||'—',human(h.robots_status),h.checks_allowed,h.checks_denied?<b className="ah-bad">{h.checks_denied}</b>:0,stamp(h.last_check)])}/>
  </section>
  <section className="sp-panel"><div className="sp-panel-head"><div><h3>Recent denials</h3></div></div>
   <Table headers={['Host','URL','Purpose','Reason','When']} empty="No request has been denied." rows={(sources.recent_denials||[]).map(d=>[d.host,<code className="ah-code">{d.url}</code>,human(d.purpose),d.reason,stamp(d.created_at)])}/>
  </section>
  <section className="sp-panel"><div className="sp-panel-head"><div><h3>Record a terms review</h3><p>Requires the admin key. The decision and reviewer are written to the audit log.</p></div></div>
   <form className="ah-form" onSubmit={submit}>
    <label>Host<input required value={form.host} onChange={e=>setForm({...form,host:e.target.value})} placeholder="stats.example.com"/></label>
    <label>Terms URL<input required type="url" pattern="https://.*" value={form.terms_url} onChange={e=>setForm({...form,terms_url:e.target.value})} placeholder="https://stats.example.com/terms"/></label>
    <label>Decision<select value={form.approved?'1':'0'} onChange={e=>setForm({...form,approved:e.target.value==='1'})}><option value="1">Approved for automated collection</option><option value="0">Not approved</option></select></label>
    <label>Admin key<input type="password" value={key} onChange={e=>setKey(e.target.value)} autoComplete="off"/></label>
    <label className="ah-wide">Notes<input value={form.notes} onChange={e=>setForm({...form,notes:e.target.value})} placeholder="What the terms allow, and any limits"/></label>
    <div className="ah-wide ah-form-actions"><button className="sp-primary" disabled={busy}>{busy?'Saving…':'Save review'}</button>{note&&<span role="status">{note}</span>}</div>
   </form>
  </section>
 </>;
}

function Learning({learning}){
 return <>
  <section className="sp-panel"><div className="sp-panel-head"><div><h3>Prospective model accuracy</h3><p>Scored only on predictions stored before kickoff and settled after. Lower Brier score, log loss and calibration error are better.</p></div></div>
   <Table headers={['Sport','Market','Model','Samples','Brier','Log loss','Calibration error','Hit rate','Scored']} empty="No prospective evaluation has been stored yet." rows={(learning.evaluations||[]).map(e=>[human(e.sport),e.market,e.version?`v${e.version}${e.active?' · active':''}`:'—',e.sample_size,num(e.brier_score),num(e.log_loss),<span className={e.calibration_error>0.1?'ah-bad':''}>{num(e.calibration_error)}</span>,pct(e.hit_rate),stamp(e.created_at)])}/>
  </section>
  <section className="sp-panel"><div className="sp-panel-head"><div><h3>Drift events · last 7 days</h3><p>Drift trains a candidate once per sport per day. The candidate stays inactive unless holdout governance activates it.</p></div></div>
   <Table headers={['Sport','Market','Type','Value','Threshold','When']} empty="No drift was detected in the last 7 days." rows={(learning.drift||[]).map(d=>[human(d.sport),d.market,human(d.drift_type),num(d.metric_value),num(d.threshold,2),stamp(d.created_at)])}/>
  </section>
  <section className="sp-panel"><div className="sp-panel-head"><div><h3>Training runs</h3><p>Walk-forward training with a chronological holdout.</p></div></div>
   <Table headers={['Sport','Version','Status','Samples','Holdout','Calibration error','Created']} empty="No training run is stored." rows={(learning.training_runs||[]).map(r=>[human(r.sport),r.version,<Pill tone={r.status==='validated'?'success':'warning'}>{human(r.status)}</Pill>,r.sample_size,r.holdout_fixtures??'—',num(r.calibration_error),stamp(r.created_at)])}/>
  </section>
 </>;
}

export default function AutonomyHealth({tab=0}){
 const {data,error,loading,refresh}=useLiveResource('/autonomy/overview',20000);
 if(loading&&!data)return <div className="sp-emptybox">Loading autonomy health…</div>;
 if(!data)return <p className="sp-ops-error"><AlertCircle size={16}/>{error||'The autonomy service did not respond.'}</p>;
 return <div className="ah-stack">
  <div className="ah-bar"><span>Updated {stamp(data.generated_at)} · refreshes every 20 seconds · no wager is ever placed</span><button className="sp-secondary" onClick={refresh}><RefreshCw size={14}/>Refresh</button></div>
  {error&&<p className="sp-ops-error"><AlertCircle size={16}/>{error}</p>}
  {tab===0&&<Overview data={data}/>}
  {tab===1&&<Recovery cases={data.recovery||[]}/>}
  {tab===2&&<Sources sources={data.sources||{}} reload={refresh}/>}
  {tab===3&&<Learning learning={data.learning||{}}/>}
 </div>;
}
