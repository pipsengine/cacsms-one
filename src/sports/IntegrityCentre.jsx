import React,{useMemo,useState}from'react';
import{AlertCircle,RefreshCw,ShieldAlert,ShieldCheck,X}from'lucide-react';
import{get}from'./api';
import useLiveResource from './useLiveResource';
import'./intelligence.css';
import'./universal.css';

const pct=v=>v===null||v===undefined||!Number.isFinite(Number(v))?'—':`${(Number(v)*100).toFixed(1)}%`;
const signed=v=>v===null||v===undefined||!Number.isFinite(Number(v))?'—':`${Number(v)>=0?'+':''}${(Number(v)*100).toFixed(2)}%`;
const num=(v,d=3)=>v===null||v===undefined||!Number.isFinite(Number(v))?'—':Number(v).toFixed(d);
const when=v=>{if(!v)return'—';const d=new Date(v);return Number.isNaN(d.getTime())?v:d.toLocaleString()};
const label=v=>String(v||'').replaceAll('_',' ').toLowerCase();
const LEVEL_KIND={ELIGIBLE:'success',RESEARCH:'warning',DISCOVERY:'neutral'};
function Pill({kind='neutral',children}){return <span className={`sp-status ${kind}`}>{children}</span>}
function Kpi({title,value,tone='unavailable',note}){return <div className={`sp-kpi ${tone}`}><span>{title}</span><strong>{value??'—'}</strong>{note&&<small className="sp-intel-subline">{note}</small>}</div>}
function ErrorLine({error}){return error?<p className="sp-ops-error" role="alert"><AlertCircle size={16}/>{error}</p>:null}

/* Reliability diagram: mean predicted probability (x) vs observed win rate (y). One series + y=x reference. */
function ReliabilityChart({bins}){
  const[hover,setHover]=useState(null);
  if(!bins?.length)return <p className="sp-muted">No settled forward predictions yet, so there is nothing to calibrate against.</p>;
  const W=320,H=240,P={l:40,r:12,t:12,b:34},x=v=>P.l+v*(W-P.l-P.r),y=v=>H-P.b-v*(H-P.t-P.b);
  const max=Math.max(...bins.map(b=>b.n));
  return <div className="sp-uni-chart">
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Reliability diagram: observed win rate against predicted probability">
      {[0,.25,.5,.75,1].map(t=><g key={t}><line x1={x(0)} x2={x(1)} y1={y(t)} y2={y(t)} className="sp-uni-grid"/><text x={P.l-6} y={y(t)+3} textAnchor="end" className="sp-uni-axis">{t*100}%</text><text x={x(t)} y={H-P.b+14} textAnchor="middle" className="sp-uni-axis">{t*100}%</text></g>)}
      <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(1)} className="sp-uni-ref"/>
      <text x={x(.5)} y={H-4} textAnchor="middle" className="sp-uni-axis">Predicted probability</text>
      {bins.map((b,i)=>{const r=4+6*Math.sqrt(b.n/max);return <g key={b.bin} onMouseEnter={()=>setHover(i)} onMouseLeave={()=>setHover(null)} onFocus={()=>setHover(i)} onBlur={()=>setHover(null)} tabIndex="0">
        <circle cx={x(b.mean_probability)} cy={y(b.observed_rate)} r={Math.max(r,10)} fill="transparent"/>
        <circle cx={x(b.mean_probability)} cy={y(b.observed_rate)} r={r} className="sp-uni-dot"/>
      </g>})}
    </svg>
    {hover!==null&&<div className="sp-uni-tip" role="status"><strong>{bins[hover].bin}</strong><span>Predicted {pct(bins[hover].mean_probability)}</span><span>Observed {pct(bins[hover].observed_rate)}</span><span>{bins[hover].n} selections</span></div>}
    <p className="sp-intel-fine">Dots on the dashed diagonal are well calibrated; dots below it mean the model was overconfident. Dot size shows how many selections fall in the bin.</p>
  </div>;
}

