import React,{useEffect,useMemo,useState}from'react';
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
  const[filters,setFilters]=useState({sport:initialSport||'',status:'',market:'',bookmaker:'',competition:'',min_edge:'0.05',starts_after:'',starts_before:''});
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
  async function refresh(){
    setLoading(true);setError('');
    try{setRows(await get(`/opportunities${query?`?${query}`:''}`))}
    catch(requestError){setError(requestError.message)}
    finally{setLoading(false)}
  }
  useEffect(()=>{refresh()},[query]);
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
      <div className="sp-panel-head"><div><h3>Opportunity Intelligence</h3><p>Only source-backed quotes and stored model evidence. Positive edge is an estimate, never a guarantee.</p></div><div className="sp-intel-actions"><button className="sp-secondary" onClick={()=>saveCsv(ordered)} disabled={!rows.length}><Download size={15}/>Export CSV</button><button className="sp-secondary" onClick={refresh} disabled={loading}><RefreshCw size={15}/>{loading?'Loading…':'Refresh'}</button></div></div>
      <div className="sp-intel-filters">
        <label>Sport<select value={filters.sport} onChange={event=>setFilters({...filters,sport:event.target.value})}><option value="">Mixed</option><option value="football">Football</option><option value="basketball">Basketball</option></select></label>
        <label>Status<select value={filters.status} onChange={event=>setFilters({...filters,status:event.target.value})}><option value="">All statuses</option>{STATUSES.map(value=><option key={value} value={value}>{value.replaceAll('_',' ')}</option>)}</select></label>
        <label>Market<input value={filters.market} onChange={event=>setFilters({...filters,market:event.target.value})} placeholder="Market code"/></label>
        <label>Bookmaker<input value={filters.bookmaker} onChange={event=>setFilters({...filters,bookmaker:event.target.value})} placeholder="Bookmaker"/></label>
        <label>Competition<input value={filters.competition} onChange={event=>setFilters({...filters,competition:event.target.value})} placeholder="Competition"/></label>
        <label>Minimum edge<input aria-describedby="edge-help" type="number" min="-1" max="100" step="0.01" value={filters.min_edge} onChange={event=>setFilters({...filters,min_edge:event.target.value})}/><small id="edge-help">Display filter only; qualification uses saved engine settings.</small></label>
        <label>Starts after<input type="datetime-local" value={filters.starts_after} onChange={event=>setFilters({...filters,starts_after:event.target.value})}/></label>
        <label>Starts before<input type="datetime-local" value={filters.starts_before} onChange={event=>setFilters({...filters,starts_before:event.target.value})}/></label>
      </div>
      {error&&<p className="sp-ops-error" role="alert"><AlertCircle size={16}/>{error}</p>}
      <div className="sp-table-scroll"><table className="sp-table"><thead><tr>
        {[[`fixture`,'Fixture'],['sport','Sport'],['market','Market'],['probability','Model P'],['decimal_odds','Odds'],['implied_probability','Implied P'],['expected_edge','Expected edge'],['bookmaker','Bookmaker'],['odds_age_minutes','Quote age (min)'],['status','Status']].map(([key,title])=><th key={key}><button className="sp-intel-sort" onClick={()=>changeSort(key)}>{title}{sort.key===key?(sort.direction==='asc'?' ↑':' ↓'):''}</button></th>)}
      </tr></thead><tbody>
        {ordered.map(row=><tr key={row.id}><td>{row.home} vs {row.away}<small className="sp-intel-subline">{row.competition} · {date(row.starts_at)}</small></td><td>{row.sport}</td><td>{row.market}</td><td>{percent(row.probability)}</td><td>{number(row.decimal_odds)}</td><td>{percent(row.implied_probability)}</td><td>{percent(row.expected_edge)}</td><td>{row.bookmaker||'—'}</td><td>{row.odds_age_minutes==null?'—':`${Math.max(0,Number(row.odds_age_minutes)).toFixed(0)}m`}</td><td><span className={`sp-status ${row.status==='qualified'?'success':row.status==='suspended'||row.status==='expired'?'warning':'neutral'}`}>{row.status?.replaceAll('_',' ')}</span><button className="sp-intel-link" onClick={()=>setSelected(row)}>Details</button></td></tr>)}
        {!loading&&!ordered.length&&<tr><td className="sp-empty" colSpan="10">No stored opportunities match these filters. Import historical results and upcoming fixtures; only permitted odds can qualify.</td></tr>}
      </tbody></table></div>
    </section>
    {selected&&<div className="sp-intel-overlay" onMouseDown={event=>{if(event.target===event.currentTarget)setSelected(null)}}><section className="sp-panel sp-intel-dialog" role="dialog" aria-modal="true" aria-labelledby="opportunity-title"><div className="sp-panel-head"><div><span className="sp-overline">{selected.sport} · {selected.competition}</span><h2 id="opportunity-title">{selected.home} vs {selected.away}</h2><p>{selected.market} · {date(selected.starts_at)}</p></div><button className="sp-secondary" onClick={()=>setSelected(null)}>Close</button></div><div className="sp-intel-detail-grid"><div><small>Status</small><strong>{selected.status}</strong></div><div><small>Model probability</small><strong>{percent(selected.probability)}</strong></div><div><small>Market-implied probability</small><strong>{percent(selected.implied_probability)}</strong></div><div><small>Estimated expected value</small><strong>{percent(selected.expected_edge)}</strong></div><div><small>Bookmaker / decimal odds</small><strong>{selected.bookmaker||'Unavailable'} / {number(selected.decimal_odds)}</strong></div><div><small>Quote observed</small><strong>{date(selected.odds_observed_at)}</strong></div><div><small>Model</small><strong>{selected.model_name} v{selected.model_version} · {selected.model_status}</strong></div><div><small>Outcome data age</small><strong>{selected.odds_age_minutes==null?'No quote':`${Number(selected.odds_age_minutes).toFixed(1)} minutes`}</strong></div></div><p>{selected.reason}</p><h3>Calculated evidence</h3><p className="sp-intel-explanation">{parseMetrics(selected.explanation_json).summary||'See source-backed feature record below.'}</p><pre className="sp-intel-json">{JSON.stringify({features:parseMetrics(selected.explanation_json).features,settlement:parseMetrics(selected.settlement_json),modelMetrics:parseMetrics(selected.model_metrics)},null,2)}</pre>{selected.source_url&&<a href={selected.source_url} target="_blank" rel="noreferrer">Odds source reference</a>}</section></div>}
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

