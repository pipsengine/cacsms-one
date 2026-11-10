import React, {useEffect, useState} from 'react';
import {get, post} from './api';
import {useLiveReload} from './live';

const number = (v, digits = 3) => v == null ? 'Unavailable' : Number(v).toFixed(digits);
const percent = v => v == null ? 'Unavailable' : `${(v * 100).toFixed(1)}%`;

function Scores({rows, label = 'Group'}) {
  return <div className="sp-table-scroll"><table className="sp-table"><thead><tr>{[label, 'Selections', 'Distinct fixtures', 'Scored', 'Win rate', 'Brier', 'Log loss', 'Calibration error', 'Singles ROI'].map(x => <th key={x}>{x}</th>)}</tr></thead><tbody>
    {rows?.map(r => <tr key={r.group}><td>{r.group}</td><td>{r.selections}</td><td>{r.distinct_fixtures ?? 'Unavailable'}</td><td>{r.scored}</td><td>{percent(r.win_rate)}</td><td>{number(r.brier_score)}</td><td>{number(r.log_loss)}</td><td>{percent(r.calibration_error)}</td><td>{percent(r.roi)}</td></tr>)}
    {!rows?.length && <tr><td colSpan="9">No eligible settled observations.</td></tr>}
  </tbody></table></div>;
}

function Reliability({score}) {
  const bins = score?.bins || [];
  return <figure style={{margin: 0}}><svg viewBox="0 0 420 300" role="img" aria-label="Calibration: predicted probability against observed win frequency" style={{maxWidth: 540, width: '100%', background: '#f8fafc', borderRadius: 12}}>
    <path d="M50 20V250H390M50 250L390 20" fill="none" stroke="#94a3b8" strokeDasharray="5 5" />
    {[0,.25,.5,.75,1].map(p => <g key={p}><text x={35} y={254-p*230} textAnchor="end" fontSize="12">{p}</text><text x={50+p*340} y="270" textAnchor="middle" fontSize="12">{p}</text></g>)}
    {bins.map(b => <circle key={b.lower} cx={50+b.confidence*340} cy={250-b.frequency*230} r={Math.min(12, 4+Math.sqrt(b.count))} fill="#176b87"><title>{b.count} selections: predicted {percent(b.confidence)}, observed {percent(b.frequency)}</title></circle>)}
    <text x="220" y="295" textAnchor="middle" fontSize="12">Predicted probability</text>
  </svg><figcaption>Observed win frequency on the vertical axis. The diagonal represents perfect calibration. Circle size reflects sample count.</figcaption></figure>;
}