function CapabilityDrawer({item,onClose}){
  if(!item)return null;
  return <section className="sp-panel sp-uni-analyzer">
    <div className="sp-panel-head"><div><span className="sp-overline">CAPABILITY EVIDENCE</span><h3>{label(item.sport)} · {item.market}{item.competition?` · ${item.competition}`:''}</h3><p>{item.reason}</p></div><div className="sp-intel-actions"><Pill kind={LEVEL_KIND[item.level]}>{item.level}</Pill><button className="sp-secondary" onClick={onClose} aria-label="Close"><X size={15}/></button></div></div>
    <div className="sp-kpi-grid">
      <Kpi title="Settled forward predictions" value={item.observations??'—'} tone="football"/>
      <Kpi title="Calibration error" value={num(item.calibration_error)} tone="prediction"/>
      <Kpi title="Model Brier" value={num(item.model_brier,4)} note={`Bookmaker ${num(item.market_brier,4)}`} tone="value"/>
      <Kpi title="Improvement z" value={num(item.improvement_z,2)} note={item.z_required?`needs ≥ ${num(item.z_required,2)}`:'league check'} tone="ticket"/>
      <Kpi title="Longshot bias (odds ≥ 3)" value={item.longshot_bias==null?'—':signed(item.longshot_bias)} note={`${item.longshot_observations??0} selections`}/>
      <Kpi title="Closing-line value" value={item.clv?.mean==null?'—':signed(item.clv.mean)} note={`${item.clv?.n??0} closes`}/>
    </div>
    <div className="sp-grid two"><div><h3>Reliability</h3><ReliabilityChart bins={item.reliability_bins}/></div>
      <div><h3>Reliability table</h3><div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>Bin</th><th>Selections</th><th>Predicted</th><th>Observed</th></tr></thead><tbody>
        {(item.reliability_bins||[]).map(b=><tr key={b.bin}><td>{b.bin}</td><td>{b.n}</td><td>{pct(b.mean_probability)}</td><td>{pct(b.observed_rate)}</td></tr>)}
        {!item.reliability_bins?.length&&<tr><td colSpan="4" className="sp-empty">No settled bins.</td></tr>}
      </tbody></table></div></div></div>
  </section>;
}

