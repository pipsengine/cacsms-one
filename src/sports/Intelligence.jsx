import React,{useEffect,useMemo,useState}from'react';
import {useLiveReload} from './live';
import {useLocation,useSearchParams} from 'react-router-dom';
import{Activity,AlertCircle,BrainCircuit,Download,Play,RefreshCw,ShieldCheck}from'lucide-react';
import{get,post,put}from'./api';
import'./intelligence.css';

const STATUSES=['under_analysis','awaiting_odds','awaiting_calibration','qualified','rejected','expired','suspended'];
const percent=value=>value!==null&&value!==undefined&&Number.isFinite(Number(value))?`${(Number(value)*100).toFixed(1)}%`:'—';
const number=value=>value!==null&&value!==undefined&&Number.isFinite(Number(value))?Number(value).toFixed(3):'—';
const date=value=>value?new Date(value).toLocaleString():'—';

function parseMetrics(value){
  try{return typeof value==='string'?JSON.parse(value):value||{}}
  catch{return{}}
}

function saveCsv(rows){
  const columns=['status','sport','competition','home','away','starts_at','market','probability','decimal_odds','implied_probability','expected_edge','bookmaker','model_name','model_version','reason'];
  const quote=value=>`"${String(value??'').replaceAll('"','""')}"`;
  const body=[columns.join(','),...rows.map(row=>columns.map(key=>quote(row[key])).join(','))].join('\r\n');
  const url=URL.createObjectURL(new Blob([body],{type:'text/csv;charset=utf-8'}));
  const link=document.createElement('a');link.href=url;link.download='sports-ai-opportunities.csv';link.click();
  URL.revokeObjectURL(url);
}

