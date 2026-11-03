import React, {useEffect, useMemo, useState} from 'react';
import {Activity, AlertCircle, BarChart3, Bot, CalendarDays, CheckCircle2, ChevronRight, Clock3, Cpu, Database, Download, FileText, Layers3, Pause, Play, RefreshCw, Settings, ShieldCheck, Square, Target, Ticket, TrendingUp, Workflow} from 'lucide-react';
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

  async function load() {
    try {
      const value = await get('/autonomous/overview');
      setData(value);
      setForm(value.settings);
      setError('');
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  useEffect(() => {
    load();
    const timer = setInterval(load, 30000);
    return () => clearInterval(timer);
  }, []);

  async function command(action) {
    setBusy(true);
    setSaved('');
    try {
      setData(await post('/autonomous/control', {action}));
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
      await load();
      setSaved('Risk controls saved.');
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
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
    [Ticket, 'Tickets generated', count(summary.tickets), summary.tickets ? 'Stored suggestions' : 'None qualified', 'violet'],
    [BarChart3, 'Prediction accuracy', percent(summary.accuracy), summary.settled ? `${count(summary.settled)} settled` : 'No settled results', 'orange'],
    [TrendingUp, 'Model edge (avg)', summary.edge == null ? '—' : `+${Number(summary.edge).toFixed(1)}%`, summary.edge == null ? 'No qualified edge' : 'Qualified average', 'green'],
  ];

  return <div className="ae">
    <header className="ae-top">
      <div className="ae-crumb">CACSMS ONE <ChevronRight size={14}/> Sports AI <ChevronRight size={14}/> <b>Autonomous Engine</b></div>
      <div className="ae-pills">
        <span className={`ae-pill ${running && data?.scheduler_fresh ? 'green' : 'amber'}`}><Bot size={13}/> {running && data?.scheduler_fresh ? 'Fully Autonomous: RUNNING' : paused ? 'Engine: PAUSED' : running ? 'Engine: SCHEDULER IDLE' : 'Engine: STOPPED'}</span>
        <span className="ae-pill mint">{running && summary.uptime ? `Heartbeat ${summary.uptime}` : 'Heartbeat not fresh'}</span>
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
      <div className="ae-pipeline"><div className="ae-pipe-lead"><Bot size={28}/><div><strong>Autonomous Workflow</strong><small>End-to-end pipeline</small></div></div>{(data?.stages || []).map((stage, index) => <div className="ae-pipe-step" key={stage.name}>{stage.ok ? <CheckCircle2 size={16} className="ok"/> : <CheckCircle2 size={16} className="muted"/>}<div><b>{index + 1}. {stage.name}</b><small>{stage.detail}</small></div>{index < 5 && <ChevronRight className="ae-arrow" size={18}/>}</div>)}</div>
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
          <div className="ae-head"><h2><Ticket size={18}/> Generated Tickets (Suggestions)</h2><button className="ae-link" onClick={() => setTab('Generated Tickets')}>View All</button></div>
          <TicketGrid tickets={data?.tickets || []}/>
        </section>
      </div>
    </>}

    {tab === 'Execution Pipeline' && <section className="ae-panel">
      <div className="ae-head"><h2><Workflow size={18}/> Execution Pipeline</h2></div>
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
      <p className="ae-note">A ticket is stored only after qualified selections produce a legal combination. Nothing on this page submits a wager.</p>
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
        <label>Minimum edge<input type="number" min="0" max="1" step="0.01" value={form.min_edge} onChange={event => setForm({...form, min_edge: event.target.value})}/></label>
        <label>Maximum ticket legs<input type="number" min="1" max="8" step="1" value={form.max_legs} onChange={event => setForm({...form, max_legs: event.target.value})}/></label>
        <label>Odds tolerance<input type="number" min="0.01" max="0.5" step="0.01" value={form.odds_tolerance} onChange={event => setForm({...form, odds_tolerance: event.target.value})}/></label>
        <label>Odds freshness, minutes<input type="number" min="1" max="1440" step="1" value={form.odds_max_age_minutes} onChange={event => setForm({...form, odds_max_age_minutes: event.target.value})}/></label>
        <label>Form half-life, days<input type="number" min="1" max="3650" step="1" value={form.form_half_life_days} onChange={event => setForm({...form, form_half_life_days: event.target.value})}/></label>
        <button className="ae-btn primary" disabled={busy}>Save risk controls</button>
        {saved && <span className="ae-saved">{saved}</span>}
      </form>
      <p className="ae-note">These limits are the stored engine settings. Pause and Stop disable the scheduler rows. They do not activate a model and they do not place a wager.</p>
    </section>}

    <footer className="ae-footer">
      <span><Clock3 size={18}/> Next cycle <b>{clock(data?.next_cycle)}</b></span>
      <span><RefreshCw size={18}/> Refresh <b>Every 30 seconds</b></span>
      <span><Database size={18}/> Sources <b>Stored providers and SportyBet prices</b></span>
      <span><Cpu size={18}/> Models <b>Football and basketball registry</b></span>
      <span><ShieldCheck size={18}/> Wagers <b>Not placed</b></span>
    </footer>
    <p className="ae-footnote">Counts, odds, tickets and model metrics come from the Sports AI database. A blank value has not been stored. No automated wagering is implemented.</p>
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
  return <div className="ae-table-wrap"><table><thead><tr><th>#</th><th>Fixture</th><th>Market</th><th>Odds</th><th>Model P</th><th>Edge</th><th>Confidence</th></tr></thead><tbody>
    {rows.map((row, index) => <tr key={`${row.fixture}-${row.market}`} className="click" onClick={() => onOpen(row)}><td>{index + 1}</td><td>{row.fixture}</td><td>{row.market}</td><td>{Number(row.odds).toFixed(2)}</td><td>{row.probability}%</td><td className="ae-positive">+{row.edge}%</td><td><em className="ae-status neutral">Not stored</em></td></tr>)}
    {!rows.length && <tr><td className="ae-empty" colSpan="7">No qualified opportunity is stored. A row appears after an active validated model and a current verified price produce a positive edge.</td></tr>}
  </tbody></table></div>;
}

function TicketGrid({tickets}) {
  if (!tickets.length) return <p className="ae-empty">No ticket suggestion is stored.</p>;
  return <div className="ae-ticket-grid">{tickets.map(ticket => <article className="ae-ticket" key={ticket.id}><h3><Ticket size={16}/>{ticket.type}</h3><small>{ticket.id}</small>{ticket.booking_code ? <p className="ae-code">{ticket.booking_code}</p> : <p className="ae-code wait">Awaiting code</p>}{ticket.picks.map(pick => <p key={pick}>{pick}</p>)}<div><strong>{ticket.odds == null ? 'Odds not stored' : `Total odds ${Number(ticket.odds).toFixed(2)}`}</strong><span>{ticket.status}</span></div></article>)}</div>;
}