export function IntegrityEligibility(){
  const{data,error,loading,refresh}=useLiveResource('/integrity/report',60000);
  const[query,setQuery]=useState(''),[level,setLevel]=useState(''),[open,setOpen]=useState(null);
  const rows=useMemo(()=>(data?.capabilities||[]).filter(c=>(!level||c.level===level)&&(!query||`${c.sport} ${c.market} ${c.competition}`.toLowerCase().includes(query.toLowerCase()))),[data,query,level]);
  const refreshInfo=data?.capability_refresh;
  return <div className="sp-intelligence-stack">
    <section className="sp-panel">
      <div className="sp-panel-head"><div><h3>Model eligibility</h3><p>Discovery is universal. Paper trading (RESEARCH) runs on every modelled market. Only ELIGIBLE sport–league–markets can produce booking selections: enough settled forward predictions, low calibration error, a Brier improvement over the bookmaker that survives a Bonferroni correction across every combination tested, no longshot overconfidence, and positive closing-line value.</p></div><button className="sp-secondary" onClick={refresh} disabled={loading}><RefreshCw size={15}/>Refresh</button></div>
      {data&&<div className="sp-kpi-grid">
        <Kpi title="Eligible" value={data.levels.ELIGIBLE} tone="value"/>
        <Kpi title="Research (paper trading)" value={data.levels.RESEARCH} tone="ticket"/>
        <Kpi title="Open paper trades" value={data.paper_trades_open} tone="prediction"/>
        <Kpi title="Quarantined (upcoming)" value={data.quarantined.length}/>
        <Kpi title="Probability records" value={data.probability_records} tone="football"/>
        <Kpi title="Last evaluation" value={refreshInfo?when(refreshInfo.evaluated_at).split(',')[0]:'Not yet'} note={refreshInfo?`${refreshInfo.pairs} settled · z ≥ ${num(refreshInfo.z_required,2)}`:'Runs every few hours'}/>
      </div>}
      <ErrorLine error={error}/>
      <div className="sp-intel-filters"><label>Search<input value={query} onChange={e=>setQuery(e.target.value)} placeholder="Sport, market or league"/></label><label>Level<select value={level} onChange={e=>setLevel(e.target.value)}><option value="">All levels</option>{['ELIGIBLE','RESEARCH','DISCOVERY'].map(l=><option key={l}>{l}</option>)}</select></label></div>
      <div className="sp-table-scroll"><table className="sp-table"><thead><tr>{['Sport · market','League','Level','Settled','Calibration','Model vs bookmaker Brier','z','Longshot bias','CLV','Why'].map(h=><th key={h}>{h}</th>)}</tr></thead><tbody>
        {rows.map(c=><tr key={`${c.sport}|${c.competition}|${c.market}`} className="sp-uni-row" onClick={()=>setOpen(c)}>
          <td><strong>{label(c.sport)}</strong><small className="sp-intel-subline">{c.market}</small></td>
          <td>{c.competition||'All leagues'}</td>
          <td><Pill kind={LEVEL_KIND[c.level]}>{c.level}</Pill></td>
          <td>{c.observations??'—'}</td>
          <td>{num(c.calibration_error)}</td>
          <td>{num(c.model_brier,4)} vs {num(c.market_brier,4)}</td>
          <td>{num(c.improvement_z,2)}</td>
          <td>{c.longshot_bias==null?'—':signed(c.longshot_bias)}</td>
          <td>{c.clv?.mean==null?'—':`${signed(c.clv.mean)} (n=${c.clv.n})`}</td>
          <td className="sp-uni-rule">{c.reason}</td>
        </tr>)}
        {!loading&&!rows.length&&<tr><td className="sp-empty" colSpan="10">No capability has been evaluated yet. The worker evaluates settled forward predictions every few hours.</td></tr>}
      </tbody></table></div>
    </section>
    <CapabilityDrawer item={open} onClose={()=>setOpen(null)}/>
    <section className="sp-panel">
      <div className="sp-panel-head"><div><h3>Quarantine</h3><p>Selections with an unresolved integrity issue: missing time zone, data after kickoff, a rebuilt feature snapshot, a mismatched market or settlement rule, or an unreconciled duplicate fixture.</p></div><ShieldAlert size={20}/></div>
      {data&&Object.keys(data.quarantine_by_issue).length>0&&<div className="sp-intel-metrics">{Object.entries(data.quarantine_by_issue).map(([k,v])=><span key={k}>{label(k)} <strong>{v}</strong></span>)}</div>}
      <div className="sp-table-scroll"><table className="sp-table"><thead><tr>{['Fixture','Sport · market','Kick-off','Issues'].map(h=><th key={h}>{h}</th>)}</tr></thead><tbody>
        {(data?.quarantined||[]).map((q,i)=><tr key={i}><td><strong>{q.home} vs {q.away}</strong><small className="sp-intel-subline">{q.competition}</small></td><td>{label(q.sport)} · {q.market}</td><td>{when(q.starts_at)}</td><td className="sp-uni-rule">{q.issues.map(x=>x.detail).join(' ')}</td></tr>)}
        {data&&!data.quarantined.length&&<tr><td className="sp-empty" colSpan="4">No upcoming selection is quarantined.</td></tr>}
      </tbody></table></div>
    </section>
    <section className="sp-panel">
      <div className="sp-panel-head"><div><h3>Why selections did not qualify</h3><p>Upcoming decisions by state and reason. Zero qualified selections is a valid outcome.</p></div></div>
      <div className="sp-intel-metrics">{(data?.decision_reasons||[]).map(r=><span key={r.state+r.reason_code}>{label(r.state)} · {label(r.reason_code)} <strong>{r.total}</strong></span>)}</div>
      {data&&<p className="sp-intel-fine">Safety margin {pct(data.policy.safety_margin)} · transaction cost {pct(data.policy.transaction_cost)} · minimum {data.policy.min_observations} settled predictions · calibration ≤ {data.policy.max_calibration_error} · CLV {data.policy.require_clv?`required (${data.policy.min_clv_observations} closes)`:'not required'}.</p>}
    </section>
  </div>;
}