export function OpportunityPage({sport:initialSport}){
  const[filters,setFilters]=useState({sport:initialSport||'',status:'',market:'',bookmaker:'',competition:'',min_edge:'',max_calibration_error:'',starts_after:'',starts_before:''});
  const[rows,setRows]=useState([]),[selected,setSelected]=useState(null),[error,setError]=useState(''),[loading,setLoading]=useState(false);
  const[sort,setSort]=useState({key:'expected_edge',direction:'desc'});
  const query=useMemo(()=>{
    const params=new URLSearchParams();
    Object.entries(filters).forEach(([key,value])=>{
      if(value)params.set(key,value);
    });
    for(const key of ['starts_after','starts_before']){
      const value=params.get(key);
      if(value)params.set(key,new Date(value).toISOString());
    }
    return params.toString();
  },[filters]);
  async function refresh(quiet){
    if(quiet!==true)setLoading(true);setError('');
    try{setRows(await get(`/opportunities${query?`?${query}`:''}`))}
    catch(requestError){setError(requestError.message)}
    finally{setLoading(false)}
  }
  useEffect(()=>{refresh()},[query]);
  useLiveReload(()=>refresh(true));
  const ordered=[...rows].sort((a,b)=>{
    const left=a[sort.key]??'',right=b[sort.key]??'';
    const comparison=typeof left==='number'&&typeof right==='number'?left-right:String(left).localeCompare(String(right));
    return sort.direction==='asc'?comparison:-comparison;
  });
  function changeSort(key){
    setSort(current=>({key,direction:current.key===key&&current.direction==='desc'?'asc':'desc'}));
  }
  return <div className="sp-intelligence-stack">
    <section className="sp-panel">
      <div className="sp-panel-head"><div><h3>Opportunity Intelligence</h3><p>Only source-backed quotes and stored model evidence. Positive edge is an estimate, never a guarantee.</p></div><div className="sp-intel-actions"><button className="sp-secondary" onClick={()=>saveCsv(ordered)} disabled={!rows.length}><Download size={15}/>Export CSV</button><button className="sp-secondary" onClick={()=>refresh()} disabled={loading}><RefreshCw size={15}/>{loading?'Loading…':'Refresh'}</button></div></div>
      <div className="sp-intel-filters">
        <label>Sport<select value={filters.sport} onChange={event=>setFilters({...filters,sport:event.target.value})}><option value="">Mixed</option><option value="football">Football</option><option value="basketball">Basketball</option></select></label>
        <label>Status<select value={filters.status} onChange={event=>setFilters({...filters,status:event.target.value})}><option value="">All statuses</option>{STATUSES.map(value=><option key={value} value={value}>{value.replaceAll('_',' ')}</option>)}</select></label>
        <label>Market<input value={filters.market} onChange={event=>setFilters({...filters,market:event.target.value})} placeholder="Market code"/></label>
        <label>Bookmaker<input value={filters.bookmaker} onChange={event=>setFilters({...filters,bookmaker:event.target.value})} placeholder="Bookmaker"/></label>
        <label>Competition<input value={filters.competition} onChange={event=>setFilters({...filters,competition:event.target.value})} placeholder="Competition"/></label>
        <label>Minimum edge<input aria-describedby="edge-help" type="number" min="-1" max="100" step="0.01" value={filters.min_edge} onChange={event=>setFilters({...filters,min_edge:event.target.value})}/><small id="edge-help">Display filter only; qualification uses saved engine settings.</small></label>
        <label>Model reliability<select value={filters.max_calibration_error} onChange={event=>setFilters({...filters,max_calibration_error:event.target.value})}><option value="">Any calibration error</option><option value="0.1">Calibration error at most 0.10</option><option value="0.2">Calibration error at most 0.20</option></select></label>
        <label>Starts after<input type="datetime-local" value={filters.starts_after} onChange={event=>setFilters({...filters,starts_after:event.target.value})}/></label>
        <label>Starts before<input type="datetime-local" value={filters.starts_before} onChange={event=>setFilters({...filters,starts_before:event.target.value})}/></label>
      </div>
      <div className="sp-intel-metrics">{STATUSES.map(status=><span key={status}>{status.replaceAll('_',' ')} <strong>{rows.filter(row=>row.status===status).length}</strong></span>)}</div>
      {error&&<p className="sp-ops-error" role="alert"><AlertCircle size={16}/>{error}</p>}
      <div className="sp-table-scroll"><table className="sp-table"><thead><tr>
        {[['fixture','Fixture'],['sport','Sport'],['market','Market'],['probability','Model probability'],['decimal_odds','Odds'],['implied_probability','Implied probability'],['expected_edge','Estimated edge'],['rank_score','Rank'],['bookmaker','Bookmaker'],['odds_age_minutes','Quote age'],['status','Status']].map(([key,title])=><th key={key}><button className="sp-intel-sort" onClick={()=>changeSort(key)}>{title}{sort.key===key?(sort.direction==='asc'?' ↑':' ↓'):''}</button></th>)}
      </tr></thead><tbody>
        {ordered.map(row=>{const age=row.odds_age_minutes==null?null:Number(row.odds_age_minutes);const fresh=age!=null&&age>=0&&age<=15;return <tr key={row.id}><td>{row.home} vs {row.away}<small className="sp-intel-subline">{row.competition} · {date(row.starts_at)}</small></td><td>{row.sport}</td><td>{row.market}</td><td>{percent(row.probability)}</td><td>{row.decimal_odds==null?'Awaiting Odds':number(row.decimal_odds)}</td><td>{row.implied_probability==null?'—':percent(row.implied_probability)}</td><td>{row.expected_edge==null?'—':percent(row.expected_edge)}</td><td>{number(row.rank_score)}</td><td>{row.bookmaker||'—'}</td><td><span className={`sp-status ${fresh?'success':age==null?'neutral':'warning'}`}>{age==null?'No quote':`${Math.max(0,age).toFixed(0)}m`}</span></td><td><span className={`sp-status ${row.status==='qualified'?'success':row.status==='suspended'||row.status==='expired'?'warning':'neutral'}`}>{row.status?.replaceAll('_',' ')}</span><button className="sp-intel-link" onClick={()=>setSelected(row)}>Details</button></td></tr>})}
        {!loading&&!ordered.length&&<tr><td className="sp-empty" colSpan="11">No stored opportunities match these filters. History includes every evaluated status. Only permitted odds and a validated active model can qualify.</td></tr>}
      </tbody></table></div>
    </section>
    {selected&&<div className="sp-intel-overlay" onMouseDown={event=>{if(event.target===event.currentTarget)setSelected(null)}}><section className="sp-panel sp-intel-dialog" role="dialog" aria-modal="true" aria-labelledby="opportunity-title"><div className="sp-panel-head"><div><span className="sp-overline">{selected.sport} · {selected.competition}</span><h2 id="opportunity-title">{selected.home} vs {selected.away}</h2><p>{selected.market} · {date(selected.starts_at)}</p></div><button className="sp-secondary" onClick={()=>setSelected(null)}>Close</button></div><div className="sp-intel-detail-grid"><div><small>Status</small><strong>{selected.status}</strong></div><div><small>Model probability</small><strong>{percent(selected.probability)}</strong></div><div><small>Market-implied probability</small><strong>{percent(selected.implied_probability)}</strong></div><div><small>Estimated expected value</small><strong>{percent(selected.expected_edge)}</strong></div><div><small>Bookmaker / decimal odds</small><strong>{selected.bookmaker||'Unavailable'} / {number(selected.decimal_odds)}</strong></div><div><small>Quote observed</small><strong>{date(selected.odds_observed_at)}</strong></div><div><small>Model</small><strong>{selected.model_name} v{selected.model_version} · {selected.model_status}</strong></div><div><small>Quote age</small><strong>{selected.odds_age_minutes==null?'No quote':`${Number(selected.odds_age_minutes).toFixed(1)} minutes`}</strong></div><div><small>Data completeness</small><strong>{percent(selected.data_completeness)}</strong></div><div><small>Uncertainty</small><strong>{number(selected.uncertainty)}</strong></div><div><small>Historical calibration error</small><strong>{number(parseMetrics(selected.model_metrics).calibration_error)}</strong></div><div><small>Holdout sample</small><strong>{parseMetrics(selected.model_metrics).holdout_fixtures??'—'}</strong></div></div><p>{selected.reason}</p><h3>Calculated evidence</h3><p className="sp-intel-explanation">{parseMetrics(selected.explanation_json).summary||'See source-backed feature record below.'}</p><pre className="sp-intel-json">{JSON.stringify({features:parseMetrics(selected.explanation_json).features,settlement:parseMetrics(selected.settlement_json),modelMetrics:parseMetrics(selected.model_metrics)},null,2)}</pre>{selected.source_url&&<a href={selected.source_url} target="_blank" rel="noreferrer">Odds source reference</a>}</section></div>}
  </div>;
}

