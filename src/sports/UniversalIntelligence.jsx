import React,{useEffect,useMemo,useState}from'react';
import{AlertCircle,ExternalLink,RefreshCw,ShieldCheck,X}from'lucide-react';
import{get}from'./api';
import{useLiveReload}from'./live';
import useLiveResource from './useLiveResource';
import{ClvDashboard,IntegrityEligibility,ProbabilityRecord}from'./IntegrityCentre';
import'./intelligence.css';
import'./universal.css';

const pct=v=>v===null||v===undefined||!Number.isFinite(Number(v))?'—':`${(Number(v)*100).toFixed(1)}%`;
const signed=v=>v===null||v===undefined||!Number.isFinite(Number(v))?'—':`${Number(v)>=0?'+':''}${(Number(v)*100).toFixed(1)}%`;
const odds=v=>v===null||v===undefined||!Number.isFinite(Number(v))?'No price':Number(v).toFixed(2);
const when=v=>{if(!v)return'—';const d=new Date(v);return Number.isNaN(d.getTime())?v:d.toLocaleString()};
const label=v=>String(v||'').replaceAll('_',' ').toLowerCase();
const STATE_KIND={QUALIFIED:'success',MONITORING:'warning',AWAITING_DATA:'neutral',SUSPENDED:'warning',REJECTED:'danger',EXPIRED:'neutral'};
const AVAIL_KIND={available:'success',awaiting_terms_review:'warning',declined_by_review:'danger',approved_robots_unchecked:'warning',approved_robots_unreachable:'warning'};
const CAP_KIND={active:'success',research:'warning',unsupported:'neutral'};

function Pill({kind='neutral',children}){return <span className={`sp-status ${kind}`}>{children}</span>}
function Kpi({title,value,tone='unavailable'}){return <div className={`sp-kpi ${tone}`}><span>{title}</span><strong>{value??'—'}</strong></div>}
function ErrorLine({error}){return error?<p className="sp-ops-error" role="alert"><AlertCircle size={16}/>{error}</p>:null}

const EMPTY={sport:'',country:'',competition:'',market:'',state:'QUALIFIED',odds_min:'',odds_max:'',min_probability:'',date_from:'',date_to:'',robust_only:false};

function Filters({filters,setFilters,facets}){
  const set=(k,v)=>setFilters({...filters,[k]:v});
  return <div className="sp-intel-filters">
    <label>Sport<select value={filters.sport} onChange={e=>set('sport',e.target.value)}><option value="">All sports</option>{(facets?.sports||[]).map(s=><option key={s} value={s}>{label(s)}</option>)}</select></label>
    <label>Country<select value={filters.country} onChange={e=>set('country',e.target.value)}><option value="">All countries</option>{(facets?.countries||[]).map(s=><option key={s} value={s}>{s}</option>)}</select></label>
    <label>League / competition<input value={filters.competition} onChange={e=>set('competition',e.target.value)} placeholder="Contains…"/></label>
    <label>Market<select value={filters.market} onChange={e=>set('market',e.target.value)}><option value="">All markets</option>{(facets?.markets||[]).map(s=><option key={s} value={s}>{s}</option>)}</select></label>
    <label>Status<select value={filters.state} onChange={e=>set('state',e.target.value)}><option value="">All statuses</option>{(facets?.states||[]).map(s=><option key={s} value={s}>{label(s)}</option>)}</select></label>
    <label>Odds from<input type="number" min="1.01" step="0.05" value={filters.odds_min} onChange={e=>set('odds_min',e.target.value)} placeholder="1.70"/></label>
    <label>Odds to<input type="number" min="1.01" step="0.05" value={filters.odds_max} onChange={e=>set('odds_max',e.target.value)} placeholder="2.50"/></label>
    <label>Min probability<input type="number" min="0" max="1" step="0.05" value={filters.min_probability} onChange={e=>set('min_probability',e.target.value)} placeholder="0.55"/></label>
    <label>From date<input type="date" value={filters.date_from} onChange={e=>set('date_from',e.target.value)}/></label>
    <label>To date<input type="date" value={filters.date_to} onChange={e=>set('date_to',e.target.value)}/></label>
    <label className="sp-uni-check"><input type="checkbox" checked={filters.robust_only} onChange={e=>set('robust_only',e.target.checked)}/>Conservative EV ≥ 0 only</label>
    <div className="sp-uni-presets"><button className="sp-secondary" onClick={()=>setFilters({...filters,odds_min:'1.70',odds_max:'2.50'})}>Research band 1.70–2.50</button><button className="sp-secondary" onClick={()=>setFilters(EMPTY)}>Reset</button></div>
  </div>;
}