function GroupTable({title,rows}){
  return <section className="sp-panel"><h3>{title}</h3><div className="sp-table-scroll"><table className="sp-table"><thead><tr>{['Group','Closes','Mean CLV','95% interval','Beat close','Settled','Profit (units)','Yield','Max drawdown','Advantage'].map(h=><th key={h}>{h}</th>)}</tr></thead><tbody>
    {rows.map(g=><tr key={g.key}><td><strong>{g.key}</strong></td><td>{g.observations}</td><td>{signed(g.mean_clv)}</td><td>{g.ci_low==null?'—':`${signed(g.ci_low)} to ${signed(g.ci_high)}`}</td><td>{pct(g.positive_share)}</td><td>{g.settled}</td><td>{num(g.profit_units,2)}</td><td>{g.yield==null?'—':signed(g.yield)}</td><td>{num(g.max_drawdown_units,2)}</td>
      <td>{g.persistent_advantage?<Pill kind="success"><ShieldCheck size={12}/>Persistent</Pill>:<Pill>Not shown</Pill>}</td></tr>)}
    {!rows.length&&<tr><td className="sp-empty" colSpan="10">No closed selections yet.</td></tr>}
  </tbody></table></div></section>;
}

const DIMENSIONS=[['by_sport','Sport'],['by_league','League'],['by_market','Market'],['by_odds_band','Odds band'],['by_bookmaker','Bookmaker'],['by_model_version','Model version'],['by_time_before_kickoff','Time before kickoff']];

export function ClvDashboard(){
  const[filters,setFilters]=useState({sport:'',kind:'',market:'',competition:''}),[dimension,setDimension]=useState('by_sport');
  const query=new URLSearchParams(Object.entries(filters).filter(([,v])=>v)).toString();
  const{data,error,loading,refresh}=useLiveResource(`/integrity/clv${query?`?${query}`:''}`,60000);
  const o=data?.overall;
  return <div className="sp-intelligence-stack">
    <section className="sp-panel">
      <div className="sp-panel-head"><div><h3>Closing-line value</h3><p>{data?.note||'Price taken against the last pre-kickoff price, per selection.'} Paper trades, qualified selections and booked selections are tracked separately.</p></div><button className="sp-secondary" onClick={refresh} disabled={loading}><RefreshCw size={15}/>Refresh</button></div>
      <div className="sp-intel-filters">
        <label>Sport<input value={filters.sport} onChange={e=>setFilters({...filters,sport:e.target.value})} placeholder="e.g. football"/></label>
        <label>Market<input value={filters.market} onChange={e=>setFilters({...filters,market:e.target.value})} placeholder="e.g. HOME"/></label>
        <label>League<input value={filters.competition} onChange={e=>setFilters({...filters,competition:e.target.value})} placeholder="Contains…"/></label>
        <label>Track<select value={filters.kind} onChange={e=>setFilters({...filters,kind:e.target.value})}><option value="">All</option><option value="paper">Paper</option><option value="qualified">Qualified</option><option value="booked">Booked</option></select></label>
      </div>
      <ErrorLine error={error}/>
      {o&&<div className="sp-kpi-grid">
        <Kpi title="Closed selections" value={o.observations} tone="football" note={`${data.pipeline.open} open · ${data.pipeline.no_valid_close} without a valid close`}/>
        <Kpi title="Mean CLV" value={signed(o.mean_clv)} tone="value" note={o.ci_low==null?'Interval needs 2+ closes':`95%: ${signed(o.ci_low)} to ${signed(o.ci_high)}`}/>
        <Kpi title="Beat the close" value={pct(o.positive_share)} tone="prediction"/>
        <Kpi title="Margin-free CLV EV" value={signed(o.mean_fair_clv_ev)} tone="ticket"/>
        <Kpi title="Settled profit (units)" value={num(o.profit_units,2)} note={`yield ${o.yield==null?'—':signed(o.yield)} · drawdown ${num(o.max_drawdown_units,2)}`}/>
        <Kpi title="Persistent advantage" value={o.persistent_advantage?'Shown':'Not shown'} note={`needs ${data.minimum_observations} closes with a 95% lower bound above zero`}/>
      </div>}
    </section>
    {data&&<GroupTable title="Paper vs qualified vs booked" rows={Object.entries(data.by_kind).map(([k,v])=>({key:k,...v}))}/>}
    <section className="sp-panel"><div className="sp-tabs" role="tablist">{DIMENSIONS.map(([k,t])=><button key={k} role="tab" aria-selected={dimension===k} className={dimension===k?'selected':''} onClick={()=>setDimension(k)}>{t}</button>)}</div></section>
    {data&&<GroupTable title={`By ${DIMENSIONS.find(d=>d[0]===dimension)[1].toLowerCase()}`} rows={data[dimension]||[]}/>}
    <section className="sp-panel"><h3>Recent selections</h3><div className="sp-table-scroll"><table className="sp-table"><thead><tr>{['Kick-off','Sport · market','League','Track','Taken','Close','CLV','Status','Result'].map(h=><th key={h}>{h}</th>)}</tr></thead><tbody>
      {(data?.recent||[]).map((r,i)=><tr key={i}><td>{when(r.starts_at)}</td><td>{label(r.sport)} · {r.market}</td><td>{r.competition||'—'}</td><td>{r.kind}</td><td>{num(r.taken_odds,2)}</td><td>{num(r.closing_odds,2)}</td><td>{signed(r.clv_price)}</td><td>{label(r.status)}</td><td>{r.settled_outcome?`${r.settled_outcome} (${num(r.profit,2)})`:'—'}</td></tr>)}
      {data&&!data.recent.length&&<tr><td className="sp-empty" colSpan="9">No selection has been tracked yet. Paper trades start automatically once a modelled market passes every selection gate.</td></tr>}
    </tbody></table></div></section>
  </div>;
}