function parseCsv(text){
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
  return lines.slice(1).map(line=>Object.fromEntries(parseLine(line).map((value,index)=>[headers[index],value.trim()]))).map(row=>({
    ...row,home_score:Number(row.home_score),away_score:Number(row.away_score),
  }));
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
  async function activate(model){
    setBusy(true);setError('');setMessage('');
    try{
      await post(`/models/${encodeURIComponent(model.id)}/activate`,{}, {'X-Sports-Admin-Key':adminKey});
      setMessage(`${model.model_name} v${model.version} activated.`);
      await refresh();
    }catch(requestError){setError(requestError.message)}
    finally{setBusy(false)}
  }
  return <div className="sp-intelligence-stack"><section className="sp-panel"><div className="sp-panel-head"><div><h3>Model registry and validation</h3><p>Chronological walk-forward validation. Training never activates a model automatically.</p></div><BrainCircuit size={22}/></div><div className="sp-intel-filters"><label>Sport<select value={sport} onChange={event=>setSport(event.target.value)}><option value="">Both sports</option><option value="football">Football</option><option value="basketball">Basketball</option></select></label><label>Activation admin key<input type="password" autoComplete="new-password" value={adminKey} onChange={event=>setAdminKey(event.target.value)}/></label></div><button className="sp-primary" onClick={train} disabled={busy}><Play size={16}/>{busy?'Running…':'Train and evaluate'}</button><p className="sp-intel-fine">A model is only eligible for activation after adequate sample coverage and held-out calibration. Complex models are not claimed without sufficient data.</p>{error&&<p className="sp-ops-error" role="alert">{error}</p>}{message&&<p role="status">{message}</p>}</section>{models.map(model=>{const metrics=parseMetrics(model.metrics_json);return <article className="sp-panel sp-intel-model" key={model.id}><div className="sp-panel-head"><div><h3>{model.model_name} v{model.version} · {model.sport}</h3><p>Registry status: {model.status} · Training: {model.training_sample_size} walk-forward fixtures · Calibration holdout: {model.calibration_sample_size??0}</p></div><span className={`sp-status ${model.active?'success':'neutral'}`}>{model.active?'active':model.status}</span></div><div className="sp-intel-metrics"><span>Brier score <strong>{number(metrics.brier_score)}</strong></span><span>Log loss <strong>{number(metrics.log_loss)}</strong></span><span>Calibration error <strong>{number(metrics.calibration_error)}</strong></span><span>Holdout size <strong>{metrics.holdout_fixtures??0}</strong></span><span>Walk-forward scored <strong>{metrics.fixtures_scored??0}</strong></span></div><details><summary>Calibration reliability bins</summary><div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>Bucket</th><th>N</th><th>Predicted</th><th>Observed</th></tr></thead><tbody>{(metrics.bins||[]).map(bin=><tr key={bin.lower}><td>{percent(bin.lower)}–{percent(bin.upper)}</td><td>{bin.sample_size}</td><td>{percent(bin.mean_predicted)}</td><td>{percent(bin.observed_frequency)}</td></tr>)}</tbody></table></div></details>{model.status==='validated'&&!model.active&&<button className="sp-secondary" disabled={busy||!adminKey} onClick={()=>activate(model)}><ShieldCheck size={16}/>Activate validated model</button>}</article>})}{!models.length&&!error&&<div className="sp-panel"><Activity size={20}/><p>No training records yet. Import settled historical results with source metadata, then run training.</p></div>}</div>;
}

