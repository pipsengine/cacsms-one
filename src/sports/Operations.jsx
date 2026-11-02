import React, { useEffect, useState } from 'react';
import { Plus, RefreshCw, Save, ShieldAlert } from 'lucide-react';
import { get, post, put, remove } from './api';
import './operations.css';

const tables = {
  results: { path: 'results', title: 'Final match results', cols: ['home', 'away', 'home_score', 'away_score', 'source', 'settled_at'] },
  backtesting: { path: 'performance', title: 'Historical evaluation', cols: [] },
  models: { path: 'model-versions', title: 'Registered model versions', cols: ['model_name', 'sport', 'version', 'status', 'details_json', 'created_at'] },
  'prediction-accuracy': { path: 'performance', title: 'Prediction accuracy', cols: [] },
  'performance-reports': { path: 'performance', title: 'Performance report', cols: [] },
  calibration: { path: 'performance', title: 'Confidence and calibration evidence', cols: [] },
  'football-teams': { path: 'teams?sport=football', title: 'Football team intelligence', cols: ['team', 'appearances', 'played', 'wins', 'draws', 'losses', 'average_xg', 'average_points', 'sources'] },
  'basketball-teams': { path: 'teams?sport=basketball', title: 'Basketball team intelligence', cols: ['team', 'appearances', 'played', 'wins', 'draws', 'losses', 'average_points', 'sources'] },
  'football-markets': { path: 'odds-history?sport=football', title: 'Football market history', cols: ['fixture_id', 'market', 'decimal_odds', 'source', 'observed_at'] },
  'basketball-points-spreads': { path: 'predictions?sport=basketball', title: 'Basketball points and spreads', cols: ['fixture', 'market', 'odds', 'probability', 'edge', 'model_name'] },
  engine: { path: 'analysis-runs', title: 'Intelligence engine runs', cols: ['id', 'created_at', 'fixture_count', 'selection_count', 'ticket_count', 'status', 'details_json'] },
  'ticket-history': { path: 'tickets', title: 'Accumulator ticket history', cols: ['id', 'tier', 'combined_odds', 'estimated_probability', 'estimated_ev', 'selection_count', 'status'] },
  'integration-monitor': { path: 'health', title: 'Sports AI integration status', cols: [] },
  'workflow-engine': { path: 'jobs', title: 'Workflow execution history', cols: ['id', 'schedule_id', 'started_at', 'finished_at', 'status', 'details_json'] },
  'collection-monitor': { path: 'sources', title: 'Data source collection readiness', cols: ['name', 'sport', 'kind', 'enabled', 'notes'] },
  'system-config': { path: 'settings', title: 'Sports AI system settings', cols: [] },
  'audit-trail': { path: 'activity', title: 'Sports AI audit trail', cols: ['actor', 'action', 'metadata_json', 'created_at'] },
  odds: { path: 'odds-history', title: 'Recorded bookmaker price history', cols: ['fixture_id', 'market', 'decimal_odds', 'source', 'observed_at'] },
  'market-scanner': { path: 'watchlist', title: 'Watched fixtures', cols: ['home', 'away', 'sport', 'note', 'starts_at'] },
  activity: { path: 'analysis-runs', title: 'Analysis activity', cols: ['id', 'created_at', 'fixture_count', 'selection_count', 'ticket_count', 'status'] },
  sources: { path: 'sources', title: 'Data source registry', cols: ['name', 'sport', 'kind', 'enabled', 'notes'] },
  scheduler: { path: 'schedules', title: 'Scheduled analysis definitions', cols: ['name', 'interval_minutes', 'enabled', 'last_run_at'] },
  alerts: { path: 'alert-rules', title: 'Notification rules', cols: ['event_type', 'channel', 'enabled', 'created_at'] },
  users: { path: 'roles', title: 'Portal role templates', cols: ['role_name', 'permissions_json'] },
  codes: { path: 'tickets', title: 'Ticket booking-code register', cols: ['id', 'tier', 'combined_odds', 'booking_code', 'booking_status'] },
  'code-history': { path: 'booking-code-history', title: 'Booking-code history', cols: ['code', 'ticket_id', 'tier', 'combined_odds', 'status', 'recorded_by', 'created_at'] },
  betslip: { path: 'tickets', title: 'Betslip preparation queue', cols: ['id', 'tier', 'combined_odds', 'selection_count', 'status'] },
  tickets: { path: 'tickets', title: 'Candidate accumulator tickets', cols: ['id', 'tier', 'combined_odds', 'estimated_probability', 'estimated_ev', 'selection_count'] },
  tiers: { path: 'tickets', title: 'Target odds tiers', cols: ['id', 'tier', 'combined_odds', 'estimated_probability', 'selection_count'] },
  optimizer: { path: 'tickets', title: 'Optimized ticket candidates', cols: ['id', 'tier', 'combined_odds', 'estimated_ev', 'selection_count'] },
  monitor: { path: 'health', title: 'Sports AI service health', cols: [] },
};