function useBoard(filters,view,page){
  const query=useMemo(()=>{const p=new URLSearchParams({view,page:String(page),page_size:'50'});Object.entries(filters).forEach(([k,v])=>{if(v!==''&&v!==false&&v!==null)p.set(k,String(v))});return p.toString()},[filters,view,page]);
  const[data,setData]=useState(null),[error,setError]=useState(''),[loading,setLoading]=useState(true);
  const load=async quiet=>{if(!quiet)setLoading(true);try{setData(await get(`/universal/opportunities?${query}`));setError('')}catch(e){setError(e.message)}finally{setLoading(false)}};
  useEffect(()=>{load()},[query]);
  useLiveReload(()=>load(true));
  return{data,error,loading,reload:()=>load()};
}

function Pager({data,page,setPage}){if(!data||data.total<=data.page_size)return null;const last=Math.ceil(data.total/data.page_size);return <div className="sp-uni-pager"><button className="sp-secondary" disabled={page<=1} onClick={()=>setPage(page-1)}>Previous</button><span>Page {page} of {last} · {data.total} rows</span><button className="sp-secondary" disabled={page>=last} onClick={()=>setPage(page+1)}>Next</button></div>}

function Summary({summary}){if(!summary)return null;return <div className="sp-kpi-grid">
  <Kpi title="Markets evaluated" value={summary.markets} tone="prediction"/>
  <Kpi title="Fixtures" value={summary.fixtures} tone="football"/>
  <Kpi title="Qualified markets" value={summary.qualified_markets} tone="value"/>
  <Kpi title="Robust (conservative EV ≥ 0)" value={summary.robust_qualified} tone="value"/>
  <Kpi title="Qualified in 1.70–2.50" value={summary.preferred_band_qualified} tone="ticket"/>
  <Kpi title="No qualified opportunity" value={summary.fixtures_without_opportunity}/>
</div>}