export function ProbabilityRecord({id,onClose}){
  const[data,setData]=useState(null),[error,setError]=useState('');
  React.useEffect(()=>{if(!id)return;setData(null);get(`/integrity/records/${encodeURIComponent(id)}`).then(setData).catch(e=>setError(e.message))},[id]);
  if(!id)return null;
  return <section className="sp-panel sp-uni-analyzer"><div className="sp-panel-head"><div><span className="sp-overline">IMMUTABLE PROBABILITY RECORD</span><h3>{id}</h3><p>Every page and booking job referencing this id uses exactly these values.</p></div><button className="sp-secondary" onClick={onClose} aria-label="Close"><X size={15}/></button></div>
    <ErrorLine error={error}/>
    {data&&<><div className="sp-kpi-grid">
      <Kpi title="Model probability" value={pct(data.model_probability)} tone="prediction"/>
      <Kpi title="Blended probability" value={pct(data.blended_probability)} tone="football"/>
      <Kpi title="Conservative probability" value={pct(data.conservative_probability)} note={`margin ${pct(data.uncertainty_margin)}`}/>
      <Kpi title="Odds · break-even" value={`${num(data.decimal_odds,2)} · ${pct(data.break_even)}`} tone="ticket"/>
      <Kpi title="Point EV" value={signed(data.point_ev)} tone="value"/>
      <Kpi title="Conservative EV" value={signed(data.conservative_ev)} tone="value"/>
    </div>
    <p className="sp-intel-fine">Model {data.model_version||'—'} · prediction {when(data.prediction_created_at)} · price {data.bookmaker} at {when(data.odds_observed_at)} · consensus {data.consensus_probability==null?`single source (${data.consensus_sources} independent)`:pct(data.consensus_probability)} · capability {data.capability_level} · integrity {data.integrity_status}{data.integrity_issues.length?`: ${data.integrity_issues.map(i=>i.code).join(', ')}`:''} · policy v{data.policy_version}</p>
    <div className="sp-table-scroll"><table className="sp-table"><thead><tr>{['Version','Odds','Probability','Conservative EV','Capability','Integrity'].map(h=><th key={h}>{h}</th>)}</tr></thead><tbody>
      {data.lineage.map(r=><tr key={r.id}><td>{when(r.created_at)}{r.id===data.id?' (this)':''}</td><td>{num(r.decimal_odds,2)}</td><td>{pct(r.blended_probability)}</td><td>{signed(r.conservative_ev)}</td><td>{r.capability_level}</td><td>{r.integrity_status}</td></tr>)}
    </tbody></table></div></>}
  </section>;
}