function ProviderCard({provider,onChange,onCollect,onImport,adminKey,busy}){
  const[config,setConfig]=useState(provider.config_json||'{}');
  const[enabled,setEnabled]=useState(Boolean(provider.enabled));
  const[reviewed,setReviewed]=useState(Boolean(provider.terms_reviewed));
  const[error,setError]=useState('');
  const[message,setMessage]=useState('');
  const[importFile,setImportFile]=useState(null);
  async function save(){
    setError('');setMessage('');
    try{await onChange(provider,{enabled,terms_reviewed:reviewed,config:JSON.parse(config)});setMessage('Provider settings saved.')}
    catch(requestError){setError(requestError.message)}
  }
  async function collect(){
    setError('');setMessage('');
    try{const result=await onCollect(provider);setMessage(`Collected ${result.fixtures??result.odds_quotes??0} source records.`)}
    catch(requestError){setError(requestError.message)}
  }
  async function importBasketball(){
    setError('');setMessage('');
    try{
      const text=await importFile.text();
      const records=importFile.name.toLowerCase().endsWith('.json')?JSON.parse(text):parseCsv(text);
      const result=await onImport(provider,records);
      setMessage(`${result.imported} basketball results imported.`);
    }catch(requestError){setError(requestError.message)}
  }
  return <article className="sp-provider-card"><div className="sp-panel-head"><div><h3>{provider.display_name}</h3><p>{provider.sport} · {provider.adapter}</p></div><span className={`sp-status ${provider.status==='operational'?'success':'neutral'}`}>{provider.status}</span></div><div className="sp-intel-checks"><label><input type="checkbox" checked={reviewed} onChange={event=>setReviewed(event.target.checked)}/>I reviewed this provider's current terms and data permissions</label><label><input type="checkbox" checked={enabled} onChange={event=>setEnabled(event.target.checked)}/>Enable provider</label></div><label className="sp-intel-block">Configuration JSON<textarea rows="3" value={config} onChange={event=>setConfig(event.target.value)}/></label><div className="sp-intel-actions"><button className="sp-primary" onClick={save} disabled={busy||!adminKey}>Save with admin authorization</button>{provider.adapter==='csv_import'?<><label className="sp-intel-file">Historical basketball results<input type="file" accept=".csv,.json,application/json,text/csv" onChange={event=>setImportFile(event.target.files?.[0]||null)}/></label><button className="sp-secondary" disabled={!importFile||busy||!provider.enabled||!provider.terms_reviewed} onClick={importBasketball}>Import results</button></>:<button className="sp-secondary" onClick={collect} disabled={busy||!provider.enabled||!provider.terms_reviewed}>Collect now</button>}</div><p className="sp-intel-fine">Last successful collection: {date(provider.last_success_at)}. Provider status reflects persisted results.</p>{message&&<p role="status">{message}</p>}{error&&<p className="sp-ops-error" role="alert">{error}</p>}</article>;
}

