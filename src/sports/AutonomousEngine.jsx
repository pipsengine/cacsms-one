import React, {useEffect, useMemo, useRef, useState} from 'react';
import {Activity, AlertCircle, BarChart3, Bot, CalendarDays, Check, CheckCircle2, ChevronRight, Clock3, Copy, Cpu, Database, Download, FileText, Layers3, Pause, Play, RefreshCw, Settings, ShieldCheck, Square, Target, Ticket, TrendingUp, Workflow} from 'lucide-react';
import {get, post, put} from './api';
import './autonomous.css';

const TABS = [
  ['Autonomous Overview', BarChart3],
  ['Execution Pipeline', Workflow],
  ['Live Operations', Activity],
  ['Generated Tickets', Ticket],
  ['Model Performance', TrendingUp],
  ['Automation Logs', FileText],
  ['Settings', Settings],
  ['Production Readiness', ShieldCheck],
];

function clock(value) {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '—';
  return new Intl.DateTimeFormat('en-GB', {timeZone: 'Africa/Lagos', hour: '2-digit', minute: '2-digit', day: '2-digit', month: 'short'}).format(parsed);
}

function count(value) {
  return value == null ? '—' : Number(value).toLocaleString();
}

function percent(value) {
  return value == null ? '—' : `${Number(value).toFixed(1)}%`;
}

function tone(status) {
  if (status === 'Completed' || status === 'Clear') return 'good';
  if (status === 'Running') return 'running';
  if (status === 'Error' || status === 'Attention') return 'warn';
  return 'neutral';
}

