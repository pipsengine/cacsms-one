import React, {useEffect, useMemo, useState} from 'react';
import {useNavigate} from 'react-router-dom';
import {AlertCircle, Download, Pause, Play, RefreshCw, RotateCcw} from 'lucide-react';
import {get, post, put} from './api';

const adminHeaders = key => (key ? {'X-Sports-Admin-Key': key} : {});

function localTime(value) {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
}

function download(name, rows) {
  const blob = new Blob([JSON.stringify(rows, null, 2)], {type: 'application/json'});
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

function Table({columns, rows, empty}) {
  if (!rows.length) return <div className="sp-emptybox">{empty}</div>;
  return (
    <div className="sp-table-scroll">
      <table className="sp-table">
        <thead><tr>{columns.map(column => <th key={column.key}>{column.label}</th>)}</tr></thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={row.id || index}>
              {columns.map(column => <td key={column.key}>{column.render ? column.render(row) : String(row[column.key] ?? '—')}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Bars({evaluation}) {
  const bins = evaluation?.metrics_json ? JSON.parse(evaluation.metrics_json).bins || [] : [];
  if (!bins.length) return <div className="sp-emptybox">No reliability bins are stored yet. Evaluation uses only predictions made before kickoff.</div>;
  return (
    <div className="sp-intel-detail-grid">
      {bins.map(bin => (
        <div key={`${bin.lower}-${bin.upper}`}>
          <small>{Math.round(bin.lower * 100)}–{Math.round(bin.upper * 100)}%</small>
          <strong>{bin.count} predictions</strong>
          <div style={{height: 8, background: '#e7eef8', borderRadius: 99}}>
            <div style={{height: 8, width: `${Math.min(100, Math.round((bin.frequency || 0) * 100))}%`, background: '#1457d8', borderRadius: 99}} />
          </div>
          <small>Observed {bin.frequency == null ? '—' : `${Math.round(bin.frequency * 100)}%`}</small>
        </div>
      ))}
    </div>
  );
}

export default function OperationsCentre({page, tab = 0}) {
  const navigate = useNavigate();
  const [adminKey, setAdminKey] = useState('');
  const [query, setQuery] = useState('');
  const [focus, setFocus] = useState('');
  const [command, setCommand] = useState(null);
  const [workflows, setWorkflows] = useState([]);
  const [events, setEvents] = useState([]);
  const [schedules, setSchedules] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [providers, setProviders] = useState([]);
  const [browser, setBrowser] = useState(null);
  const [evaluations, setEvaluations] = useState({evaluations: [], drift: [], note: ''});
  const [rules, setRules] = useState([]);
  const [deliveries, setDeliveries] = useState([]);
  const [recovery, setRecovery] = useState([]);
  const [ruleForm, setRuleForm] = useState({event_type: 'qualified_opportunity', channel: 'dashboard', template: 'Qualified opportunity {entity_key}', enabled: false});
  const [scheduleForm, setScheduleForm] = useState({name: 'Manual research check', interval_minutes: 60, job_kind: 'browser_research', cron_expression: '', enabled: false});
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    setError('');
    try {
      if (page === 'monitor') {
        const [centre, health, research, quality, flow] = await Promise.all([
          get('/operations/command'), get('/operations/providers/health'), get('/operations/browser-research'),
          get('/operations/evaluations'), get('/operations/workflows'),
        ]);
        setCommand(centre);
        setProviders(health);
        setBrowser(research);
        setEvaluations(quality);
        setWorkflows(flow);
      } else if (page === 'scheduler') {
        const [rows, history] = await Promise.all([get('/operations/schedules'), get('/operations/jobs')]);
        setSchedules(rows);
        setJobs(history);
      } else if (page === 'alerts') {
        const [ruleRows, deliveryRows] = await Promise.all([
          get('/operations/notification-rules'), get('/operations/notification-deliveries'),
        ]);
        setRules(ruleRows);
        setDeliveries(deliveryRows);
      } else {
        const [centre, cases, flow] = await Promise.all([
          get('/operations/command'), get('/operations/recovery'), get('/operations/workflows'),
        ]);
        setCommand(centre);
        setRecovery(cases);
        setWorkflows(flow);
        setEvents(centre.recent_events || []);
      }
    } catch (loadError) {
      setError(loadError.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, [page]);

  const needle = query.trim().toLowerCase();
  const visible = rows => rows.filter(row => !needle || JSON.stringify(row).toLowerCase().includes(needle));
  const cards = useMemo(() => {
    if (!command) return [];
    return [
      ['Fixtures discovered', command.fixtures, 'workflows'],
      ['Fixtures analysed', command.fixtures_analysed, 'workflows'],
      ['Predictions', command.predictions, 'evaluations'],
      ['Qualified opportunities', command.qualified_opportunities, 'workflows'],
      ['Accumulator tickets', command.accumulator_tickets, '/sports/tickets'],
      ['Pending settlements', command.pending_settlements, '/sports/tickets'],
      ['Active jobs', command.active_jobs, 'jobs'],
      ['Failed jobs', command.failed_jobs, 'jobs'],
      ['Open incidents', command.open_incidents, 'recovery'],
      ['Providers reporting', command.providers?.length || 0, 'providers'],
    ];
  }, [command]);

  async function act(work) {
    setMessage('');
    setError('');
    try {
      const result = await work();
      setMessage(typeof result === 'string' ? result : 'Saved. The worker executes queued jobs; this request did not start a second scheduler.');
      await load();
    } catch (actionError) {
      setError(actionError.message);
    }
  }

  function openCard(target) {
    if (String(target).startsWith('/sports/')) navigate(target);
    else setFocus(target);
  }

  const focusedWorkflows = visible(workflows).filter(row => !focus || focus === 'workflows' || row.state === focus);
  const failedJobs = visible(jobs).filter(row => ['failed', 'dead_letter'].includes(row.status));

  return (
    <div className="sp-intelligence-stack">
      <section className="sp-panel">
        <div className="sp-panel-head">
          <div>
            <h3>Operations records</h3>
            <p>Times below are shown in this browser’s time zone. Schedules are stored in UTC. {command?.hit_rate_note || evaluations.note}</p>
          </div>
          <div className="sp-intel-actions">
            <button className="sp-secondary" onClick={load}><RefreshCw size={16} />{loading ? 'Loading…' : 'Refresh'}</button>
          </div>
        </div>
        <div className="sp-intel-filters">
          <label>Search records<input value={query} onChange={event => setQuery(event.target.value)} placeholder="Fixture, job, provider or status" /></label>
          <label>Admin key for changes<input type="password" value={adminKey} onChange={event => setAdminKey(event.target.value)} placeholder="Required for schedules, retries and alerts" autoComplete="off" /></label>
        </div>
        {error && <p className="sp-ops-error"><AlertCircle size={16} />{error}</p>}
        {message && <p role="status">{message}</p>}
        {loading && !command && page !== 'scheduler' && page !== 'alerts' && <div className="sp-emptybox">Loading operational records…</div>}
      </section>

      {page === 'monitor' && tab === 0 && (
        <section className="sp-panel">
          <div className="sp-metrics">
            {cards.map(([label, value, target]) => (
              <button className="sp-metric" key={label} onClick={() => openCard(target)}>
                <span>{label}</span><strong>{value ?? 0}</strong><small>Open the stored records</small>
              </button>
            ))}
          </div>
          <p className="sp-muted">Scheduler lease owner: {command?.leader?.owner || 'none'}. Heartbeat {localTime(command?.leader?.heartbeat_at)}.</p>
          {focus === 'jobs' && <Table columns={[{key: 'state', label: 'Workflow state'}, {key: 'total', label: 'Fixtures'}]} rows={Object.entries(command?.workflows_by_state || {}).map(([state, total]) => ({id: state, state, total}))} empty="No fixture workflows have been recorded." />}
          {focus === 'providers' && <ProviderTable rows={visible(providers)} />}
          {focus === 'evaluations' && <EvaluationTable rows={visible(evaluations.evaluations || [])} />}
          {focus === 'recovery' && <p className="sp-muted">Open incidents are listed on Activity & Audit → Changes, where a case can be requeued.</p>}
          {focus === 'workflows' && <>
            <WorkflowTable rows={focusedWorkflows} onOpen={async id => setEvents(await get(`/operations/workflows/${id}/events`))} />
            {events.length > 0 && <Table columns={[{key: 'from_state', label: 'From'}, {key: 'to_state', label: 'To'}, {key: 'reason', label: 'Reason'}, {key: 'created_at', label: 'When', render: row => localTime(row.created_at)}]} rows={events} empty="No workflow events." />}
          </>}
        </section>
      )}

      {page === 'monitor' && tab === 1 && (
        <section className="sp-panel">
          <div className="sp-panel-head"><div><h3>Provider freshness</h3><p>A source pauses after three consecutive failed collection jobs. Missing values stay missing.</p></div><button className="sp-secondary" onClick={() => download('provider-health.json', providers)}><Download size={16} />Export</button></div>
          <ProviderTable rows={visible(providers)} onPause={id => act(() => post(`/operations/providers/${id}/pause`, {}, adminHeaders(adminKey)))} onResume={id => act(() => post(`/operations/providers/${id}/resume`, {}, adminHeaders(adminKey)))} />
          <h3>Browser research</h3>
          <p>{browser?.reason || 'Browser research status has not been loaded.'}</p>
          <p className="sp-muted">Approved non-BetExplorer hosts recorded: {browser?.approved_sources ?? 0}. Status: {browser?.status || '—'}</p>
        </section>
      )}

      {page === 'monitor' && tab === 2 && (
        <section className="sp-panel">
          <div className="sp-panel-head"><div><h3>Model evaluation</h3><p>{evaluations.note}</p></div></div>
          <EvaluationTable rows={visible(evaluations.evaluations || [])} />
          <Bars evaluation={evaluations.evaluations?.[0]} />
          <h3>Drift signals</h3>
          <Table columns={[{key: 'sport', label: 'Sport'}, {key: 'market', label: 'Market'}, {key: 'metric_value', label: 'Shift', render: row => Number(row.metric_value).toFixed(3)}, {key: 'created_at', label: 'Recorded', render: row => localTime(row.created_at)}]} rows={visible(evaluations.drift || [])} empty="No drift event is stored. Drift monitoring does not replace the active model." />
          <form className="sp-ops-form" onSubmit={event => { event.preventDefault(); const sport = new FormData(event.target).get('sport'); act(() => post(`/operations/training/evaluate?sport=${sport}`, {}, adminHeaders(adminKey)).then(result => `Candidate status ${result.status}. Promoted: ${result.promoted}.`)); }}>
            <h3>Candidate retraining</h3>
            <label>Sport<select name="sport" defaultValue="football"><option value="football">Football</option><option value="basketball">Basketball</option></select></label>
            <button className="sp-primary" type="submit">Evaluate candidate</button>
          </form>
        </section>
      )}

      {page === 'scheduler' && tab === 0 && (
        <section className="sp-panel">
          <div className="sp-panel-head"><div><h3>UTC schedules</h3><p>Disabled rows do not run. Enabling a row does not launch another scheduler inside the web process.</p></div><button className="sp-secondary" onClick={() => download('schedules.json', schedules)}><Download size={16} />Export</button></div>
          {loading && <div className="sp-emptybox">Loading schedules…</div>}
          <Table columns={[
            {key: 'name', label: 'Schedule'},
            {key: 'job_kind', label: 'Job'},
            {key: 'interval_minutes', label: 'Interval (minutes)'},
            {key: 'cron_expression', label: 'Cron (UTC)'},
            {key: 'enabled', label: 'Enabled', render: row => row.enabled ? 'Yes' : 'No'},
            {key: 'last_run_at', label: 'Last run', render: row => localTime(row.last_run_at)},
            {key: 'actions', label: 'Actions', render: row => (
              <span className="sp-intel-actions">
                <button className="sp-secondary" onClick={() => act(() => put(`/operations/schedules/${row.id}`, {enabled: !row.enabled}, adminHeaders(adminKey)))}>{row.enabled ? <Pause size={14} /> : <Play size={14} />}{row.enabled ? 'Disable' : 'Enable'}</button>
                <button className="sp-secondary" onClick={() => act(() => post(`/operations/schedules/${row.id}/run`, {}, adminHeaders(adminKey)))}>Queue</button>
              </span>
            )},
          ]} rows={visible(schedules)} empty="No schedules are stored." />
          <form className="sp-ops-form" onSubmit={event => { event.preventDefault(); act(() => post('/operations/schedules', {...scheduleForm, interval_minutes: Number(scheduleForm.interval_minutes), cron_expression: scheduleForm.cron_expression || null}, adminHeaders(adminKey))); }}>
            <h3>Add a schedule</h3>
            <div className="sp-form-grid">
              <label>Name<input value={scheduleForm.name} onChange={event => setScheduleForm({...scheduleForm, name: event.target.value})} required /></label>
              <label>Interval minutes<input type="number" min="5" value={scheduleForm.interval_minutes} onChange={event => setScheduleForm({...scheduleForm, interval_minutes: event.target.value})} required /></label>
              <label>Job kind<input value={scheduleForm.job_kind} onChange={event => setScheduleForm({...scheduleForm, job_kind: event.target.value})} required /></label>
              <label>Cron UTC, optional<input value={scheduleForm.cron_expression} onChange={event => setScheduleForm({...scheduleForm, cron_expression: event.target.value})} placeholder="0 * * * *" /></label>
            </div>
            <button className="sp-primary" type="submit">Save disabled schedule</button>
          </form>
        </section>
      )}

      {page === 'scheduler' && tab === 1 && <section className="sp-panel"><h3>Execution history</h3><JobTable rows={visible(jobs)} onCancel={id => act(() => post(`/operations/jobs/${id}/cancel`, {}, adminHeaders(adminKey)))} /></section>}
      {page === 'scheduler' && tab === 2 && <section className="sp-panel"><h3>Failed and dead-letter jobs</h3><JobTable rows={failedJobs} /></section>}

      {page === 'alerts' && tab === 0 && <section className="sp-panel"><div className="sp-panel-head"><div><h3>Delivery history</h3><p>Sent means the channel accepted the message. not_configured means no provider call was made.</p></div><button className="sp-secondary" onClick={() => download('notification-deliveries.json', deliveries)}><Download size={16} />Export</button></div><Table columns={[{key: 'event_type', label: 'Event'}, {key: 'channel', label: 'Channel'}, {key: 'status', label: 'Status'}, {key: 'provider_response', label: 'Provider response'}, {key: 'created_at', label: 'Recorded', render: row => localTime(row.created_at)}]} rows={visible(deliveries)} empty="No notification has been delivered." /></section>}
      {page === 'alerts' && tab === 1 && <section className="sp-panel"><h3>Channels</h3><p>Dashboard writes an in-app record. Email uses SMTP only when the host, sender and recipient are configured. Telegram uses the bot API only when the token and chat are configured.</p><Table columns={[{key: 'channel', label: 'Channel'}, {key: 'event_type', label: 'Event'}, {key: 'enabled', label: 'Enabled', render: row => row.enabled ? 'Yes' : 'No'}]} rows={visible(rules)} empty="No notification rules are stored." /></section>}
      {page === 'alerts' && tab === 2 && (
        <section className="sp-panel">
          <form className="sp-ops-form" onSubmit={event => { event.preventDefault(); act(() => post('/operations/notification-rules', ruleForm, adminHeaders(adminKey))); }}>
            <h3>Alert rule</h3>
            <div className="sp-form-grid">
              <label>Event<input value={ruleForm.event_type} onChange={event => setRuleForm({...ruleForm, event_type: event.target.value})} required /></label>
              <label>Channel<select value={ruleForm.channel} onChange={event => setRuleForm({...ruleForm, channel: event.target.value})}><option value="dashboard">Dashboard</option><option value="email">Email</option><option value="telegram">Telegram</option></select></label>
              <label>Template<input value={ruleForm.template} onChange={event => setRuleForm({...ruleForm, template: event.target.value})} required /></label>
              <label className="sp-intel-checks"><input type="checkbox" checked={ruleForm.enabled} onChange={event => setRuleForm({...ruleForm, enabled: event.target.checked})} />Enabled</label>
            </div>
            <button className="sp-primary" type="submit">Save rule</button>
          </form>
        </section>
      )}
      {page === 'alerts' && tab === 3 && (
        <section className="sp-panel">
          <h3>Test delivery</h3>
          <p className="sp-muted">A dashboard test can be sent. Email and Telegram return not_configured when credentials are absent, and that result is not treated as delivery.</p>
          <div className="sp-intel-actions">
            {['dashboard', 'email', 'telegram'].map(channel => (
              <button className="sp-secondary" key={channel} onClick={() => act(() => post(`/operations/notifications/test?channel=${channel}`, {}, adminHeaders(adminKey)).then(result => `${channel}: ${result.status}`))}>{channel}</button>
            ))}
          </div>
        </section>
      )}

      {page === 'activity' && tab === 0 && <section className="sp-panel"><h3>Workflow activity</h3><Table columns={[{key: 'fixture_id', label: 'Fixture'}, {key: 'from_state', label: 'From'}, {key: 'to_state', label: 'To'}, {key: 'reason', label: 'Reason'}, {key: 'created_at', label: 'When', render: row => localTime(row.created_at)}]} rows={visible(events)} empty="No workflow transition has been recorded." /></section>}
      {page === 'activity' && tab === 1 && <section className="sp-panel"><h3>Fixture workflows</h3><WorkflowTable rows={visible(workflows)} onOpen={async id => { setEvents(await get(`/operations/workflows/${id}/events`)); }} />{events.length > 0 && <Table columns={[{key: 'from_state', label: 'From'}, {key: 'to_state', label: 'To'}, {key: 'reason', label: 'Reason'}, {key: 'created_at', label: 'When', render: row => localTime(row.created_at)}]} rows={events} empty="No events." />}</section>}
      {page === 'activity' && tab === 2 && <section className="sp-panel"><h3>Recovery cases</h3><RecoveryTable rows={visible(recovery)} onRetry={id => act(() => post(`/operations/recovery/${id}/retry`, {}, adminHeaders(adminKey)))} /></section>}
    </div>
  );
}

function WorkflowTable({rows, onOpen}) {
  return <Table columns={[{key: 'fixture_id', label: 'Fixture'}, {key: 'state', label: 'State'}, {key: 'reason', label: 'Reason'}, {key: 'updated_at', label: 'Updated', render: row => localTime(row.updated_at)}, {key: 'open', label: 'Timeline', render: row => <button className="sp-text-action" onClick={() => onOpen(row.id)}>Events</button>}]} rows={rows} empty="No fixture has entered the workflow." />;
}

function JobTable({rows, onCancel}) {
  return <Table columns={[{key: 'job_kind', label: 'Kind'}, {key: 'status', label: 'Status'}, {key: 'attempt', label: 'Attempt'}, {key: 'error_code', label: 'Error code'}, {key: 'error', label: 'Diagnostic'}, {key: 'created_at', label: 'Created', render: row => localTime(row.created_at)}, {key: 'cancel', label: 'Cancel', render: row => onCancel && ['queued', 'retry_pending'].includes(row.status) ? <button className="sp-secondary" onClick={() => onCancel(row.id)}>Cancel</button> : '—'}]} rows={rows} empty="No jobs have been recorded." />;
}

function ProviderTable({rows, onPause, onResume}) {
  return <Table columns={[{key: 'display_name', label: 'Provider'}, {key: 'status', label: 'Status'}, {key: 'consecutive_failures', label: 'Consecutive failures'}, {key: 'freshness_minutes', label: 'Freshness (minutes)', render: row => row.freshness_minutes == null ? 'No successful collection' : Number(row.freshness_minutes).toFixed(1)}, {key: 'paused', label: 'Paused', render: row => row.paused ? 'Yes' : 'No'}, {key: 'actions', label: 'Actions', render: row => onPause ? <span className="sp-intel-actions"><button className="sp-secondary" onClick={() => onPause(row.provider_id || row.id)}><Pause size={14} />Pause</button><button className="sp-secondary" onClick={() => onResume(row.provider_id || row.id)}>Resume</button></span> : '—'}]} rows={rows} empty="No provider health snapshot is stored yet. Run the worker to record one." />;
}

function EvaluationTable({rows}) {
  return <Table columns={[{key: 'sport', label: 'Sport'}, {key: 'market', label: 'Market'}, {key: 'sample_size', label: 'Sample'}, {key: 'brier_score', label: 'Brier', render: row => row.brier_score == null ? '—' : Number(row.brier_score).toFixed(4)}, {key: 'log_loss', label: 'Log loss', render: row => row.log_loss == null ? '—' : Number(row.log_loss).toFixed(4)}, {key: 'calibration_error', label: 'Calibration error', render: row => row.calibration_error == null ? '—' : Number(row.calibration_error).toFixed(4)}, {key: 'hit_rate', label: 'Hit rate', render: row => row.hit_rate == null ? '—' : `${(row.hit_rate * 100).toFixed(1)}%`}]} rows={rows} empty="No scored prediction has a result recorded after kickoff." />;
}

function RecoveryTable({rows, onRetry}) {
  return <Table columns={[{key: 'error_code', label: 'Error code'}, {key: 'status', label: 'Status'}, {key: 'attempt_count', label: 'Attempts'}, {key: 'job_id', label: 'Job'}, {key: 'created_at', label: 'Opened', render: row => localTime(row.created_at)}, {key: 'retry', label: 'Retry', render: row => row.status === 'open' ? <button className="sp-secondary" onClick={() => onRetry(row.id)}><RotateCcw size={14} />Requeue</button> : row.status}]} rows={rows} empty="No recovery case is open." />;
}