function OpportunitiesCentre({onOpen,onRecord}){
  const[filters,setFilters]=useState(EMPTY),[page,setPage]=useState(1);
  useEffect(()=>setPage(1),[filters]);
  const{data,error,loading,reload}=useBoard(filters,'markets',page);
  const rows=data?.items||[];
  return <section className="sp-panel">
    <div className="sp-panel-head"><div><h3>Universal Opportunities Centre</h3><p>Every sport and league, ranked by conservative expected value: point EV minus the validated uncertainty margin times the odds. Only ELIGIBLE capabilities with conservative EV above the safety margin qualify; the rest are paper-traded. Only stored model decisions and permitted bookmaker prices appear.</p></div><button className="sp-secondary" onClick={reload} disabled={loading}><RefreshCw size={15}/>{loading?'Loading…':'Refresh'}</button></div>
    <Filters filters={filters} setFilters={setFilters} facets={data?.facets}/>
    <Summary summary={data?.summary}/>
    <ErrorLine error={error}/>
    {data?.summary?.truncated&&<p className="sp-muted">Showing the first 5,000 decisions. Narrow the filters to see the rest.</p>}
    <div className="sp-table-scroll"><table className="sp-table"><thead><tr>
      {['#','Fixture','Sport · Country','Market','Odds','Probability','Market consensus','Break-even','EV','Conservative EV','Eligibility','Quote age','Status','Evidence'].map(h=><th key={h}>{h}</th>)}
    </tr></thead><tbody>
      {rows.map((r,i)=><tr key={r.decision_id} className="sp-uni-row" onClick={()=>onOpen(r.fixture_id)}>
        <td>{(page-1)*50+i+1}</td>
        <td><strong>{r.home} vs {r.away}</strong><small className="sp-intel-subline">{r.competition} · {when(r.starts_at)}</small></td>
        <td>{label(r.sport)}<small className="sp-intel-subline">{r.country||'Country not stored'}</small></td>
        <td>{r.market}{r.preferred_band&&<small className="sp-intel-subline">Research band</small>}</td>
        <td>{odds(r.odds)}</td>
        <td>{pct(r.probability)}{r.model_probability!=null&&r.model_probability!==r.probability&&<small className="sp-intel-subline">model {pct(r.model_probability)}</small>}</td>
        <td>{r.consensus_probability!=null?pct(r.consensus_probability):r.sportybet_fair_probability!=null?<>{pct(r.sportybet_fair_probability)}<small className="sp-intel-subline">SportyBet only</small></>:'—'}</td>
        <td>{pct(r.break_even)}</td>
        <td>{signed(r.expected_value)}</td>
        <td><Pill kind={r.robust?'success':'neutral'}>{signed(r.conservative_ev)}</Pill>{r.ev_source==='estimated_from_holdout'&&<small className="sp-intel-subline">not yet recorded</small>}</td>
        <td>{r.capability_level?<Pill kind={r.capability_level==='ELIGIBLE'?'success':'warning'}>{label(r.capability_level)}</Pill>:'—'}{r.paper_trade&&<small className="sp-intel-subline">paper trade</small>}</td>
        <td>{r.odds_age_minutes==null?'—':`${Math.round(r.odds_age_minutes)} min`}</td>
        <td><Pill kind={STATE_KIND[r.state]}>{label(r.state)}</Pill><small className="sp-intel-subline">{r.reason}</small></td>
        <td>{r.integrity_issues?.length?<Pill kind="danger">Quarantined</Pill>:r.probability_record_id?<button className="sp-uni-link" onClick={e=>{e.stopPropagation();onRecord(r.probability_record_id)}}>Record</button>:'—'}</td>
      </tr>)}
      {!loading&&!rows.length&&<tr><td className="sp-empty" colSpan="14">No stored decisions match these filters. When nothing passes the gates, the answer is NO QUALIFIED OPPORTUNITY — the engine does not force selections.</td></tr>}
    </tbody></table></div>
    <Pager data={data} page={page} setPage={setPage}/>
  </section>;
}