const inputs = {
  results: [['fixture_id', 'Fixture ID', 'text'], ['home_score', 'Home score', 'number'], ['away_score', 'Away score', 'number'], ['source', 'Result source', 'text']],
  'market-scanner': [['fixture_id', 'Fixture ID', 'text'], ['note', 'Watchlist note', 'text']],
  sources: [['name', 'Provider name', 'text'], ['sport', 'Sport', 'select'], ['kind', 'Source type', 'select'], ['enabled', 'Enable this source', 'checkbox'], ['notes', 'Notes', 'text']],
  scheduler: [['name', 'Schedule name', 'text'], ['interval_minutes', 'Interval in minutes', 'number'], ['enabled', 'Enable this schedule', 'checkbox']],
  alerts: [['event_type', 'Event type', 'select'], ['channel', 'Channel', 'select'], ['enabled', 'Enable this rule', 'checkbox']],
  users: [['role_name', 'Role name', 'text'], ['permissions', 'Permissions, comma-separated', 'text']],
  codes: [['ticket_id', 'Ticket ID', 'text'], ['code', 'External booking code', 'text'], ['evidence', 'Evidence of external retrieval', 'text']],
};

const choices = {
  sport: ['football', 'basketball', 'both'],
  kind: ['manual', 'api', 'file'],
  event_type: ['analysis', 'value', 'code', 'result'],
  channel: ['dashboard', 'email', 'telegram'],
};

function endpointFor(id, tab, fallback) {
  const tiers = [2, 5, 10, 25, 100, 1000, 5000, 25000];
  if (id === 'alerts' && tab === 1) return 'notifications';
  if (id === 'models' && tab === 1) return 'performance';
  if (id === 'tiers') return `tickets?tier=${tiers[tab]}`;
  if (id === 'ticket-history' && tab === 1) return 'tickets?sport=football';
  if (id === 'ticket-history' && tab === 2) return 'tickets?sport=basketball';
  if (id === 'code-history' && tab === 1) return 'booking-code-history?status=verified';
  if (id === 'code-history' && tab === 2) return 'booking-code-history?status=unverified';
  if (id === 'market-scanner' && tab === 1) return 'watchlist?sport=football';
  if (id === 'market-scanner' && tab === 2) return 'watchlist?sport=basketball';
  if (id === 'activity' && tab === 0) return 'activity';
  if (id === 'activity' && tab === 1) return 'analysis-runs';
  if (id === 'activity' && tab === 2) return 'jobs?status=failed';
  if (id === 'engine' && tab === 1) return 'predictions';
  if (id === 'engine' && tab === 2) return 'tickets';
  if (id === 'scheduler' && tab === 1) return 'jobs';
  if (id === 'scheduler' && tab === 2) return 'jobs?status=failed';
  if (id === 'workflow-engine' && tab === 1) return 'schedules';
  if (id === 'workflow-engine' && tab === 2) return 'jobs?status=failed';
  if (id === 'collection-monitor' && tab === 2) return 'health';
  return fallback;
}

function columnsFor(id, tab, defaults) {
  if (id === 'activity' && tab === 0) return ['actor', 'action', 'metadata_json', 'created_at'];
  if ((id === 'activity' && tab === 2) || (id === 'scheduler' && tab > 0) || (id === 'workflow-engine' && tab !== 1)) {
    return ['id', 'schedule_id', 'started_at', 'finished_at', 'status', 'details_json'];
  }
  if (id === 'activity' && tab === 1) return ['id', 'created_at', 'fixture_count', 'selection_count', 'ticket_count', 'status'];
  if (id === 'engine' && tab === 1) return ['fixture', 'sport', 'market', 'odds', 'probability', 'edge', 'model_name'];
  if (id === 'engine' && tab === 2) return ['id', 'tier', 'combined_odds', 'estimated_probability', 'estimated_ev', 'selection_count', 'status'];
  if (id === 'workflow-engine' && tab === 1) return ['name', 'interval_minutes', 'enabled', 'last_run_at'];
  return defaults;
}

