import {useEffect, useState} from 'react';
import {useLiveReload} from './live';
import {get, post} from './api';
import {ModelManagement} from './Intelligence';

function percent(value) {
  return value == null || value === '' ? '—' : `${(Number(value) * 100).toFixed(1)}%`;
}

function decimal(value, digits = 3) {
  return value == null || value === '' ? '—' : Number(value).toFixed(digits);
}

function query(filters) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => { if (value) params.set(key, value); });
  const text = params.toString();
  return text ? `?${text}` : '';
}

export default function PerformanceLearning({page, tab = 0}) {
  const [filters, setFilters] = useState({sport: '', market: '', competition: '', model_version: '', date_from: '', date_to: ''});
  const [report, setReport] = useState(null);
  const [runs, setRuns] = useState({runs: [], controls: []});
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  function load(next = filters) {
    setError('');
    return Promise.all([
      get(`/learning/report${query(next)}`),
      get('/learning/runs'),
    ]).then(([reportValue, runValue]) => {
      setReport(reportValue);
      setRuns(runValue);
    }).catch(requestError => setError(requestError.message));
  }
  useEffect(() => { load(); }, []);
  useLiveReload(() => load());
  async function refreshEvaluation() {
    setBusy(true);
    setError('');
    try {
      await post('/learning/refresh', {});
      await load();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }
  async function download(name) {
    const response = await fetch(`/api/sports-ai/learning/export${query({...filters, report: name})}`, {credentials: 'include'});
    if (!response.ok) {
      setError('The export could not be downloaded.');
      return;
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `cacsms-${name}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  }
  return <div className="sp-ops-stack">
    <p className="sp-muted">{report?.note || 'Selection accuracy is scored only from a prediction stored before kickoff and a final result stored afterwards.'}</p>
    {error && <p className="sp-ops-error" role="alert">{error}</p>}
    <Filters filters={filters} onChange={next => { setFilters(next); load(next); }} />
    {page === 'results' && tab === 0 && <SelectionBoard report={report} />}
    {page === 'results' && tab === 1 && <Queue report={report} />}
    {page === 'results' && tab === 2 && <Exceptions report={report} />}
    {page === 'backtesting' && tab === 0 && <Runs runs={runs} busy={busy} onRefresh={refreshEvaluation} />}
    {page === 'backtesting' && tab === 1 && <Evaluation report={report} />}
    {page === 'backtesting' && tab === 2 && <Strategies report={report} onExport={download} />}
    {page === 'models' && tab === 0 && <Calibration report={report} />}
    {page === 'models' && tab === 1 && <MetricTable report={report} metric="brier_score" label="Brier score" />}
    {page === 'models' && tab === 2 && <MetricTable report={report} metric="log_loss" label="Log loss" />}
    {page === 'models' && tab === 3 && <Versions report={report} runs={runs} />}
  </div>;
}

function Filters({filters, onChange}) {
  const [draft, setDraft] = useState(filters);
  function update(key, value) { setDraft(current => ({...current, [key]: value})); }
  return <form className="sp-filter-bar" onSubmit={event => { event.preventDefault(); onChange(draft); }}>
    <label>Sport<select value={draft.sport} onChange={event => update('sport', event.target.value)}><option value="">All</option><option value="football">Football</option><option value="basketball">Basketball</option></select></label>
    <label>Market<input value={draft.market} onChange={event => update('market', event.target.value)} placeholder="HOME" /></label>
    <label>Competition<input value={draft.competition} onChange={event => update('competition', event.target.value)} /></label>
    <label>Model version<input value={draft.model_version} onChange={event => update('model_version', event.target.value)} /></label>
    <label>From<input type="date" value={draft.date_from.slice(0, 10)} onChange={event => update('date_from', event.target.value ? `${event.target.value}T00:00:00+00:00` : '')} /></label>
    <label>To<input type="date" value={draft.date_to.slice(0, 10)} onChange={event => update('date_to', event.target.value ? `${event.target.value}T23:59:59+00:00` : '')} /></label>
    <button className="sp-secondary" type="submit">Apply filters</button>
  </form>;
}

function SelectionBoard({report}) {
  const score = report?.selections;
  return <>
    <div className="sp-kpi-grid">
      <article className="sp-kpi"><span>Settled selections</span><strong>{score?.sample_size ?? '—'}</strong></article>
      <article className="sp-kpi"><span>Accuracy</span><strong>{percent(score?.accuracy)}</strong></article>
      <article className="sp-kpi"><span>Brier</span><strong>{decimal(score?.brier_score)}</strong></article>
      <article className="sp-kpi"><span>Log loss</span><strong>{decimal(score?.log_loss)}</strong></article>
      <article className="sp-kpi"><span>Expected return</span><strong>{percent(score?.expected_return)}</strong></article>
      <article className="sp-kpi"><span>Realized return</span><strong>{percent(score?.realized_return)}</strong></article>
      <article className="sp-kpi"><span>Max drawdown</span><strong>{decimal(score?.max_drawdown, 2)}</strong></article>
      <article className="sp-kpi"><span>Ticket win rate</span><strong>{percent(report?.tickets?.win_rate)}</strong></article>
    </div>
    <p className="sp-muted">{score?.note} Priced sample {score?.priced_sample ?? 0}. Stored uncertainty {decimal(score?.uncertainty)}. Probability dispersion {decimal(score?.probability_dispersion)}. Predictions stored at or after kickoff are excluded: {report?.exceptions?.leakage ?? 0}.</p>
    <Equity points={report?.equity?.points || []} />
    <StoredValidation rows={report?.stored_validation || []} />
  </>;
}

function Queue({report}) {
  const items = report?.queue?.items || [];
  return <section className="sp-panel"><h3>Awaiting a verified final</h3><p className="sp-muted">{report?.queue?.total ?? 0} predictions have no final result yet.</p>
    <div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>Fixture</th><th>Sport</th><th>Market</th><th>Kickoff</th></tr></thead><tbody>
      {items.map(row => <tr key={`${row.fixture_id}-${row.market}`}><td>{row.fixture}</td><td>{row.sport}</td><td>{row.market}</td><td>{row.kickoff}</td></tr>)}
      {!items.length && <tr><td className="sp-empty" colSpan="4">No unsettled prediction is waiting.</td></tr>}
    </tbody></table></div></section>;
}

function Exceptions({report}) {
  const items = report?.exceptions?.items || [];
  return <section className="sp-panel"><h3>Excluded records</h3><p className="sp-muted">Leakage {report?.exceptions?.leakage ?? 0}. Unsupported or non-binary markets {report?.exceptions?.unsupported_market ?? 0}. Showing {items.length} of {report?.exceptions?.total ?? 0}.</p>
    <div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>Fixture</th><th>Market</th><th>Reason</th></tr></thead><tbody>
      {items.map((row, index) => <tr key={`${row.fixture_id}-${index}`}><td>{row.fixture_id}</td><td>{row.market}</td><td>{row.reason}</td></tr>)}
      {!items.length && <tr><td className="sp-empty" colSpan="3">No excluded prediction is stored.</td></tr>}
    </tbody></table></div></section>;
}

function Runs({runs, busy, onRefresh}) {
  return <>
    <div className="sp-filter-bar"><button className="sp-primary" type="button" disabled={busy} onClick={onRefresh}>{busy ? 'Scoring…' : 'Refresh evaluation'}</button></div>
    <p className="sp-muted">One stored run is kept for each day. Refreshing scores settled results and can suspend a deteriorating active model. It does not activate a model.</p>
    <section className="sp-panel"><div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>Date</th><th>Status</th><th>Selections</th><th>Tickets</th><th>Leakage excluded</th></tr></thead><tbody>
      {(runs.runs || []).map(row => <tr key={row.id}><td>{row.run_date}</td><td>{row.status}</td><td>{row.selection_sample}</td><td>{row.ticket_sample}</td><td>{row.leakage_excluded}</td></tr>)}
      {!runs.runs?.length && <tr><td className="sp-empty" colSpan="5">No evaluation run is stored yet.</td></tr>}
    </tbody></table></div></section>
    <section className="sp-panel"><h3>Controls</h3><div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>When</th><th>Action</th><th>Status</th><th>Reason</th></tr></thead><tbody>
      {(runs.controls || []).map((row, index) => <tr key={`${row.model_id}-${index}`}><td>{row.created_at}</td><td>{row.action}</td><td>{row.status}</td><td>{row.reason}</td></tr>)}
      {!runs.controls?.length && <tr><td className="sp-empty" colSpan="4">No suspension or retraining recommendation is stored.</td></tr>}
    </tbody></table></div></section>
  </>;
}

function Evaluation({report}) {
  const folds = report?.walk_forward?.folds || [];
  return <>
    <p className="sp-muted">{report?.walk_forward?.note}</p>
    <section className="sp-panel"><div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>Fold</th><th>From</th><th>To</th><th>Sample</th><th>Brier</th><th>Log loss</th><th>Accuracy</th><th>Calibration</th></tr></thead><tbody>
      {folds.map(row => <tr key={row.fold}><td>{row.fold}</td><td>{row.from || '—'}</td><td>{row.to || '—'}</td><td>{row.sample_size}</td><td>{decimal(row.brier_score)}</td><td>{decimal(row.log_loss)}</td><td>{percent(row.accuracy)}</td><td>{decimal(row.calibration_error)}</td></tr>)}
    </tbody></table></div></section>
    <CalibrationChart bins={report?.calibration || []} />
    <Equity points={report?.equity?.points || []} />
    <StoredValidation rows={report?.stored_validation || []} />
  </>;
}

function Strategies({report, onExport}) {
  return <>
    <div className="sp-filter-bar">
      {['selections', 'tickets', 'tiers', 'draws', 'rollover', 'models'].map(name => <button className="sp-secondary" type="button" key={name} onClick={() => onExport(name)}>Export {name}</button>)}
    </div>
    <section className="sp-panel"><h3>Odds tiers</h3><p className="sp-muted">Ticket rows are settled accumulator tickets. Selection rows use the pre-kickoff single price.</p>
      <SimpleTable columns={['Tier', 'Settled', 'Win rate', 'Profit', 'Drawdown']} rows={(report?.tiers || []).map(row => [row.label, row.sample_size, percent(row.win_rate), decimal(row.hypothetical_profit, 2), decimal(row.max_drawdown, 2)])} /></section>
    <section className="sp-panel"><h3>Selection price bands</h3>
      <SimpleTable columns={['Band', 'Sample', 'Accuracy', 'Expected', 'Realized', 'Drawdown']} rows={(report?.selection_tiers || []).map(row => [row.label, row.sample_size, percent(row.accuracy), percent(row.expected_return), percent(row.realized_return), decimal(row.max_drawdown, 2)])} /></section>
    <section className="sp-panel"><h3>Draw tickets</h3><p className="sp-muted">{report?.draws?.note}</p>
      <SimpleTable columns={['Mode', 'Settled tickets', 'Ticket win rate']} rows={(report?.draws?.by_mode || []).map(row => [row.mode, row.sample_size, percent(row.win_rate)])} />
      <SimpleTable columns={['Mode', 'Picks', 'Tickets', 'Ticket win rate', 'Leg accuracy']} rows={(report?.draws?.by_size || []).map(row => [row.mode, row.picks, row.sample_size, percent(row.win_rate), percent(row.leg_accuracy)])} /></section>
    <section className="sp-panel"><h3>30-day rollover</h3>
      <SimpleTable columns={['Campaign', 'Mode', 'Status', 'Realized', 'Drawdown', 'Skipped', 'Path probability']} rows={(report?.rollover || []).map(row => [row.name, row.mode === 'FULL_ROLLOVER_SIMULATION' ? 'Full rollover' : 'Capital controlled', row.status, percent(row.realized_return), percent(row.max_drawdown), row.skipped, percent(row.path_probability)])} />
      <p className="sp-muted">{report?.rollover?.[0]?.note || 'A campaign path uses stored selection probabilities only.'}</p></section>
  </>;
}

function Calibration({report}) {
  return <>
    <div className="sp-kpi-grid">
      <article className="sp-kpi"><span>Calibration error</span><strong>{decimal(report?.selections?.calibration_error)}</strong></article>
      <article className="sp-kpi"><span>Sample</span><strong>{report?.selections?.sample_size ?? '—'}</strong></article>
    </div>
    <CalibrationChart bins={report?.selections?.bins || report?.calibration || []} />
    <StoredValidation rows={report?.stored_validation || []} />
  </>;
}

function MetricTable({report, metric, label}) {
  return <section className="sp-panel"><h3>{label} by model version</h3>
    <SimpleTable columns={['Model', 'Version', 'Sport', 'Sample', label, 'Accuracy']} rows={(report?.models?.versions || []).map(row => [`${row.model_name}`, row.version, row.sport, row.sample_size, decimal(row[metric]), percent(row.accuracy)])} /></section>;
}

function Versions({report, runs}) {
  return <>
    <p className="sp-muted">A challenger with a lower settled Brier score is reported here. Activation remains on the registry below and still requires held-out evidence plus an administrator. Training does not activate a model.</p>
    {(report?.models?.comparisons || []).map(row => <p key={row.sport}>{row.sport}: champion {row.champion || '—'}, challenger {row.challenger || '—'}. {row.recommendation}</p>)}
    {(report?.models?.deterioration || []).filter(row => row.deteriorated).map(row => <p className="sp-ops-error" key={row.model_id} role="status">{row.version} deteriorated. Recent Brier {decimal(row.recent_brier)} against prior {decimal(row.prior_brier)}.</p>)}
    <section className="sp-panel"><h3>Recent controls</h3>
      <SimpleTable columns={['Action', 'Status', 'Reason']} rows={(runs.controls || []).slice(0, 8).map(row => [row.action, row.status, row.reason])} /></section>
    <ModelManagement />
  </>;
}

function StoredValidation({rows}) {
  const populated = rows.filter(row => row.brier_score != null);
  if (!populated.length) return null;
  return <section className="sp-panel"><h3>Stored training holdout</h3><p className="sp-muted">{populated[0].note}</p>
    <SimpleTable columns={['Model', 'Version', 'Holdout', 'Walk-forward folds', 'Brier', 'Log loss', 'Calibration']} rows={populated.map(row => [row.model_name, row.version, row.holdout_fixtures ?? '—', row.walk_forward_folds ?? '—', decimal(row.brier_score), decimal(row.log_loss), decimal(row.calibration_error)])} /></section>;
}

function CalibrationChart({bins}) {
  if (!bins.length) return <p className="sp-muted">Calibration bins appear after a settled pre-kickoff sample exists.</p>;
  return <section className="sp-panel"><h3>Calibration</h3><div className="sp-reliability" aria-label="Calibration chart">{bins.map(bin => <div key={bin.lower} className="sp-reliability-row"><span>{percent(bin.lower)}</span><div className="sp-reliability-track"><i style={{width: `${Math.min(100, bin.confidence * 100)}%`}} /><b style={{width: `${Math.min(100, bin.frequency * 100)}%`}} /></div><small>n={bin.count}</small></div>)}</div><p className="sp-intel-fine">Navy bar: mean stored probability. Blue bar: observed frequency.</p></section>;
}

function Equity({points}) {
  if (!points.length) return <p className="sp-muted">No pre-kickoff price is stored, so a return curve is not drawn.</p>;
  const values = points.map(point => point.equity);
  const low = Math.min(...values, 0);
  const high = Math.max(...values, 0);
  const span = high - low || 1;
  return <section className="sp-panel"><h3>Hypothetical return</h3><div className="sp-reliability" aria-label="Hypothetical return curve">{points.slice(-40).map((point, index) => <div key={`${point.kickoff}-${index}`} className="sp-reliability-row"><span>{point.kickoff.slice(5)}</span><div className="sp-reliability-track"><i style={{width: `${Math.max(2, ((point.equity - low) / span) * 100)}%`}} /></div></div>)}</div><p className="sp-intel-fine">{points.length} stored points. The last 40 are shown. This is not a forecast.</p></section>;
}

function SimpleTable({columns, rows}) {
  return <div className="sp-table-scroll"><table className="sp-table"><thead><tr>{columns.map(column => <th key={column}>{column}</th>)}</tr></thead><tbody>
    {rows.map((row, index) => <tr key={index}>{row.map((value, cell) => <td key={cell}>{value ?? '—'}</td>)}</tr>)}
    {!rows.length && <tr><td className="sp-empty" colSpan={columns.length}>No settled rows are stored for this report.</td></tr>}
  </tbody></table></div>;
}