export default function BasketballLossAnalysis() {
  const [data, setData] = useState(null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [key, setKey] = useState(''), [file, setFile] = useState(null), [view, setView] = useState('actual');
  async function load() { try { setData(await get('/basketball-losses')); setError(''); } catch (e) { setError(e.message); } }
  useEffect(() => { load(); }, []);
  useLiveReload(load);
  async function action(importFile) {
    setBusy(true); setError('');
    try {
      const body = importFile ? {records: JSON.parse(await file.text())} : {};
      await post(`/basketball-losses/${importFile ? 'import' : 'investigate'}`, body, key ? {'X-Sports-Admin-Key': key} : {});
      await load();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  const forecast = data?.forecast_audit;
  const groups = view === 'actual' ? {league:data?.actual_by_league,market:data?.actual_by_market,odds:data?.actual_by_odds_range,model:data?.actual_by_model} : {league:forecast?.by_league,market:forecast?.by_market,odds:forecast?.by_odds_range,model:forecast?.by_model};
  return <div className="sp-ops-stack">
    <section className="sp-panel"><h2>Basketball Performance &amp; Loss Analysis</h2><p>{data?.eligibility || 'Loading the basketball release policy…'}</p>
      {error && <p role="alert" className="sp-ops-error">{error}</p>}
      <div className="sp-grid three">{data?.actual_cash?.map(r => <article key={r.currency}><h3>Actual tickets · {r.currency}</h3><p>{r.selections} tickets · {percent(r.win_rate)} ticket win rate</p><strong>Net return {number(r.profit, 2)} · ROI {percent(r.roi)}</strong><p>Stake {number(r.stake, 2)} · Returned {number(r.total_return, 2)}</p></article>)}
        <article><h3>Actual basketball selections</h3><strong>{data?.actual_selections?.binary_selections || 0} settled · {percent(data?.actual_selections?.win_rate)} won</strong><p>{data?.actual_selections?.scored || 0} confirmed prediction links eligible for probability scoring.</p></article>
        <article><h3>Model controls</h3><strong>{data?.quarantines?.length || 0} active quarantines</strong><p>Collection, forecasting and evaluation continue during quarantine.</p></article>
      </div>
      {data?.notes?.map(note => <p className="sp-muted" key={note}>{note}</p>)}
    </section>
    <section className="sp-panel"><h3>Settled betting evidence</h3><div className="sp-table-scroll"><table className="sp-table"><thead><tr>{['Fixture','Market / scope','Odds','Result','Original probability','Attribution / evidence'].map(x=><th key={x}>{x}</th>)}</tr></thead><tbody>
      {data?.settled_selections?.map(r=><tr key={r.id}><td>{r.home} / {r.away}<small style={{display:'block'}}>{r.competition || 'League unresolved'}</small></td><td>{r.market}<small style={{display:'block'}}>{r.period}</small></td><td>{number(r.decimal_odds,2)}</td><td>{r.result}</td><td>{percent(r.probability)}</td><td><details><summary>{r.attribution}</summary><pre style={{whiteSpace:'pre-wrap',maxWidth:500,maxHeight:300,overflow:'auto'}}>{JSON.stringify(r.snapshot,null,2)}</pre></details></td></tr>)}
      {!data?.settled_selections?.length && <tr><td colSpan="6">Import genuine settled SportyBet records to establish actual returns.</td></tr>}
    </tbody></table></div></section>
    <section className="sp-panel"><h3>Performance breakdown</h3><label>Evidence stream <select value={view} onChange={e=>setView(e.target.value)}><option value="actual">Actual placed bets</option><option value="forecast">Stored pre-match forecast replay</option></select></label><p className="sp-muted">Forecast replay contains no actual stake or cash return. Unresolved actual-bet prediction links have no calibration score.</p>
      {Object.entries(groups).map(([name,rows])=><details key={name} open={name==='market'}><summary>By {name}</summary><Scores rows={rows}/></details>)}
    </section>
    <section className="sp-panel"><h3>Calibration evidence</h3><Reliability score={view==='actual'?data?.actual_selections:forecast?.forecast_summary}/><p>{view==='actual'?'Confirmed original bet predictions only.':'Original pre-match forecasts scored against stored official finals.'}</p><Scores label="Observation" rows={data?.trends?.map(t=>({group:t.created_at,...t.metrics}))}/></section>
    <section className="sp-panel"><h3>Quarantines and failure evidence</h3>{data?.quarantines?.map(q=><details key={q.id}><summary>{q.competition} · {q.market_family} · {q.model_id.slice(0,8)} — {q.reason}</summary><pre style={{whiteSpace:'pre-wrap'}}>{q.evidence_json}</pre></details>)}<h4>Feature coverage and associations</h4><Scores rows={forecast?.diagnostics?.map(r=>({...r,group:r.flag}))}/><p className="sp-muted">Missing player, pace, rest or opponent information is reported as missing. These associations do not establish the cause of a loss.</p></section>
    <section className="sp-panel"><h3>Chronological candidate validation</h3>{data?.candidates?.map(c=><details key={c.id} open><summary>{c.recipe_version} · {c.status} · {c.created_at}</summary><p>Dataset {c.dataset_hash}</p>{JSON.parse(c.metrics_json).leagues?.map(l=><div key={l.competition}><h4>{l.competition} · {l.status}</h4><p>Train {l.counts.train} / calibration {l.counts.calibration} / holdout {l.counts.holdout}</p>{l.reason && <p>{l.reason}</p>}{l.markets?.map(m=><div key={m.family}><strong>{m.family} · {m.status}</strong><Scores label="Comparison" rows={[{group:'Candidate',...m.candidate},{group:'Legacy baseline',...m.legacy_baseline}]}/><p>Paired Brier difference, 95% interval: {m.brier_delta_95pct.map(x=>number(x,4)).join(' to ')}</p><ul>{m.release_blockers.map(b=><li key={b}>{b}</li>)}</ul></div>)}</div>)}</details>)}</section>
    <section className="sp-panel"><h3>Import and investigation controls</h3><p>Administrator access is required. Import a JSON array of settled-ticket evidence; duplicate bet IDs cannot overwrite the original record.</p><label>Administrator key <input type="password" autoComplete="off" value={key} onChange={e=>setKey(e.target.value)}/></label><label>Settled ticket JSON <input type="file" accept=".json,application/json" onChange={e=>setFile(e.target.files?.[0])}/></label><div className="sp-actions"><button className="sp-secondary" disabled={busy||!file} onClick={()=>action(true)}>Import evidence</button><button className="sp-primary" disabled={busy} onClick={()=>action(false)}>{busy?'Investigating…':'Run investigation and validation'}</button></div></section>
    <section className="sp-panel"><h3>Audit trail</h3>{data?.events?.map(e=><details key={e.id}><summary>{e.created_at} · {e.action}</summary><pre style={{whiteSpace:'pre-wrap'}}>{e.details_json}</pre></details>)}</section>
  </div>;
}