export default function AutonomousEngine({nav}) {
  const [tab, setTab] = useState('Autonomous Overview');
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [taskFilter, setTaskFilter] = useState('Running Tasks');
  const [selected, setSelected] = useState(null);
  const [logQuery, setLogQuery] = useState('');
  const [form, setForm] = useState(null);
  const [saved, setSaved] = useState('');
  const latestRequest = useRef(0);
  const settingsDirty = useRef(false);

  async function load(syncSettings = false) {
    const request = ++latestRequest.current;
    try {
      const value = await get('/autonomous/overview');
      if (request !== latestRequest.current) return;
      setData(value);
      if (syncSettings || !settingsDirty.current) setForm(value.settings);
      setError('');
    } catch (requestError) {
      if (request !== latestRequest.current) return;
      setError(requestError.message);
    }
  }

  useEffect(() => {
    let active = true;
    let timer;
    const refresh = async () => {
      await load();
      if (active) timer = setTimeout(refresh, 10000);
    };
    refresh();
    return () => {
      active = false;
      clearTimeout(timer);
      latestRequest.current += 1;
    };
  }, []);

  async function command(action) {
    setBusy(true);
    setSaved('');
    try {
      latestRequest.current += 1;
      const value = await post('/autonomous/control', {action});
      latestRequest.current += 1;
      setData(value);
      setError('');
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }

  async function saveSettings(event) {
    event.preventDefault();
    setBusy(true);
    setSaved('');
    try {
      await put('/settings', {
        min_edge: Number(form.min_edge),
        max_legs: Number(form.max_legs),
        odds_tolerance: Number(form.odds_tolerance),
        odds_max_age_minutes: Number(form.odds_max_age_minutes),
        form_half_life_days: Number(form.form_half_life_days),
      });
      settingsDirty.current = false;
      await load(true);
      setSaved('Risk controls saved.');
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }

  function updateForm(values) {
    settingsDirty.current = true;
    setForm(current => ({...current, ...values}));
  }

  const summary = data?.summary || {};
  const tasks = data?.tasks || [];
  const filtered = useMemo(() => tasks.filter(task => {
    if (taskFilter === 'Running Tasks') return task.status === 'Running';
    if (taskFilter === 'Completed') return task.status === 'Completed';
    if (taskFilter === 'Errors') return task.status === 'Error';
    if (taskFilter === 'Queue') return task.status === 'Queued';
    return true;
  }), [tasks, taskFilter]);
  const logs = (data?.logs || []).filter(row => !logQuery || row.message.toLowerCase().includes(logQuery.toLowerCase()));
  const running = data?.status === 'running';
  const paused = data?.status === 'paused';

  function exportCSV() {
    const rows = [['Fixture', 'Market', 'Bookmaker', 'Odds', 'Probability', 'Edge'], ...(data?.opportunities || []).map(row => [row.fixture, row.market, row.bookmaker, row.odds, row.probability, row.edge])];
    const blob = new Blob([rows.map(row => row.map(value => `"${String(value ?? '').replaceAll('"', '""')}"`).join(',')).join('\n')], {type: 'text/csv'});
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'cacsms-qualified-opportunities.csv';
    link.click();
    URL.revokeObjectURL(url);
  }

  const metrics = [
    [Layers3, 'Fixtures stored', count(summary.fixtures), summary.fixtures_24h == null ? '—' : `${count(summary.fixtures_24h)} observed in 24h`, 'blue'],
    [CalendarDays, 'Live matches', count(summary.live), summary.live ? 'In play' : 'None in play', 'green'],
    [Cpu, 'Analysed (24h)', count(summary.analysed), 'Prediction rows', 'violet'],
    [Target, 'Value opportunities', count(summary.opportunities), 'Qualified', 'orange'],
    [Ticket, 'Tickets generated', count(summary.tickets), `Ready this week: ${count((data?.tickets || []).length)}`, 'violet'],
    [BarChart3, 'Prediction accuracy', percent(summary.accuracy), summary.settled ? `${count(summary.settled)} settled` : 'No settled results', 'orange'],
    [TrendingUp, 'Model edge (avg)', summary.edge == null ? '—' : `+${Number(summary.edge).toFixed(1)}%`, summary.edge == null ? 'No qualified edge' : 'Qualified average', 'green'],
    [ShieldCheck, 'Qualified decisions', count(summary.qualified_decisions), 'Central qualification', 'blue'],
    [CheckCircle2, 'Verified codes', count(summary.verified_codes), 'Displayed by SportyBet', 'green'],
    [AlertCircle, 'Failed jobs', count(summary.failures), summary.pending_jobs == null ? '—' : `${count(summary.pending_jobs)} pending`, 'orange'],
    [Clock3, 'Rollover campaigns', count(summary.rollover_campaigns), 'Simulation only', 'violet'],
  ];

  return <div className="ae">
    <header className="ae-top">
      <div className="ae-crumb">CACSMS ONE <ChevronRight size={14}/> Sports AI <ChevronRight size={14}/> <b>Autonomous Engine</b></div>
      <div className="ae-pills">
        <span className={`ae-pill ${running && data?.scheduler_fresh ? 'green' : 'amber'}`}><Bot size={13}/> {!data ? 'Engine: connecting' : running && data?.scheduler_fresh ? 'Fully Autonomous: RUNNING' : paused ? 'Engine: PAUSED' : running ? 'Engine: SCHEDULER IDLE' : 'Engine: STOPPED'}</span>
        <span className="ae-pill mint">{!data ? 'Heartbeat checking' : running && summary.uptime ? `Heartbeat ${summary.uptime}` : 'Heartbeat not fresh'}</span>
        <span className="ae-pill">Job success {percent(summary.health)}</span>
      </div>
    </header>
    <div className="ae-headline">
      <div className="ae-heading"><Bot size={33} className="ae-robot"/><div><h1>Autonomous Engine</h1><p>Self-driven sports intelligence: fixture discovery, odds collection, predictions and opportunity generation.</p></div></div>
      <div className="ae-actions">
        <label className="ae-toggle"><input type="checkbox" checked={running} disabled={busy || !data} onChange={event => command(event.target.checked ? 'start' : 'stop')}/> Autonomous Mode (24/7)</label>
        <button className="ae-btn outline" disabled={busy || !data || (!running && !paused)} onClick={() => command(paused ? 'resume' : 'pause')}>{paused ? <Play size={15}/> : <Pause size={15}/>} {paused ? 'Resume' : 'Pause'}</button>
        <button className="ae-btn red" disabled={busy || !data || data.status === 'stopped'} onClick={() => command('stop')}><Square size={14}/> Stop</button>
        <button className="ae-btn" onClick={load}><RefreshCw size={14}/> Refresh</button>
      </div>
    </div>
    {error && <div className="ae-notice"><AlertCircle size={17}/>{error} <button onClick={load}>Retry connection</button></div>}
    <nav className="ae-tabs">{TABS.map(([name, Icon]) => <button key={name} className={tab === name ? 'current' : ''} onClick={() => setTab(name)}><Icon size={17}/>{name}</button>)}</nav>

    {tab === 'Autonomous Overview' && <>
      <div className="ae-metrics">{metrics.map(([Icon, label, value, detail, color]) => <div className="ae-metric" key={label}><div className={`ae-metric-icon ${color}`}><Icon size={22}/></div><div><span>{label}</span><strong>{value}</strong><small>{detail}</small></div></div>)}</div>
      <div className="ae-pipeline"><div className="ae-pipe-lead"><Bot size={28}/><div><strong>Autonomous Workflow</strong><small>End-to-end pipeline</small></div></div>{(data?.stages || []).map((stage, index, stages) => <div className="ae-pipe-step" key={stage.name}>{stage.ok ? <CheckCircle2 size={16} className="ok"/> : <CheckCircle2 size={16} className="muted"/>}<div><b>{index + 1}. {stage.name}</b><small>{stage.detail}</small></div>{index < stages.length - 1 && <ChevronRight className="ae-arrow" size={18}/>}</div>)}</div>
      <div className="ae-grid">
        <section className="ae-panel operations">
          <div className="ae-head"><h2><Activity size={18}/> Live Operations</h2><span className="ae-pill mint">{running && data?.scheduler_fresh ? 'Scheduler heartbeat fresh' : 'Scheduler idle'}</span></div>
          <TaskTable tasks={filtered} all={tasks} filter={taskFilter} onFilter={setTaskFilter}/>
        </section>
        <section className="ae-panel engines">
          <div className="ae-head"><h2><Cpu size={18}/> System Engines</h2></div>
          {(data?.engines || []).map(engine => <div className="ae-engine" key={engine.name}><i className={`ae-dot ${engine.tone === 'good' ? 'on' : ''}`}/><span>{engine.name}</span><em className={`ae-status ${tone(engine.tone === 'good' ? 'Completed' : engine.tone === 'warn' ? 'Error' : 'Stopped')}`}>{engine.status}</em></div>)}
        </section>
        <section className="ae-panel logs">
          <div className="ae-head"><h2><FileText size={18}/> Live Automation Log</h2><button className="ae-link" onClick={() => setTab('Automation Logs')}>View All</button></div>
          {(data?.logs || []).slice(0, 7).map((row, index) => <div className="ae-log" key={`${row.time}-${index}`}><CheckCircle2 size={15}/><time>{clock(row.time)}</time><span>{row.message}</span></div>)}
          {!data?.logs?.length && <p className="ae-empty">No scheduler or audit event is stored.</p>}
        </section>
        <section className="ae-panel opportunities">
          <div className="ae-head"><h2><Target size={18}/> Top Autonomous Opportunities</h2><button className="ae-link" onClick={exportCSV}><Download size={14}/> Export CSV</button></div>
          <OpportunityTable rows={data?.opportunities || []} onOpen={setSelected}/>
        </section>
        <section className="ae-panel tickets">
          <div className="ae-head"><h2><Ticket size={18}/> Generated Tickets (Suggestions)</h2><span className="ae-pill mint">Checked {clock(data?.tickets_checked_at)}</span><button className="ae-link" onClick={() => setTab('Generated Tickets')}>View All</button></div>
          <TicketGrid tickets={data?.tickets || []}/>
        </section>
      </div>
    </>}

    {tab === 'Execution Pipeline' && <section className="ae-panel">
      <div className="ae-head"><h2><Workflow size={18}/> Opportunity pipeline</h2><span className="ae-pill">{data?.pipeline?.phase || '—'}</span></div>
      <div className="ae-table-wrap"><table><thead><tr><th>Stage</th><th>Stored count</th></tr></thead><tbody>
        {(data?.pipeline?.stages || []).map(stage => <tr key={stage.stage}><td>{stage.stage}</td><td>{count(stage.count)}</td></tr>)}
        {!data?.pipeline?.stages?.length && <tr><td className="ae-empty" colSpan="2">No pipeline snapshot is stored.</td></tr>}
      </tbody></table></div>
      <h3 className="ae-subhead">Blockers</h3>
      {(data?.pipeline?.blockers || []).map(item => <p key={`${item.stage}-${item.reason}`}>{item.stage}: {item.reason}</p>)}
      {!data?.pipeline?.blockers?.length && <p className="ae-empty">No blocker is stored.</p>}
      <h3 className="ae-subhead">Booking diagnostics</h3>
      <p className="ae-note">Next publication window: {clock(data?.pipeline?.next_publication_at)}. A code is stored only when SportyBet displays it.</p>
      <div className="ae-table-wrap"><table><thead><tr><th>When</th><th>Reason</th><th>Tickets</th><th>Detail</th></tr></thead><tbody>
        {(data?.pipeline?.booking_diagnostics || []).map(row => <tr key={row.reason_code}><td>{clock(row.created_at)}</td><td>{row.reason_code}</td><td>{count(row.total)}</td><td>{row.detail}</td></tr>)}
        {!data?.pipeline?.booking_diagnostics?.length && <tr><td className="ae-empty" colSpan="4">No booking diagnostic is stored.</td></tr>}
      </tbody></table></div>
      <h3 className="ae-subhead">Prediction review flags</h3>
      <div className="ae-table-wrap"><table><thead><tr><th>Flag</th><th>Qualified selections</th></tr></thead><tbody>
        {(data?.pipeline?.prediction_reviews || []).map(row => <tr key={row.reason_code}><td>{row.reason_code}</td><td>{count(row.total)}</td></tr>)}
        {!data?.pipeline?.prediction_reviews?.length && <tr><td className="ae-empty" colSpan="2">No qualified selection is flagged.</td></tr>}
      </tbody></table></div>
      <h3 className="ae-subhead">Fixture reconciliation</h3>
      <div className="ae-table-wrap"><table><thead><tr><th>Decision</th><th>Fixtures</th></tr></thead><tbody>
        {(data?.pipeline?.reconciliation || []).map(row => <tr key={row.status}><td>{row.status}</td><td>{count(row.total)}</td></tr>)}
        {!data?.pipeline?.reconciliation?.length && <tr><td className="ae-empty" colSpan="2">No fixture reconciliation is stored.</td></tr>}
      </tbody></table></div>
      <h3 className="ae-subhead">Feature timing audit</h3>
      <div className="ae-table-wrap"><table><thead><tr><th>Finding</th><th>Records</th></tr></thead><tbody>
        {(data?.pipeline?.feature_audit || []).map(row => <tr key={row.finding}><td>{row.finding}</td><td>{count(row.total)}</td></tr>)}
        {!data?.pipeline?.feature_audit?.length && <tr><td className="ae-empty" colSpan="2">No feature audit is stored.</td></tr>}
      </tbody></table></div>
      <h3 className="ae-subhead">Rejection reasons</h3>
      <div className="ae-table-wrap"><table><thead><tr><th>Reason</th><th>Decisions</th></tr></thead><tbody>
        {(data?.pipeline?.rejection_reasons || []).map(row => <tr key={row.reason_code}><td>{row.reason_code}</td><td>{count(row.count)}</td></tr>)}
      </tbody></table></div>
      <p className="ae-note">Fresh prices use the stored {data?.pipeline?.refresh?.freshness_minutes ?? 15}-minute window. The refresh queue is waiting on {count(data?.pipeline?.refresh?.queued)} fixtures. Blocked {count(data?.pipeline?.refresh?.blocked)}. Refreshed from a stored SportyBet price {count(data?.pipeline?.refresh?.refreshed)}. Weekly draw rows and rollover campaigns on this page come from the same tables. No wager is placed.</p>
      <div className="ae-head"><h2><Workflow size={18}/> Scheduler stages</h2></div>
      <div className="ae-table-wrap"><table><thead><tr><th>Stage</th><th>Last run</th><th>Next run</th><th>Status</th><th>Evidence</th></tr></thead><tbody>
        {(data?.stages || []).map(stage => <tr key={stage.name}><td>{stage.name}<small>{stage.detail}</small></td><td>{clock(stage.last_run_at)}</td><td>{clock(stage.next_run_at)}</td><td><em className={`ae-status ${tone(stage.status)}`}>{stage.status}</em></td><td>{stage.result || '—'}</td></tr>)}
      </tbody></table></div>
    </section>}

    {tab === 'Live Operations' && <section className="ae-panel">
      <div className="ae-head"><h2><Activity size={18}/> Live Operations</h2></div>
      <TaskTable tasks={filtered} all={tasks} filter={taskFilter} onFilter={setTaskFilter}/>
      <h3 className="ae-subhead">Recent jobs</h3>
      <div className="ae-table-wrap"><table><thead><tr><th>Kind</th><th>Status</th><th>Attempt</th><th>Started</th><th>Finished</th><th>Error</th></tr></thead><tbody>
        {(data?.jobs || []).map(job => <tr key={job.id}><td>{job.kind}</td><td><em className={`ae-status ${tone(job.status === 'complete' ? 'Completed' : job.status === 'running' ? 'Running' : job.status === 'failed' ? 'Error' : 'Stopped')}`}>{job.status}</em></td><td>{job.attempt}</td><td>{clock(job.started)}</td><td>{clock(job.finished)}</td><td>{job.error || '—'}</td></tr>)}
        {!data?.jobs?.length && <tr><td className="ae-empty" colSpan="6">No job is stored.</td></tr>}
      </tbody></table></div>
    </section>}

    {tab === 'Generated Tickets' && <section className="ae-panel">
      <div className="ae-head"><h2><Ticket size={18}/> Generated Tickets</h2><button className="ae-link" onClick={() => nav('tickets')}>Open ticket builder</button></div>
      <TicketGrid tickets={data?.tickets || []}/>
      <p className="ae-note">Checked {clock(data?.tickets_checked_at)}. This is the same list as Ticket Builder: qualified, above 70%, and kicking off between Monday and Sunday this week. Nothing on this page submits a wager.</p>
    </section>}

    {tab === 'Model Performance' && <section className="ae-panel">
      <div className="ae-head"><h2><TrendingUp size={18}/> Model Performance</h2><button className="ae-link" onClick={() => nav('models')}>Open model accuracy</button></div>
      <div className="ae-table-wrap"><table><thead><tr><th>Sport</th><th>Model</th><th>Status</th><th>Brier</th><th>Log loss</th><th>Calibration error</th><th>Samples</th><th>Holdout</th></tr></thead><tbody>
        {(data?.models || []).map(model => <tr key={`${model.sport}-${model.version}-${model.created_at}`}><td>{model.sport}</td><td>{model.name} v{model.version}</td><td><em className={`ae-status ${model.active ? 'good' : 'warn'}`}>{model.active ? 'Active' : model.status}</em></td><td>{model.brier == null ? '—' : Number(model.brier).toFixed(3)}</td><td>{model.log_loss == null ? '—' : Number(model.log_loss).toFixed(3)}</td><td>{model.calibration_error == null ? '—' : Number(model.calibration_error).toFixed(3)}</td><td>{count(model.samples)}</td><td>{count(model.holdout)}</td></tr>)}
      </tbody></table></div>
      <p className="ae-note">Settled accuracy is {percent(summary.accuracy)}. A model becomes active only after its stored chronological holdout clears the calibration and baseline checks. Qualified value uses that active model.</p>
    </section>}

    {tab === 'Automation Logs' && <section className="ae-panel">
      <div className="ae-head"><h2><FileText size={18}/> Automation Logs</h2><input value={logQuery} onChange={event => setLogQuery(event.target.value)} placeholder="Filter log text"/></div>
      {logs.map((row, index) => <div className="ae-log" key={`${row.time}-${index}`}><CheckCircle2 size={15}/><time>{clock(row.time)}</time><span>{row.message}</span></div>)}
      {!logs.length && <p className="ae-empty">No log matches this filter.</p>}
    </section>}

    {tab === 'Settings' && form && <section className="ae-panel">
      <div className="ae-head"><h2><Settings size={18}/> Engine Settings</h2></div>
      <form className="ae-settings" onSubmit={saveSettings}>
        <label>Minimum edge<input type="number" min="0" max="1" step="0.01" value={form.min_edge} onChange={event => updateForm({min_edge: event.target.value})}/></label>
        <label>Maximum ticket legs<input type="number" min="1" max="8" step="1" value={form.max_legs} onChange={event => updateForm({max_legs: event.target.value})}/></label>
        <label>Odds tolerance<input type="number" min="0.01" max="0.5" step="0.01" value={form.odds_tolerance} onChange={event => updateForm({odds_tolerance: event.target.value})}/></label>
        <label>Odds freshness, minutes<input type="number" min="1" max="1440" step="1" value={form.odds_max_age_minutes} onChange={event => updateForm({odds_max_age_minutes: event.target.value})}/></label>
        <label>Form half-life, days<input type="number" min="1" max="3650" step="1" value={form.form_half_life_days} onChange={event => updateForm({form_half_life_days: event.target.value})}/></label>
        <label className="ae-check"><input type="checkbox" checked={form.autonomous_tickets !== false} onChange={event => updateForm({autonomous_tickets: event.target.checked})}/> Build tickets automatically from current QUALIFIED selections</label>
        <button className="ae-btn primary" disabled={busy}>Save risk controls</button>
        {saved && <span className="ae-saved">{saved}</span>}
      </form>
      <p className="ae-note">The worker keeps collection, qualification, tickets and booking codes running. A pause is resumed on the next cycle. No wager is placed.</p>
    </section>}

    {tab === 'Production Readiness' && <section className="ae-panel">
      <div className="ae-head"><h2><ShieldCheck size={18}/> Production readiness</h2><span className={`ae-pill ${data?.readiness?.ready ? 'green' : 'amber'}`}>{data?.readiness?.ready ? 'Ready' : 'Not production-ready'}</span></div>
      <div className="ae-table-wrap"><table><thead><tr><th>Check</th><th>Result</th><th>Evidence</th></tr></thead><tbody>
        {(data?.readiness?.checks || []).map(item => <tr key={item.name}><td>{item.name}</td><td><em className={`ae-status ${item.ok ? 'good' : 'warn'}`}>{item.ok ? 'Met' : 'Open'}</em></td><td>{item.detail}</td></tr>)}
      </tbody></table></div>
      <h3 className="ae-subhead">Operating procedure</h3>
      {(data?.readiness?.procedures || []).map(item => <p key={item}>{item}</p>)}
      <h3 className="ae-subhead">Rollback</h3>
      {(data?.readiness?.rollback || []).map(item => <p key={item}>{item}</p>)}
      <p className="ae-note">Paid wagering stays off. A green check is evidence already stored or configured. An open check is unresolved.</p>
    </section>}

    <footer className="ae-footer">
      <span><Clock3 size={18}/> Next cycle <b>{clock(data?.next_cycle)}</b></span>
      <span><RefreshCw size={18}/> Refresh <b>Every 10 seconds</b></span>
      <span><Database size={18}/> Sources <b>Stored providers and SportyBet prices</b></span>
      <span><Cpu size={18}/> Models <b>Football and basketball registry</b></span>
      <span><ShieldCheck size={18}/> Wagers <b>Not placed</b></span>
    </footer>
    <p className="ae-footnote">Counts, odds, tickets and model metrics come from the Sports AI database. A blank value has not been stored. Real-money bets are placed only by Single Betting, under an authorization you grant.</p>
    {selected && <div className="ae-overlay" onClick={() => setSelected(null)}><div className="ae-modal" onClick={event => event.stopPropagation()}><button className="ae-close" onClick={() => setSelected(null)}>×</button><h2>{selected.fixture}</h2><p>{selected.competition} · {clock(selected.starts_at)} · {selected.market} · {selected.bookmaker} {Number(selected.odds).toFixed(2)} · probability {selected.probability}% · edge +{selected.edge}%</p><button className="ae-btn primary" onClick={() => setSelected(null)}>Close</button></div></div>}
  </div>;
}

function TaskTable({tasks, all, filter, onFilter}) {
  const filters = ['Running Tasks', 'Queue', 'Completed', 'Errors'];
  return <>
    <div className="ae-mini">{filters.map(name => <button key={name} className={filter === name ? 'selected' : ''} onClick={() => onFilter(name)}>{name} <span>({all.filter(task => task.status === (name === 'Running Tasks' ? 'Running' : name === 'Queue' ? 'Queued' : name === 'Errors' ? 'Error' : 'Completed')).length})</span></button>)}</div>
    <div className="ae-table-wrap"><table><thead><tr><th>#</th><th>Task / Operation</th><th>Progress</th><th>Status</th><th>Started</th><th>Duration</th></tr></thead><tbody>
      {tasks.map((task, index) => <tr key={task.name}><td>{index + 1}</td><td>{task.name}</td><td>{task.progress == null ? 'In progress' : <><i className="ae-progress"><b style={{width: `${task.progress}%`}}/></i> {task.progress}%</>}</td><td><em className={`ae-status ${tone(task.status)}`}>{task.status}</em></td><td>{clock(task.started)}</td><td>{task.duration || '—'}</td></tr>)}
      {!tasks.length && <tr><td className="ae-empty" colSpan="6">No matching tasks</td></tr>}
    </tbody></table></div>
  </>;
}

function OpportunityTable({rows, onOpen}) {
  return <div className="ae-table-wrap"><table><thead><tr><th>#</th><th>Fixture</th><th>Market</th><th>Odds</th><th>AI Probability</th><th>Edge</th><th>Confidence</th></tr></thead><tbody>
    {rows.map((row, index) => <tr key={row.id || `${row.fixture}-${row.market}-${index}`} className="click" onClick={() => onOpen(row)}><td>{index + 1}</td><td>{row.fixture}</td><td>{row.market}</td><td>{Number(row.odds).toFixed(2)}</td><td>{row.probability}%</td><td className="ae-positive">+{row.edge}%</td><td><em className="ae-status neutral">Not stored</em></td></tr>)}
    {!rows.length && <tr><td className="ae-empty" colSpan="7">No qualified opportunity is stored. A row appears after an active validated model and a current verified price produce a positive edge.</td></tr>}
  </tbody></table></div>;
}

function marketLabel(market) {
  const name = String(market || '').toUpperCase();
  const fixed = {HOME: 'Home win', AWAY: 'Away win', DRAW: 'Draw', BTTS_YES: 'Both teams score', BTTS_NO: 'Not both teams score',
    DOUBLE_CHANCE_1X: 'Double chance 1X', DOUBLE_CHANCE_X2: 'Double chance X2', DOUBLE_CHANCE_12: 'Double chance 12',
    MONEYLINE_HOME: 'Home win', MONEYLINE_AWAY: 'Away win'};
  if (fixed[name]) return fixed[name];
  const line = name.match(/^(OVER|UNDER)_(.+)$/);
  if (line) return `${line[1] === 'OVER' ? 'Over' : 'Under'} ${line[2].replace('_', '.')}`;
  return name.replace(/_/g, ' ').toLowerCase().replace(/^./, c => c.toUpperCase());
}

function kickoff(value) {
  if (!value) return '';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '';
  return parsed.toLocaleString('en-GB', {timeZone: 'Africa/Lagos', weekday: 'short', hour: '2-digit', minute: '2-digit'});
}

function CodeChip({code}) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(code); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* clipboard blocked */ }
  };
  return <button type="button" className="ae-code-chip" onClick={copy} title="Copy booking code" aria-label={`Copy booking code ${code}`}>
    <span>{code}</span>{copied ? <Check size={14}/> : <Copy size={14}/>}
  </button>;
}