export function parseCsv(text){
  const lines=text.replace(/^\uFEFF/,'').split(/\r?\n/).filter(Boolean);
  if(lines.length<2)throw Error('CSV must include a header and at least one result.');
  const parseLine=line=>{
    const values=[];let value='',quoted=false;
    for(let i=0;i<line.length;i++){
      const char=line[i];
      if(char==='"'&&quoted&&line[i+1]==='"'){value+='"';i++}
      else if(char==='"')quoted=!quoted;
      else if(char===','&&!quoted){values.push(value);value=''}
      else value+=char;
    }
    if(quoted)throw Error('CSV contains an unclosed quoted field.');
    values.push(value);return values;
  };
  const headers=parseLine(lines[0]).map(value=>value.trim().toLowerCase());
  const required=['id','date','home','away','home_score','away_score'];
  if(required.some(key=>!headers.includes(key)))throw Error(`CSV headers must include ${required.join(', ')}.`);
  return lines.slice(1).map((line,index)=>{
    const values=parseLine(line);
    if(values.length!==headers.length)throw Error(`CSV row ${index+2} has an unexpected number of columns.`);
    const row=Object.fromEntries(values.map((value,column)=>[headers[column],value.trim()]));
    for(const key of ['home_score','away_score']){
      const score=Number(row[key]);
      if(!row[key]||!Number.isInteger(score)||score<0||score>300)throw Error(`CSV row ${index+2} has an invalid ${key}.`);
      row[key]=score;
    }
    return row;
  });
}

export function ProviderManager(){
  const[providers,setProviders]=useState([]),[adminKey,setAdminKey]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  async function refresh(){
    setError('');
    try{setProviders(await get('/providers'))}
    catch(requestError){setError(requestError.message)}
  }
  useEffect(()=>{refresh()},[]);
  async function update(provider,payload){
    await put(`/providers/${encodeURIComponent(provider.id)}`,payload,{'X-Sports-Admin-Key':adminKey});
    await refresh();
  }
  async function collect(provider){
    setBusy(true);
    try{return await post(`/providers/${encodeURIComponent(provider.id)}/collect`,{})}
    finally{setBusy(false);await refresh()}
  }
  async function importRows(provider,rows){
    setBusy(true);
    try{return await post(`/providers/${encodeURIComponent(provider.id)}/basketball-import`,{rows})}
    finally{setBusy(false);await refresh()}
  }
  return <div className="sp-intelligence-stack"><section className="sp-panel"><h3>Permitted provider configuration</h3><p className="sp-muted">Free football APIs and public datasets can be enabled after reviewing their current terms. Basketball historical results can be imported from a properly licensed CSV. The optional odds API may require a paid plan and is not required for this portal. No quote is fabricated.</p><label className="sp-intel-admin">Provider administrator key<input type="password" autoComplete="new-password" value={adminKey} onChange={event=>setAdminKey(event.target.value)} placeholder="SPORTS_ADMIN_API_KEY"/></label>{error&&<p className="sp-ops-error" role="alert">{error}</p>}</section>{providers.map(provider=><ProviderCard key={provider.id} provider={provider} adminKey={adminKey} busy={busy} onChange={update} onCollect={collect} onImport={importRows}/>)}</div>;
}