function MarketAnalyzer({fixtureId,onClose}){
  const[data,setData]=useState(null),[error,setError]=useState(''),[loading,setLoading]=useState(true);
  const load=async quiet=>{if(!fixtureId)return;if(!quiet)setLoading(true);try{setData(await get(`/universal/fixtures/${encodeURIComponent(fixtureId)}/markets`));setError('')}catch(e){setError(e.message)}finally{setLoading(false)}};
  useEffect(()=>{setData(null);load()},[fixtureId]);
  useLiveReload(()=>load(true));
  if(!fixtureId)return null;
  const f=data?.fixture;
  return <section className="sp-panel sp-uni-analyzer">
    <div className="sp-panel-head"><div><span className="sp-overline">ALL-MARKET ANALYZER</span><h3>{f?`${f.home} vs ${f.away}`:'Loading fixture…'}</h3><p>{f?`${label(f.sport)} · ${f.country||f.country_code||'Country not stored'} · ${f.competition} · ${when(f.starts_at)} · ${f.status}`:''}</p></div>
      <div className="sp-intel-actions">{data&&<Pill kind={data.verdict==='QUALIFIED'?'success':'neutral'}>{data.verdict==='QUALIFIED'?<><ShieldCheck size={13}/>Best: {data.best_market?.market}</>:data.verdict}</Pill>}{onClose&&<button className="sp-secondary" onClick={onClose} aria-label="Close analyzer"><X size={15}/></button>}</div></div>
    <ErrorLine error={error}/>
    {data&&<div className="sp-kpi-grid"><Kpi title="Modelled markets" value={data.counts.modelled} tone="prediction"/><Kpi title="Priced" value={data.counts.priced} tone="football"/><Kpi title="Qualified" value={data.counts.qualified} tone="value"/><Kpi title="Quoted, no model" value={data.counts.unmodelled_quotes}/></div>}
    <div className="sp-table-scroll"><table className="sp-table"><thead><tr>
      {['Market','Outcome','Model','Probability','Odds','Break-even','Edge','EV','Conservative EV','Registry','Settlement','Status / reason','Source'].map(h=><th key={h}>{h}</th>)}
    </tr></thead><tbody>
      {(data?.markets||[]).map(r=><tr key={r.market+r.outcome}>
        <td><strong>{r.market}</strong></td><td>{r.outcome}</td>
        <td>{r.model}<small className="sp-intel-subline">{r.model_lifecycle} · cal {r.calibration_error==null?'—':Number(r.calibration_error).toFixed(3)} · n={r.holdout_fixtures??'—'}</small></td>
        <td>{pct(r.probability)}{r.model_probability!=null&&r.model_probability!==r.probability&&<small className="sp-intel-subline">raw {pct(r.model_probability)}</small>}</td>
        <td>{odds(r.odds)}<small className="sp-intel-subline">{r.bookmaker||''}{r.odds_age_minutes!=null?` · ${Math.round(r.odds_age_minutes)} min`:''}</small></td>
        <td>{pct(r.break_even)}</td><td>{signed(r.edge)}</td><td>{signed(r.expected_value)}</td>
        <td><Pill kind={r.robust?'success':'neutral'}>{signed(r.conservative_ev)}</Pill></td>
        <td>{label(r.registry_status)}{r.capability_level&&<small className="sp-intel-subline">{label(r.capability_level)}</small>}</td>
        <td className="sp-uni-rule">{r.settlement_rule||'—'}</td>
        <td><Pill kind={STATE_KIND[r.state]}>{label(r.state)}</Pill><small className="sp-intel-subline">{r.reason}</small></td>
        <td>{r.source_url?<a href={r.source_url} target="_blank" rel="noreferrer noopener" className="sp-text-action"><ExternalLink size={13}/>Quote</a>:'—'}</td>
      </tr>)}
      {!loading&&data&&!data.markets.length&&<tr><td className="sp-empty" colSpan="13">No model probability is stored for this fixture. Its sport or markets may be unsupported; nothing is estimated.</td></tr>}
    </tbody></table></div>
    {data?.unmodelled_quotes?.length>0&&<details className="sp-uni-details"><summary>Bookmaker markets with no model ({data.unmodelled_quotes.length}) — listed, not evaluated</summary>
      <div className="sp-table-scroll"><table className="sp-table"><thead><tr>{['Bookmaker','Market','Outcome','Line','Odds','Break-even','Observed'].map(h=><th key={h}>{h}</th>)}</tr></thead><tbody>
        {data.unmodelled_quotes.map((q,i)=><tr key={i}><td>{q.bookmaker}</td><td>{q.label||q.market}</td><td>{q.outcome}</td><td>{q.line??'—'}</td><td>{odds(q.odds)}</td><td>{pct(q.break_even)}</td><td>{when(q.observed_at)}</td></tr>)}
      </tbody></table></div></details>}
  </section>;
}

