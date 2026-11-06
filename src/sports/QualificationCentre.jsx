import {useEffect, useState} from 'react';
import {get, post} from './api';

const STATE_FOR = {
  booking: ['', 'QUALIFIED', 'MONITORING', 'REJECTED', 'AWAITING_DATA', 'EXPIRED', 'SUSPENDED'],
  weekly_draw: ['QUALIFIED', 'MONITORING', ''],
  daily_rollover: ['QUALIFIED', 'MONITORING', ''],
};
const COPY = {
  booking: 'One qualification decision is shared by booking, the Weekly Draw Centre and the Daily Rollover Centre. A ticket or booking code cannot be created from a selection that is not QUALIFIED.',
  weekly_draw: 'Full-time draw selections for the next seven days, scored separately for each sport that can finish level. This centre reads the same decisions as booking and does not qualify anything on its own.',
  daily_rollover: 'Selections whose kickoff is today. This centre reads the same decisions as booking and does not qualify anything on its own.',
};

function percent(value) {
  return value == null || value === '' ? '—' : `${(Number(value) * 100).toFixed(1)}%`;
}

export default function QualificationCentre({product = 'booking', tab = 0}) {
  const [summary, setSummary] = useState(null);
  const [health, setHealth] = useState(null);
  const [payload, setPayload] = useState({items: [], total: 0, page: 1});
  const [page, setPage] = useState(1);
  const [sport, setSport] = useState('');
  const [market, setMarket] = useState('');
  const [competition, setCompetition] = useState('');
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const state = (STATE_FOR[product] || STATE_FOR.booking)[tab] || '';

  useEffect(() => { setPage(1); }, [product, tab, sport, market, competition]);

  useEffect(() => {
    let cancel = false;
    async function load() {
      try {
        const params = new URLSearchParams({page: String(page), page_size: '25'});
        if (product !== 'booking') params.set('product', product);
        if (state) params.set('state', state);
        if (sport) params.set('sport', sport);
        if (market) params.set('market', market);
        if (competition) params.set('competition', competition);
        const [cards, models, decisions] = await Promise.all([
          get('/qualification/summary'),
          get('/qualification/model-health'),
          get('/qualification/decisions?' + params.toString()),
        ]);
        if (!cancel) {
          setSummary(cards);
          setHealth(models);
          setPayload(decisions);
          setError('');
        }
      } catch (requestError) {
        if (!cancel) setError(requestError.message);
      }
    }
    load();
    const timer = setInterval(load, 10000);
    return () => { cancel = true; clearInterval(timer); };
  }, [product, state, sport, market, competition, page, revision]);

  async function revalidate() {
    setBusy(true);
    try {
      await post('/qualification/revalidate', {});
      setRevision(value => value + 1);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }

  const counts = summary?.counts || {};
  const pages = Math.max(1, Math.ceil((payload.total || 0) / 25));
  return <div className="sp-ops-stack">
    <p className="sp-muted">{COPY[product]}</p>
    {error && <p className="sp-ops-error" role="alert">{error}</p>}
    <div className="sp-kpi-grid">
      {['QUALIFIED', 'MONITORING', 'REJECTED', 'AWAITING_DATA', 'EXPIRED', 'SUSPENDED'].map(name => <article className="sp-kpi" key={name}><span>{name}</span><strong>{counts[name] || 0}</strong></article>)}
    </div>
    <div className="sp-filter-bar">
      <label>Sport<select value={sport} onChange={event => setSport(event.target.value)}><option value="">All</option><option value="football">Football</option><option value="basketball">Basketball</option></select></label>
      <label>Market<input value={market} onChange={event => setMarket(event.target.value)} placeholder="HOME, DRAW, AWAY"/></label>
      <label>Competition<input value={competition} onChange={event => setCompetition(event.target.value)} placeholder="League"/></label>
      <button className="sp-primary" type="button" onClick={revalidate} disabled={busy}>{busy ? 'Checking…' : 'Revalidate stored selections'}</button>
      <span>Last check {summary?.last_run?.evaluated_at ? new Date(summary.last_run.evaluated_at).toLocaleString() : 'not run'}</span>
    </div>
    <section className="sp-panel">
      <div className="sp-table-scroll"><table className="sp-table"><thead><tr>{['Fixture', 'Market', 'State', 'Reason code', 'Estimated edge', 'Products', 'Evidence'].map(heading => <th key={heading}>{heading}</th>)}</tr></thead><tbody>
        {(payload.items || []).map(row => <tr key={row.id}><td>{row.home || '—'} vs {row.away || '—'}</td><td>{row.market}</td><td><span className={`sp-status ${row.state === 'QUALIFIED' ? 'success' : row.state === 'SUSPENDED' || row.state === 'REJECTED' ? 'warning' : ''}`}>{row.state}</span></td><td>{row.reason_code}</td><td>{row.rank_score == null ? '—' : percent(row.rank_score)}</td><td>{(row.products || []).join(', ') || '—'}</td><td><button className="sp-secondary" type="button" onClick={() => setSelected(row)}>View</button></td></tr>)}
        {!payload.items?.length && <tr><td className="sp-empty" colSpan="7">No stored qualification decision matches this filter.</td></tr>}
      </tbody></table></div>
      <div className="sp-pager"><span>{payload.total || 0} decisions</span><button className="sp-secondary" type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button><span>Page {page} of {pages}</span><button className="sp-secondary" type="button" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button></div>
    </section>
    <section className="sp-panel"><h3>Model health</h3><p className="sp-muted">An inactive, uncalibrated or deteriorating model cannot produce a QUALIFIED selection.</p><div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>Model</th><th>Lifecycle</th><th>Calibration error</th><th>Holdout</th><th>Brier</th><th>Blocks qualification</th></tr></thead><tbody>
      {(health?.models || []).map(model => <tr key={model.id}><td>{model.model_name} v{model.version} · {model.sport}</td><td>{model.lifecycle}</td><td>{model.calibration_error == null ? '—' : Number(model.calibration_error).toFixed(3)}</td><td>{model.holdout_fixtures ?? '—'}</td><td>{model.brier_score == null ? '—' : Number(model.brier_score).toFixed(3)}</td><td>{model.blocks_qualification ? 'yes' : 'no'}</td></tr>)}
      {!health?.models?.length && <tr><td className="sp-empty" colSpan="6">No model registry row is stored.</td></tr>}
    </tbody></table></div></section>
    {selected && <div className="sp-intel-overlay" onMouseDown={event => { if (event.target === event.currentTarget) setSelected(null); }}><section className="sp-panel" role="dialog" aria-modal="true"><button className="sp-secondary" type="button" onClick={() => setSelected(null)}>Close</button><h3>{selected.home} vs {selected.away}</h3><p>{selected.reason}</p><p><b>Reason code:</b> {selected.reason_code}</p><p><b>Policy version:</b> {selected.policy_version} · <b>Decision version:</b> {selected.decision_version}</p><p><b>Model probability:</b> {percent(selected.evidence?.probability)} · <b>Implied probability:</b> {percent(selected.evidence?.implied_probability)} · <b>Uncertainty:</b> {selected.evidence?.uncertainty ?? '—'}</p><p><b>Missing:</b> {(selected.evidence?.missing || []).join(', ') || 'none'}</p>{selected.evidence?.lineups == null && <p>No lineup was stored. Nothing was invented.</p>}</section></div>}
  </div>;
}