export function ModelManagement(){
  const[models,setModels]=useState([]),[sport,setSport]=useState(''),[adminKey,setAdminKey]=useState(''),[error,setError]=useState(''),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
  async function refresh(){
    try{setModels(await get('/models'));setError('')}
    catch(requestError){setError(requestError.message)}
  }
  useEffect(()=>{refresh()},[]);
  async function train(){
    setBusy(true);setError('');setMessage('');
    try{
      const result=await post('/training/run',{sport:sport||null});
      setMessage(result.models.map(model=>`${model.sport}: ${model.status}, ${model.metrics.fixtures_scored} walk-forward fixtures, ${model.metrics.holdout_fixtures} holdout`).join(' · '));
      await refresh();
    }catch(requestError){setError(requestError.message)}
    finally{setBusy(false)}
  }
  async function rollback(model){
    setBusy(true);setError('');setMessage('');
    try{
      const result=await post(`/models/${encodeURIComponent(model.id)}/rollback`,{},{'X-Sports-Admin-Key':adminKey});
      setMessage(result.restored_model_id?`Restored model ${result.restored_model_id}.`:'Active model removed. No earlier validated version was eligible.');
      await refresh();
    }catch(requestError){setError(requestError.message)}
    finally{setBusy(false)}
  }
  async function suspend(model){
    setBusy(true);setError('');setMessage('');
    try{
      await post(`/models/${encodeURIComponent(model.id)}/suspend`,{},{'X-Sports-Admin-Key':adminKey});
      setMessage(`${model.model_name} v${model.version} suspended. It cannot qualify a booking.`);
      await refresh();
    }catch(requestError){setError(requestError.message)}
    finally{setBusy(false)}
  }
  async function activate(model){
    setBusy(true);setError('');setMessage('');
    try{
      await post(`/models/${encodeURIComponent(model.id)}/activate`,{}, {'X-Sports-Admin-Key':adminKey});
      setMessage(`${model.model_name} v${model.version} activated.`);
      await refresh();
    }catch(requestError){setError(requestError.message)}
    finally{setBusy(false)}
  }
  return <div className="sp-intelligence-stack"><section className="sp-panel"><div className="sp-panel-head"><div><h3>Model registry and validation</h3><p>Chronological walk-forward validation. Training never activates a model automatically.</p></div><BrainCircuit size={22}/></div><div className="sp-intel-filters"><label>Sport<select value={sport} onChange={event=>setSport(event.target.value)}><option value="">Both sports</option><option value="football">Football</option><option value="basketball">Basketball</option></select></label><label>Activation admin key<input type="password" autoComplete="new-password" value={adminKey} onChange={event=>setAdminKey(event.target.value)}/></label></div><button className="sp-primary" onClick={train} disabled={busy}><Play size={16}/>{busy?'Running…':'Train and evaluate'}</button><p className="sp-intel-fine">A model is only eligible for activation after adequate sample coverage and held-out calibration. Complex models are not claimed without sufficient data.</p>{error&&<p className="sp-ops-error" role="alert">{error}</p>}{message&&<p role="status">{message}</p>}</section>{models.map(model=>{const metrics=parseMetrics(model.metrics_json);return <article className="sp-panel sp-intel-model" key={model.id}><div className="sp-panel-head"><div><h3>{model.model_name} v{model.version} · {model.sport}</h3><p>Registry status: {model.status} · Lifecycle: {model.lifecycle||'research'} · Training: {model.training_sample_size} walk-forward fixtures · Calibration holdout: {model.calibration_sample_size??0}</p></div><span className={`sp-status ${model.lifecycle==='active'?'success':'neutral'}`}>{model.lifecycle||model.status}</span></div><div className="sp-intel-metrics"><span>Brier score <strong>{number(metrics.brier_score)}</strong></span><span>Log loss <strong>{number(metrics.log_loss)}</strong></span><span>Calibration error <strong>{number(metrics.calibration_error)}</strong></span><span>Holdout size <strong>{metrics.holdout_fixtures??0}</strong></span><span>Walk-forward scored <strong>{metrics.fixtures_scored??0}</strong></span></div><ReliabilityChart bins={metrics.bins||[]}/><details><summary>Calibration reliability bins</summary><div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>Bucket</th><th>N</th><th>Predicted</th><th>Observed</th></tr></thead><tbody>{(metrics.bins||[]).map(bin=><tr key={bin.lower}><td>{percent(bin.lower)}–{percent(bin.upper)}</td><td>{bin.sample_size}</td><td>{percent(bin.mean_predicted)}</td><td>{percent(bin.observed_frequency)}</td></tr>)}</tbody></table></div></details><p className="sp-intel-fine">Selected family: {parseMetrics(model.parameters_json).selected_family||'baseline'} · Calibration: {parseMetrics(model.parameters_json).calibration||model.calibration_status} · Gradient boosting: {metrics.gradient_boosting||'n/a'} · Ridge: {metrics.ridge||'n/a'}</p>{model.status==='validated'&&!model.active&&<button className="sp-secondary" disabled={busy||!adminKey} onClick={()=>activate(model)}><ShieldCheck size={16}/>Activate validated model</button>}{model.active?<button className="sp-secondary" disabled={busy||!adminKey} onClick={()=>suspend(model)}>Suspend model</button>:null}{model.active?<button className="sp-secondary" disabled={busy||!adminKey} onClick={()=>rollback(model)}>Roll back active model</button>:null}</article>})}{!models.length&&!error&&<div className="sp-panel"><Activity size={20}/><p>No training records yet. Import settled historical results with source metadata, then run training.</p></div>}</div>;
}

function ReliabilityChart({bins}){
  const populated=(bins||[]).filter(bin=>bin.sample_size);
  if(!populated.length)return <p className="sp-muted">Reliability diagram appears after a holdout sample exists.</p>;
  return <div className="sp-reliability" aria-label="Reliability diagram">{populated.map(bin=><div key={bin.lower} className="sp-reliability-row"><span>{percent(bin.lower)}</span><div className="sp-reliability-track"><i style={{width:`${Math.min(100,(bin.mean_predicted||0)*100)}%`}}/><b style={{width:`${Math.min(100,(bin.observed_frequency||0)*100)}%`}}/></div><small>n={bin.sample_size}</small></div>)}<p className="sp-intel-fine">Navy bar: mean predicted probability. Blue bar: observed frequency.</p></div>;
}

function ProbabilityChart({rows}){
  const focus=rows.filter(row=>['HOME','DRAW','AWAY','MONEYLINE_HOME','MONEYLINE_AWAY'].includes(row.market));
  if(!focus.length)return null;
  return <div className="sp-probability-chart">{focus.map(row=><div key={row.id}><span>{row.market}</span><div><i style={{width:`${Math.min(100,Number(row.probability||0)*100)}%`}}/></div><strong>{percent(row.probability)}</strong></div>)}</div>;
}