function FixtureVerdicts({selected,setSelected}){
  const[filters,setFilters]=useState({...EMPTY,state:''}),[page,setPage]=useState(1);
  useEffect(()=>setPage(1),[filters]);
  const{data,error,loading}=useBoard(filters,'fixtures',page);
  const rows=data?.items||[];
  return <section className="sp-panel">
    <div className="sp-panel-head"><div><h3>Fixture verdicts</h3><p>One verdict per fixture: the best qualified market, never a default to match winner, or NO QUALIFIED OPPORTUNITY with the leading reason. Select a fixture to see every market.</p></div></div>
    <Filters filters={filters} setFilters={setFilters} facets={data?.facets}/>
    <ErrorLine error={error}/>
    <div className="sp-table-scroll"><table className="sp-table"><thead><tr>{['Fixture','Sport · Country','Kick-off','Markets evaluated','Qualified','Verdict','Best market','Conservative EV'].map(h=><th key={h}>{h}</th>)}</tr></thead><tbody>
      {rows.map(g=><tr key={g.fixture_id} className={'sp-uni-row'+(selected===g.fixture_id?' selected':'')} onClick={()=>setSelected(g.fixture_id)}>
        <td><strong>{g.home} vs {g.away}</strong><small className="sp-intel-subline">{g.competition}</small></td>
        <td>{label(g.sport)}<small className="sp-intel-subline">{g.country||'—'}</small></td>
        <td>{when(g.starts_at)}</td><td>{g.markets_evaluated}</td><td>{g.qualified_markets}</td>
        <td><Pill kind={g.best?'success':'neutral'}>{g.verdict}</Pill>{!g.best&&<small className="sp-intel-subline">{g.leading_reason}</small>}</td>
        <td>{g.best?`${g.best.market} @ ${odds(g.best.odds)} · ${pct(g.best.probability)}`:'—'}</td>
        <td>{g.best?signed(g.best.conservative_ev):'—'}</td>
      </tr>)}
      {!loading&&!rows.length&&<tr><td className="sp-empty" colSpan="8">No upcoming fixtures have stored decisions for these filters.</td></tr>}
    </tbody></table></div>
    <Pager data={data} page={page} setPage={setPage}/>
  </section>;
}

function ResearchSources(){
  const{data,error,loading,refresh}=useLiveResource('/universal/sources',60000);
  const rows=data?.sources||[];
  return <section className="sp-panel">
    <div className="sp-panel-head"><div><h3>Research sources</h3><p>{data?.policy||'Named public research sources and every operator-approved host.'} Approve or decline a host under Autonomy Health → Sources & Permissions.</p></div><button className="sp-secondary" onClick={refresh} disabled={loading}><RefreshCw size={15}/>Refresh</button></div>
    {data&&<div className="sp-kpi-grid"><Kpi title="Registered sources" value={data.summary.total} tone="football"/><Kpi title="Available" value={data.summary.available} tone="value"/><Kpi title="Awaiting terms review" value={data.summary.awaiting_review} tone="ticket"/><Kpi title="Declined" value={data.summary.declined}/></div>}
    <ErrorLine error={error}/>
    <div className="sp-table-scroll"><table className="sp-table"><thead><tr>{['Source','Responsibilities','Availability','Coverage (7 days)','Freshness','Reliability','Provenance'].map(h=><th key={h}>{h}</th>)}</tr></thead><tbody>
      {rows.map(s=><tr key={s.key}>
        <td><strong>{s.name}</strong><small className="sp-intel-subline"><a href={s.url} target="_blank" rel="noreferrer noopener">{s.url}</a> · {s.sports}{s.registered==='operator_approval'?' · operator-approved':''}</small></td>
        <td className="sp-uni-rule">{s.responsibilities.join(', ')}</td>
        <td><Pill kind={AVAIL_KIND[s.availability]||'neutral'}>{label(s.availability)}</Pill><small className="sp-intel-subline">robots: {s.provenance.robots_status}</small></td>
        <td>{s.coverage.pages_permitted_7d} permitted · {s.coverage.pages_denied_7d} denied<small className="sp-intel-subline">{s.coverage.collection_jobs_7d} jobs · {s.coverage.records_saved_7d} records saved</small></td>
        <td><Pill kind={s.freshness==='fresh'?'success':s.freshness==='never'?'neutral':'warning'}>{s.freshness}</Pill><small className="sp-intel-subline">{when(s.last_activity_at)}</small></td>
        <td>{s.reliability==null?'No attempts':pct(s.reliability)}</td>
        <td>{s.provenance.approved_by||'No approval'}<small className="sp-intel-subline">{s.provenance.terms_url?<a href={s.provenance.terms_url} target="_blank" rel="noreferrer noopener">Terms reviewed {when(s.provenance.reviewed_at)}</a>:'No terms review stored'}</small></td>
      </tr>)}
    </tbody></table></div>
  </section>;
}