// Scrolls the ticket feed slowly on its own; pauses while the pointer or focus is inside,
// and stays still for people who prefer reduced motion.
function useAutoScroll(ref, deps) {
  useEffect(() => {
    const node = ref.current;
    if (!node || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return undefined;
    let frame, paused = false, hold = 90, position = node.scrollTop;
    const pause = () => { paused = true; };
    const resume = () => { paused = false; position = node.scrollTop; };
    const step = () => {
      if (!paused && node.scrollHeight > node.clientHeight + 4) {
        if (hold > 0) hold -= 1;
        else if (node.scrollTop + node.clientHeight >= node.scrollHeight - 2) { hold = 150; position = 0; node.scrollTo({top: 0, behavior: 'smooth'}); }
        else { position += 0.35; node.scrollTop = position; }
      }
      frame = requestAnimationFrame(step);
    };
    node.addEventListener('mouseenter', pause); node.addEventListener('mouseleave', resume);
    node.addEventListener('focusin', pause); node.addEventListener('focusout', resume);
    node.addEventListener('touchstart', pause, {passive: true}); node.addEventListener('touchend', resume);
    frame = requestAnimationFrame(step);
    return () => {
      cancelAnimationFrame(frame);
      node.removeEventListener('mouseenter', pause); node.removeEventListener('mouseleave', resume);
      node.removeEventListener('focusin', pause); node.removeEventListener('focusout', resume);
      node.removeEventListener('touchstart', pause); node.removeEventListener('touchend', resume);
    };
  }, deps); // eslint-disable-line react-hooks/exhaustive-deps
}

function TicketGrid({tickets}) {
  const feed = useRef(null);
  useAutoScroll(feed, [tickets.length]);
  if (!tickets.length) return <p className="ae-empty">No ticket is stored for this week. The worker checks again on its next cycle.</p>;
  const coded = tickets.filter(t => t.booking_code).length;
  return <>
    <div className="ae-ticket-summary"><span><b>{tickets.length}</b> tickets</span><span><b>{coded}</b> with code</span><span><b>{tickets.length - coded}</b> awaiting</span></div>
    <div className="ae-ticket-feed" ref={feed} tabIndex={0} aria-label="Generated tickets, scrolls automatically; hover or focus to pause">
      {tickets.map(ticket => {
        const favourite = ticket.product === 'favourites';
        const legs = ticket.legs?.length ? ticket.legs : (ticket.picks || []).map(text => ({text}));
        return <article className={`ae-ticket ${favourite ? 'fav' : ''}`} key={ticket.id}>
          <header>
            <h3><Ticket size={15}/>{ticket.type}</h3>
            {favourite ? <em className="ae-tag warn" title="Strong favourites without a positive edge; expected to lose money over time">Favourites · no edge</em>
              : <em className="ae-tag">Qualified edge</em>}
          </header>
          <small className="ae-ticket-meta">Built {clock(ticket.created_at)}{ticket.kickoff ? ` · first kickoff ${kickoff(ticket.kickoff)} WAT` : ''}</small>
          {ticket.booking_code ? <CodeChip code={ticket.booking_code}/>
            : <p className="ae-code wait"><Clock3 size={13}/>{ticket.status === 'qualified' ? 'Awaiting SportyBet code' : ticket.status}</p>}
          <ul className="ae-legs">{legs.map((leg, index) => leg.text ? <li key={index}><span>{leg.text}</span></li> :
            <li key={index}><span><b>{leg.home} v {leg.away}</b><small>{marketLabel(leg.market)} · {kickoff(leg.starts_at)}</small></span><strong>{Number(leg.odds).toFixed(2)}</strong></li>)}</ul>
          <footer><strong>{ticket.odds == null ? 'Odds not stored' : `Total odds ${Number(ticket.odds).toFixed(2)}`}</strong>
            {ticket.probability != null && <span title="Estimated chance that every leg wins">{ticket.probability}% win chance</span>}</footer>
        </article>;
      })}
    </div>
  </>;
}