function rate(value){return value===null||value===undefined?'—':percent(value)}
function goals(value){return value===null||value===undefined||!Number.isFinite(Number(value))?'—':Number(value).toFixed(2)}
function TeamRecord({profile,sport='football'}){
  if(!profile)return null;
  const windows=[['Last 5',profile.last_5],['Last 10',profile.last_10],['Last 15',profile.last_15]];
  return <section className="sp-panel"><h3>{profile.team}</h3><div className="sp-intel-metrics"><span>Stored matches <strong>{profile.played}</strong></span><span>Home <strong>{profile.home_played}</strong></span><span>Away <strong>{profile.away_played}</strong></span><span>{sport==='basketball'?'Points per game':'Goals per match'} <strong>{goals(profile.overall.average_for)} / {goals(profile.overall.average_against)}</strong></span><span>Clean sheets <strong>{rate(profile.overall.clean_sheet_rate)}</strong></span><span>Both teams score <strong>{rate(profile.overall.btts_rate)}</strong></span></div><p className="sp-intel-fine">{profile.trend||'A recent trend needs two separate five-match samples.'}</p><div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>Window</th><th>Sample</th><th>W-D-L</th><th>Form</th><th>For</th><th>Against</th><th>Over 2.5</th></tr></thead><tbody>{windows.map(([label,row])=><tr key={label}><td>{label}</td><td>{row.complete?row.played:`${row.available} of ${row.requested}`}</td><td>{row.wins}-{row.draws}-{row.losses}</td><td>{row.form||'—'}</td><td>{goals(row.average_for)}</td><td>{goals(row.average_against)}</td><td>{rate(row.over_2_5)}</td></tr>)}</tbody></table></div><h3>Home and away</h3><div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>Venue</th><th>Played</th><th>W-D-L</th><th>Goals for</th><th>Goals against</th><th>Clean sheets</th></tr></thead><tbody><tr><td>Home</td><td>{profile.home.played}</td><td>{profile.home.wins}-{profile.home.draws}-{profile.home.losses}</td><td>{profile.home.goals_for}</td><td>{profile.home.goals_against}</td><td>{rate(profile.home.clean_sheet_rate)}</td></tr><tr><td>Away</td><td>{profile.away.played}</td><td>{profile.away.wins}-{profile.away.draws}-{profile.away.losses}</td><td>{profile.away.goals_for}</td><td>{profile.away.goals_against}</td><td>{rate(profile.away.clean_sheet_rate)}</td></tr></tbody></table></div></section>;
}
export function FixtureAnalysis({fixtures,sport,tab=0,runToken=0,onRunState}){
  const [params]=useSearchParams();
  const location=useLocation();
  const requested=params.get('fixture')||'';
  const seeded=location.state?.fixture?.id===requested?location.state.fixture:null;
  const[fixtureId,setFixtureId]=useState(requested),[report,setReport]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[catalog,setCatalog]=useState([]),[pinned,setPinned]=useState(seeded);
  useEffect(()=>{get(`/fixtures?sport=${sport}&window=upcoming&page_size=100`).then(value=>setCatalog(value.items||[])).catch(()=>setCatalog([]))},[sport]);
  useEffect(()=>{
    if(!requested)return undefined;
    let cancel=false;
    get(`/fixtures?fixture_id=${encodeURIComponent(requested)}`).then(value=>{
      const rows=Array.isArray(value)?value:(value.items||[]);
      if(!cancel&&rows[0])setPinned(rows[0]);
    }).catch(()=>{});
    return ()=>{cancel=true};
  },[requested]);
  const matches=useMemo(()=>{const byId=new Map();[pinned,seeded,...catalog,...fixtures.filter(item=>item.sport===sport)].filter(Boolean).forEach(item=>byId.set(item.id,item));return [...byId.values()].sort((a,b)=>String(a.kickoff_at).localeCompare(String(b.kickoff_at)))},[catalog,fixtures,sport,pinned,seeded]);
  useEffect(()=>{if(!requested&&!matches.some(item=>item.id===fixtureId))setFixtureId(matches[0]?.id||'')},[matches,fixtureId,requested]);
  useEffect(()=>{
    if(!fixtureId){setReport(null);return}
    let cancel=false;
    setBusy(true);setError('');onRunState?.(true);
    post(`/fixtures/${encodeURIComponent(fixtureId)}/analyze`,{})
      .then(value=>{if(!cancel)setReport(value)})
      .catch(requestError=>{if(!cancel)setError(requestError.message)})
      .finally(()=>{if(!cancel){setBusy(false);onRunState?.(false)}});
    return ()=>{cancel=true};
  },[fixtureId,runToken]);
  const fixture=matches.find(item=>item.id===fixtureId)||(report?.fixture_id===fixtureId?{home:report.home,away:report.away,competition:report.competition,kickoff_at:report.kickoff_at,source:report.provider}:null);
  const forecast=report?.forecast;
  const matrix=forecast?.matrix||[];
  const goalLabels=[...new Set(matrix.map(item=>item.home_goals))].sort((a,b)=>a-b);
  return <div className="sp-intelligence-stack"><section className="sp-panel"><label className="sp-intel-fixture">Fixture<select value={fixtureId} onChange={event=>setFixtureId(event.target.value)}><option value="">Select a fixture</option>{matches.map(item=><option key={item.id} value={item.id}>{item.home} vs {item.away} · {item.competition}</option>)}</select></label>{fixture&&<><h2>{fixture.home} vs {fixture.away}</h2><p>{fixture.competition} · {date(fixture.kickoff_at)} · {report?.provider||fixture.provider_name||fixture.source||'Stored fixture'}</p></>}{busy&&<p>Collecting historical results, checking sample sizes, building features, and running the Poisson model.</p>}{error&&<p className="sp-ops-error" role="alert">{error}</p>}{report?.stages&&<div className="sp-intel-metrics">{report.stages.map(stage=><span key={stage.name}>{stage.name} <strong>{stage.status}</strong></span>)}</div>}</section>
  {!busy&&report&&tab===0&&<><TeamRecord profile={report.home_profile} sport={sport}/><TeamRecord profile={report.away_profile} sport={sport}/><section className="sp-panel"><h3>Head-to-head before kickoff</h3>{report.head_to_head?.length?<div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>Date</th><th>Match</th><th>Score</th><th>Source</th></tr></thead><tbody>{report.head_to_head.map(row=><tr key={row.kickoff_at+row.home}><td>{date(row.kickoff_at)}</td><td>{row.home} vs {row.away}</td><td>{row.home_score}–{row.away_score}</td><td>{row.source}</td></tr>)}</tbody></table></div>:<p>No completed head-to-head match is stored before this kickoff.</p>}<h3>League table</h3>{report.standings?.length?<div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>Team</th><th>Pos</th><th>Played</th><th>W-D-L</th><th>GD</th><th>Pts</th><th>Source team id</th></tr></thead><tbody>{report.standings.map(row=><tr key={row.team}><td>{row.team}</td><td>{row.position}</td><td>{row.played}</td><td>{row.won}-{row.drawn}-{row.lost}</td><td>{row.goal_difference}</td><td>{row.points}</td><td>{row.details?.team_id??'—'}</td></tr>)}</tbody></table></div>:<p>No standings snapshot is stored for this competition.</p>}<p className="sp-intel-fine">{report.forecast_note}</p>{!!report.missing?.length&&<p>Limitations: {report.missing.join(' ')}</p>}</section></>}
  {!busy&&report&&tab===1&&<section className="sp-panel">{forecast?<><h3>{sport==='basketball'?'Expected points':'Expected goals'}</h3><div className="sp-intel-metrics"><span>Home <strong>{goals(forecast.expected_home_goals??forecast.expected_home_points)}</strong></span><span>Away <strong>{goals(forecast.expected_away_goals??forecast.expected_away_points)}</strong></span><span>Home attack <strong>{goals(forecast.home_attack)}</strong></span><span>Away attack <strong>{goals(forecast.away_attack)}</strong></span><span>Dixon-Coles rho <strong>{forecast.dixon_coles_rho?goals(forecast.dixon_coles_rho):'Not applied'}</strong></span><span>Outcome sum <strong>{goals(forecast.outcome_probability_sum)}</strong></span></div><h3>Most likely scorelines</h3><div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>Home goals</th><th>Away goals</th><th>Probability</th></tr></thead><tbody>{(forecast.scorelines||[]).map(item=><tr key={`${item.home_goals}-${item.away_goals}`}><td>{item.home_goals}</td><td>{item.away_goals}</td><td>{percent(item.probability)}</td></tr>)}</tbody></table></div>{!!matrix.length&&<><h3>Scoreline probability matrix, goals 0–5</h3><div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>Home \\ Away</th>{goalLabels.map(goal=><th key={goal}>{goal}</th>)}</tr></thead><tbody>{goalLabels.map(home=><tr key={home}><td>{home}</td>{goalLabels.map(away=>{const cell=matrix.find(item=>item.home_goals===home&&item.away_goals===away);return <td key={away}>{cell?percent(cell.probability):'—'}</td>})}</tr>)}</tbody></table></div></>}<h3>Goal distribution markets</h3><div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>Market</th><th>Model probability</th></tr></thead><tbody>{(report.markets||[]).filter(row=>row.market.startsWith('OVER_')||row.market.startsWith('UNDER_')||row.market.startsWith('BTTS_')).map(row=><tr key={row.market}><td>{row.market}</td><td>{percent(row.probability)}</td></tr>)}</tbody></table></div></>:<p>{report.forecast_note||'The Poisson forecast was not produced from the stored history.'}</p>}</section>}
  {!busy&&report&&tab===2&&<section className="sp-panel"><h3>Market probabilities</h3><div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>Market</th><th>Model probability</th><th>Bookmaker odds</th><th>Implied probability</th><th>Estimated edge</th></tr></thead><tbody>{(report.markets||[]).map(row=><tr key={row.market}><td>{row.market}</td><td>{percent(row.probability)}</td><td>{row.odds_status}</td><td>—</td><td>—</td></tr>)}{!(report.markets||[]).length&&<tr><td className="sp-empty" colSpan="5">No model probability is stored for this fixture.</td></tr>}</tbody></table></div><p className="sp-intel-fine">Bookmaker odds stay unavailable until a permitted quote is stored. No edge is calculated without that quote.</p></section>}
  {!busy&&report&&tab===3&&<section className="sp-panel"><h3>Model evidence</h3><div className="sp-intel-metrics"><span>Level <strong>{report.forecast_level}</strong></span><span>Model <strong>{forecast?.model?.name||'—'} {forecast?.model?.version?`v${forecast.model.version}`:''}</strong></span><span>Registry status <strong>{forecast?.model?.status||'not used'}</strong></span><span>Activated <strong>{forecast?.model?.active?'yes':'no'}</strong></span><span>Competition sample <strong>{forecast?.sample?.historical_games??report.counts?.home_team_matches??0}</strong></span><span>Collected <strong>{date(report.generated_at)}</strong></span></div><p className="sp-intel-explanation">{report.forecast_note}</p><p>Provider: {report.provider||'—'} · Fixture source observed {date(report.source_observed_at)}{report.source_url?` · ${report.source_url}`:''}</p><h3>What is missing</h3><ul>{(report.missing||[]).map(item=><li key={item}>{item}</li>)}</ul>{report.stages&&<div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>Stage</th><th>Status</th><th>Detail</th></tr></thead><tbody>{report.stages.map(stage=><tr key={stage.name}><td>{stage.name}</td><td>{stage.status}</td><td>{stage.detail}</td></tr>)}</tbody></table></div>}</section>}
  </div>;
}