function CapabilityCatalogue(){
  const{data,error,loading,refresh}=useLiveResource('/universal/capabilities',60000);
  const rows=data?.sports||[];
  return <section className="sp-panel">
    <div className="sp-panel-head"><div><h3>Capability catalogue</h3><p>Every sport seen in collected fixtures is registered automatically. A sport without its own validated model stays unsupported or in research; it never borrows another sport's model.</p></div><button className="sp-secondary" onClick={refresh} disabled={loading}><RefreshCw size={15}/>Refresh</button></div>
    {data&&<div className="sp-kpi-grid"><Kpi title="Sports registered" value={rows.length} tone="football"/><Kpi title="Active" value={data.summary.active} tone="value"/><Kpi title="Research" value={data.summary.research} tone="ticket"/><Kpi title="Unsupported" value={data.summary.unsupported}/></div>}
    <ErrorLine error={error}/>
    <div className="sp-table-scroll"><table className="sp-table"><thead><tr>{['Sport','Capability','Model family','Active models','Markets by status','Upcoming fixtures','Notes'].map(h=><th key={h}>{h}</th>)}</tr></thead><tbody>
      {rows.map(s=><tr key={s.code}>
        <td><strong>{s.name}</strong><small className="sp-intel-subline">{s.code}</small></td>
        <td><Pill kind={CAP_KIND[s.capability]}>{s.capability}</Pill><small className="sp-intel-subline">{s.reason}</small></td>
        <td>{s.model_family?label(s.model_family):'None'}</td>
        <td>{s.active_models.length?s.active_models.map(m=><div key={m.version}>{m.model_name} {m.version}<small className="sp-intel-subline">cal {m.calibration_error==null?'—':Number(m.calibration_error).toFixed(3)} · n={m.holdout_fixtures??'—'}</small></div>):'None'}</td>
        <td>{Object.keys(s.markets_by_status).length?Object.entries(s.markets_by_status).map(([k,v])=><span key={k} className="sp-uni-chip">{label(k)} {v}</span>):'No markets registered'}</td>
        <td>{s.upcoming_fixtures}</td>
        <td className="sp-uni-rule">{s.notes||'—'}</td>
      </tr>)}
    </tbody></table></div>
  </section>;
}

export default function UniversalIntelligence({tab=0}){
  const[opened,setOpened]=useState(null),[selected,setSelected]=useState(null),[record,setRecord]=useState(null);
  if(tab===1)return <div className="sp-intelligence-stack"><FixtureVerdicts selected={selected} setSelected={setSelected}/>{selected?<MarketAnalyzer fixtureId={selected} onClose={()=>setSelected(null)}/>:<section className="sp-panel"><p className="sp-muted">Select a fixture above to analyse every available market.</p></section>}</div>;
  if(tab===2)return <ResearchSources/>;
  if(tab===3)return <CapabilityCatalogue/>;
  if(tab===4)return <IntegrityEligibility/>;
  if(tab===5)return <ClvDashboard/>;
  return <div className="sp-intelligence-stack"><OpportunitiesCentre onOpen={setOpened} onRecord={setRecord}/>{record&&<ProbabilityRecord id={record} onClose={()=>setRecord(null)}/>}{opened&&<MarketAnalyzer fixtureId={opened} onClose={()=>setOpened(null)}/>}</div>;
}