function Listing({ data, cols, onRemove }) {
  if (!Array.isArray(data) || !data.length) {
    return <div className="sp-emptybox">No records have been stored yet. Import source-backed data or create a record using the form below.</div>;
  }
  return (
    <div className="sp-table-scroll">
      <table className="sp-table">
        <thead><tr>{cols.map((column) => <th key={column}>{column.replaceAll('_', ' ')}</th>)}{onRemove && <th>Actions</th>}</tr></thead>
        <tbody>
          {data.map((row, index) => (
            <tr key={row.id || index}>
              {cols.map((column) => <td key={column}>{typeof row[column] === 'object' ? JSON.stringify(row[column]) : String(row[column] ?? '—').slice(0, 160)}</td>)}
              {onRemove && <td><button className="sp-text-action" onClick={() => onRemove(row.fixture_id)}>Remove</button></td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RecordForm({ id, refresh }) {
  const [values, setValues] = useState({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const fields = inputs[id];
  if (!fields) return null;

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setMessage('');
    try {
      const payload = { ...values };
      if (id === 'users') payload.permissions = (values.permissions || '').split(',').map((item) => item.trim()).filter(Boolean);
      if (id === 'results') {
        payload.home_score = Number(values.home_score);
        payload.away_score = Number(values.away_score);
      }
      if (id === 'scheduler') payload.interval_minutes = Number(values.interval_minutes);
      if (id === 'codes') {
        const ticketId = payload.ticket_id;
        delete payload.ticket_id;
        payload.verification_method = 'manual';
        await post(`/tickets/${encodeURIComponent(ticketId)}/booking-code`, payload);
      } else {
        const endpoint = { 'market-scanner': 'watchlist', alerts: 'alert-rules', scheduler: 'schedules', users: 'roles' }[id] || id;
        await post(`/${endpoint}`, payload);
      }
      setMessage(id === 'codes' ? 'Saved as unverified. External verification is still required.' : 'Saved successfully.');
      setValues({});
      await refresh();
    } catch (error) {
      setMessage(`Unable to save: ${error.message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="sp-ops-form" onSubmit={submit}>
      <h3>{id === 'codes' ? 'Record an externally obtained booking code' : 'Create or update record'}</h3>
      <div className="sp-form-grid">
        {fields.map(([key, label, type]) => (
          <label key={key}>
            {label}
            {type === 'checkbox'
              ? <input type="checkbox" checked={Boolean(values[key])} onChange={(event) => setValues({ ...values, [key]: event.target.checked })} />
              : type === 'select'
                ? <select required value={values[key] ?? ''} onChange={(event) => setValues({ ...values, [key]: event.target.value })}><option value="" disabled>Select…</option>{choices[key].map((choice) => <option key={choice} value={choice}>{choice}</option>)}</select>
                : <input required type={type} min={type === 'number' ? 0 : undefined} value={values[key] ?? ''} onChange={(event) => setValues({ ...values, [key]: event.target.value })} />}
          </label>
        ))}
      </div>
      <button className="sp-primary" disabled={busy}><Save size={16} />{busy ? 'Saving…' : 'Save Record'}</button>
      {message && <p role="status">{message}</p>}
    </form>
  );
}

function ModelSettings() {
  const [values, setValues] = useState({ min_edge: 0.05, max_legs: 6, odds_tolerance: 0.15 });
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    get('/settings').then(setValues).catch((error) => setMessage(error.message));
  }, []);

  async function save() {
    setBusy(true);
    try {
      setValues(await put('/settings', values));
      setMessage('Settings saved. They apply to subsequent analysis runs.');
    } catch (error) {
      setMessage(`Unable to save settings: ${error.message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="sp-panel">
      <h3>Prediction and accumulator parameters</h3>
      <p className="sp-muted">These settings apply to the statistical baseline. They do not represent a trained or calibrated model.</p>
      <div className="sp-form-grid">
        {[['min_edge', 'Minimum estimated edge', 0.01], ['max_legs', 'Maximum accumulator legs', 1], ['odds_tolerance', 'Target odds tolerance', 0.01]].map(([key, label, step]) => (
          <label key={key}>{label}<input type="number" min={key === 'max_legs' ? 1 : 0} max={key === 'max_legs' ? 8 : key === 'min_edge' ? 1 : 0.5} step={step} value={values[key]} onChange={(event) => setValues({ ...values, [key]: Number(event.target.value) })} /></label>
        ))}
      </div>
      <button className="sp-primary" onClick={save} disabled={busy}><Save size={16} />{busy ? 'Saving…' : 'Save engine settings'}</button>
      {message && <p role="status">{message}</p>}
    </section>
  );
}

function FixtureImport({ refresh }) {
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');

  async function submit() {
    setBusy(true);
    setMessage('');
    try {
      const payload = JSON.parse(await file.text());
      if (!Array.isArray(payload)) throw Error('The JSON file must contain an array of fixture records.');
      const result = await post('/fixtures/import', payload);
      setMessage(`${result.imported} fixtures imported. Supply genuine data and timezone-aware timestamps.`);
      await refresh();
    } catch (error) {
      setMessage(`Import failed: ${error.message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="sp-panel">
      <h3>Import source-backed fixtures</h3>
      <p className="sp-muted">Upload a JSON array using the API fixture contract. Include real source names, timezone-aware kickoff and observation timestamps, supported statistics, and actual market odds.</p>
      <div className="sp-form-grid"><label>Fixture JSON<input type="file" accept=".json,application/json" onChange={(event) => setFile(event.target.files?.[0] || null)} /></label></div>
      <button className="sp-primary" disabled={!file || busy} onClick={submit}><Plus size={16} />{busy ? 'Importing…' : 'Import fixtures'}</button>
      {message && <p role="status">{message}</p>}
    </section>
  );
}

function Performance({ data }) {
  const metrics = [
    ['Evaluated predictions', data?.evaluated_predictions],
    ['Hit rate', data?.hit_rate == null ? 'Not available' : `${(data.hit_rate * 100).toFixed(2)}%`],
    ['Brier score', data?.brier_score == null ? 'Not available' : data.brier_score.toFixed(4)],
    ['Log loss', data?.log_loss == null ? 'Not available' : data.log_loss.toFixed(4)],
  ];
  return <div className="sp-performance-grid">{metrics.map(([label, value]) => <div className="sp-performance-card" key={label}><small>{label}</small><strong>{value ?? '—'}</strong></div>)}</div>;
}

export default function Operations({ id, tab = 0 }) {
  const [data, setData] = useState([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const config = tables[id];

  async function refresh() {
    if (!config) return;
    setBusy(true);
    try {
      setData(await get(`/${endpointFor(id, tab, config.path)}`));
      setError('');
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => { refresh(); }, [id, tab]);
  if (id === 'settings' || id === 'system-config') return <ModelSettings />;
  if (!config) return null;

  const isPerformance = ['backtesting', 'prediction-accuracy', 'performance-reports', 'calibration'].includes(id) || (id === 'models' && tab === 1);
  const isHealth = ['integration-monitor', 'monitor'].includes(id) || (id === 'collection-monitor' && tab === 2);
  const recordFormTab = id === 'results' || id === 'codes' ? 1 : 0;
  const onRemove = id === 'market-scanner' ? async (fixtureId) => {
    if (!window.confirm('Remove this fixture from the watchlist?')) return;
    try {
      await remove(`/watchlist/${encodeURIComponent(fixtureId)}`);
      await refresh();
    } catch (requestError) {
      setError(requestError.message);
    }
  } : null;

  return (
    <div className="sp-ops-stack">
      {id === 'sources' && tab === 1
        ? <FixtureImport refresh={refresh} />
        : (
          <section className="sp-panel">
            <div className="sp-panel-head">
              <div><h3>{config.title}</h3><p>{isPerformance ? 'Calculated from imported final scores and stored prediction records.' : 'Operational records from the existing SQLite database.'}</p></div>
              <button className="sp-secondary" onClick={refresh} disabled={busy}><RefreshCw size={16} />{busy ? 'Loading…' : 'Refresh'}</button>
            </div>
            {error && <p className="sp-ops-error"><ShieldAlert size={16} />{error}</p>}
            {isPerformance
              ? <Performance data={data} />
              : isHealth
                ? <div className="sp-health">{Object.entries(data).map(([name, value]) => <div key={name}><span>{name.replaceAll('_', ' ')}</span><span>{String(value ?? 'Not available')}</span></div>)}</div>
                : id === 'models' && tab === 2
                  ? <p className="sp-muted">The registered baseline models are statistical references only; no model is marked trained, calibrated, or production-validated.</p>
                  : id === 'alerts' && tab === 2
                    ? <p className="sp-muted">Email and Telegram delivery require provider credentials and an approved delivery integration. No message is represented as delivered until a provider confirms acceptance.</p>
                    : <Listing data={data} cols={id === 'alerts' && tab === 1 ? ['channel', 'event_type', 'payload_json', 'status', 'created_at'] : columnsFor(id, tab, config.cols)} onRemove={onRemove} />}
          </section>
        )}
      {tab === recordFormTab && <RecordForm id={id} refresh={refresh} />}
      <div className="sp-ops-info"><ShieldAlert size={18} /><span>SportyBet booking codes cannot be generated or verified here. Manually entered codes remain unverified. Live data collection and delivery integrations require authorized providers.</span></div>
    </div>
  );
}