export function FixtureAnalysis({fixtures,sport}){
  const[fixtureId,setFixtureId]=useState(''),[rows,setRows]=useState([]),[error,setError]=useState(''),[loading,setLoading]=useState(false);
  const matches=fixtures.filter(item=>item.sport===sport);
  useEffect(()=>{setFixtureId(matches[0]?.id||'')},[sport,fixtures.length]);
  useEffect(()=>{
    if(!fixtureId){setRows([]);return}
    setLoading(true);get(`/fixtures/${encodeURIComponent(fixtureId)}/analysis`)
      .then(setRows).catch(requestError=>setError(requestError.message))
      .finally(()=>setLoading(false));
  },[fixtureId]);
  const fixture=matches.find(item=>item.id===fixtureId);
  return <div className="sp-intelligence-stack"><section className="sp-panel"><label className="sp-intel-fixture">Fixture<select value={fixtureId} onChange={event=>setFixtureId(event.target.value)}><option value="">Select a fixture</option>{matches.map(item=><option key={item.id} value={item.id}>{item.home} vs {item.away} · {item.competition}</option>)}</select></label>{fixture&&<><h2>{fixture.home} vs {fixture.away}</h2><p>{fixture.competition} · {date(fixture.kickoff_at)} · Data observed {date(fixture.observed_at)}</p></>}{error&&<p className="sp-ops-error" role="alert">{error}</p>}</section><section className="sp-panel"><h3>Historical feature analysis</h3>{loading?<p>Loading analysis…</p>:rows.length?<><div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>Market</th><th>Probability</th><th>Settlement distribution</th><th>Model/version</th><th>Validation status</th></tr></thead><tbody>{rows.map(row=><tr key={row.id}><td>{row.market}</td><td>{percent(row.probability)}</td><td>{JSON.stringify(parseMetrics(row.settlement_json))}</td><td>{row.model_name} v{row.model_version}</td><td>{row.model_status}</td></tr>)}</tbody></table></div><h3>Actual calculated features</h3><pre className="sp-intel-json">{JSON.stringify(parseMetrics(rows[0].features_json),null,2)}</pre><p>Source references: {JSON.stringify(parseMetrics(rows[0].source_refs_json))} · Features generated {date(rows[0].features_generated_at)}</p><p className="sp-intel-fine">Probabilities are model estimates, not guarantees. Only supported markets with appropriate calibration and fresh bookmaker prices can qualify.</p></>:<p className="sp-muted">{fixture?'No source-backed historical form is sufficient for a prediction yet.':'Import or collect a fixture first.'}</p>}</section></div>;
}