export function OddsIntelligence(){
  const[data,setData]=useState(null),[error,setError]=useState(''),[loading,setLoading]=useState(false);
  async function refresh(quiet){
    if(quiet!==true)setLoading(true);setError('');
    try{setData(await get('/odds/intelligence'))}
    catch(requestError){setError(requestError.message)}
    finally{setLoading(false)}
  }
  useEffect(()=>{refresh()},[]);
  useLiveReload(()=>refresh(true));
  const quotes=data?.quotes||[];
  return <div className="sp-intelligence-stack"><section className="sp-panel"><div className="sp-panel-head"><div><h3>Odds intelligence</h3><p>Imported quotes only. Opening and current prices come from stored snapshots. Nothing is filled in when a provider has no price.</p></div><button className="sp-secondary" onClick={()=>refresh()} disabled={loading}><RefreshCw size={15}/>{loading?'Loading…':'Refresh'}</button></div>{error&&<p className="sp-ops-error" role="alert">{error}</p>}<div className="sp-intel-metrics">{(data?.providers||[]).map(provider=><span key={provider.id}>{provider.display_name} <strong>{provider.status}</strong><small>{provider.last_success_at?date(provider.last_success_at):'No successful collection'}</small></span>)}</div></section><section className="sp-panel"><h3>Current quotes</h3><div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>Fixture</th><th>Bookmaker</th><th>Market</th><th>Opening</th><th>Current</th><th>Implied probability</th><th>Age</th></tr></thead><tbody>{quotes.map(quote=>{const age=Number(quote.age_minutes);const fresh=Number.isFinite(age)&&age>=0&&age<=15;return <tr key={quote.id}><td>{quote.home} vs {quote.away}<small className="sp-intel-subline">{quote.competition}</small></td><td>{quote.bookmaker}</td><td>{quote.market} · {quote.outcome}{quote.line==null?'':` ${quote.line}`}</td><td>{number(quote.opening_decimal_odds)}</td><td>{number(quote.decimal_odds)}</td><td>{percent(1/quote.decimal_odds)}</td><td><span className={`sp-status ${fresh?'success':'warning'}`}>{Number.isFinite(age)?`${Math.max(0,age).toFixed(0)}m`:'—'}</span></td></tr>})}{!quotes.length&&<tr><td className="sp-empty" colSpan="7">No permitted odds snapshots are stored.</td></tr>}</tbody></table></div>{!!data?.conflicting_duplicates?.length&&<><h3>Conflicting duplicates</h3><pre className="sp-intel-json">{JSON.stringify(data.conflicting_duplicates,null,2)}</pre></>}</section></div>;
}
