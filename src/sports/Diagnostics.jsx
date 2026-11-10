import React, {useEffect, useMemo, useState} from 'react';
import {
  AlertCircle, AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, Clock3, Database, ExternalLink, Globe,
  KeyRound, MinusCircle, PauseCircle, Play, PlugZap, RefreshCw, Search, Server, ShieldCheck, Ticket, XCircle,
} from 'lucide-react';
import {useLiveReload} from './live';
import {get, post} from './api';
import './diagnostics.css';

const TONE = {healthy: 'success', stale: 'warning', paused: 'warning', awaiting: 'warning', failing: 'danger',
  disabled: 'neutral', not_configured: 'neutral'};
const ICON = {healthy: CheckCircle2, stale: Clock3, paused: PauseCircle, awaiting: Clock3, failing: XCircle,
  disabled: MinusCircle, not_configured: KeyRound};
const STATE_ORDER = ['failing', 'paused', 'stale', 'awaiting', 'healthy', 'not_configured', 'disabled'];
const STATE_LABEL = {failing: 'Failing', paused: 'Paused', stale: 'Stale', awaiting: 'Awaiting', healthy: 'Healthy',
  not_configured: 'Not configured', disabled: 'Not enabled'};

function absolute(value) {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
}

function relative(value) {
  if (!value) return 'Never';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  const seconds = Math.round((Date.now() - parsed.getTime()) / 1000);
  const future = seconds < 0;
  const s = Math.abs(seconds);
  const text = s < 60 ? `${s}s` : s < 3600 ? `${Math.round(s / 60)} min` : s < 86400 ? `${Math.round(s / 3600)} h`
    : `${Math.round(s / 86400)} d`;
  return future ? `in ${text}` : `${text} ago`;
}

function StatePill({state, label}) {
  const Icon = ICON[state] || MinusCircle;
  return <span className={`sp-status ${TONE[state] || 'neutral'} sp-dg-pill`}><Icon size={12} aria-hidden="true" />{label || STATE_LABEL[state] || state}</span>;
}

function Tile({icon: Icon, label, value, sub, state}) {
  return (
    <div className={`sp-dg-tile ${TONE[state] || 'neutral'}`}>
      <div className="sp-dg-tile-icon"><Icon size={18} aria-hidden="true" /></div>
      <div className="sp-dg-tile-body">
        <span>{label}</span>
        <strong>{value ?? '—'}</strong>
        {sub && <small>{sub}</small>}
      </div>
      {state && <StatePill state={state} label={STATE_LABEL[state] === 'Healthy' ? 'OK' : undefined} />}
    </div>
  );
}

function host(url) {
  try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return null; }
}

export default function Diagnostics({tab = 0}) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState({});

  async function load(silent = false) {
    if (!silent) setLoading(true);
    try {
      setReport(await get('/operations/diagnostics'));
      setError('');
    } catch (loadError) {
      setError(loadError.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);
  useLiveReload(() => load(true));

  async function act(provider, action) {
    const key = `${provider.id}:${action}`;
    setBusy(current => ({...current, [key]: true}));
    setNotice(null);
    try {
      const result = await post(`/acquisition/sources/${provider.id}/${action}`, {});
      const text = action === 'test' ? (result.message || 'Connection succeeded.')
        : action === 'collect' ? `${result.records_saved ?? 0} record(s) saved from ${provider.name}.`
        : `${provider.name} resumed. The scheduler will collect on its next pass.`;
      setNotice({tone: 'success', text});
      await load(true);
    } catch (actionError) {
      setNotice({tone: 'danger', text: `${provider.name}: ${actionError.message}`});
      await load(true);
    } finally {
      setBusy(current => ({...current, [key]: false}));
    }
  }

  if (loading && !report) {
    return <div className="sp-dg-loading" role="status"><RefreshCw size={18} className="sp-dg-spin" aria-hidden="true" />Checking backend, database, scheduler and providers…</div>;
  }
  if (!report) {
    return (
      <section className="sp-panel sp-dg-offline" role="alert">
        <XCircle size={28} aria-hidden="true" />
        <div><h3>Diagnostics service unavailable</h3><p>{error || 'The backend did not respond.'} If the API was just restarted it may still be applying a migration; this page retries automatically.</p></div>
        <button className="sp-secondary" onClick={() => load()}><RefreshCw size={15} />Retry now</button>
      </section>
    );
  }

  const {scheduler, sportybet, browser, fixtures, database} = report;
  return (
    <div className="sp-intelligence-stack sp-dg">
      <section className="sp-panel">
        <div className="sp-panel-head">
          <div>
            <h3>System status</h3>
            <p>{report.note} Updated {relative(report.generated_at)}.</p>
          </div>
          <button className="sp-secondary" onClick={() => load()} disabled={loading}><RefreshCw size={15} className={loading ? 'sp-dg-spin' : ''} />{loading ? 'Refreshing…' : 'Refresh'}</button>
        </div>
        {error && <p className="sp-ops-error" role="alert"><AlertCircle size={16} />Last refresh failed: {error}. Showing the previous report.</p>}
        <div className="sp-dg-tiles">
          <Tile icon={Server} label="Backend API" value={report.backend.label} sub={`Data mode: ${report.data_mode}`} state={report.backend.state} />
          <Tile icon={Database} label="Database" value={database.label} sub="Write-ahead logging keeps readers unblocked" state={database.state} />
          <Tile icon={Clock3} label="Scheduler" value={scheduler.label} sub={scheduler.heartbeat_at ? `Heartbeat ${relative(scheduler.heartbeat_at)} · ${scheduler.owner}` : 'No heartbeat recorded'} state={scheduler.state} />
          <Tile icon={Globe} label="Browser runtime" value={browser.chromium ? 'Chromium ready' : 'Unavailable'} sub={browser.chromium ? `${report.approved_browser_hosts} approved host(s)` : browser.detail} state={browser.chromium ? 'healthy' : 'failing'} />
          <Tile icon={Ticket} label="SportyBet" value={sportybet.label} sub={`Last price ${relative(sportybet.last_price_at)} · ${sportybet.codes_24h} code(s) in 24 h`} state={sportybet.state} />
          <Tile icon={PlugZap} label="Fixtures stored" value={Number(fixtures.total).toLocaleString()} sub={`${Number(fixtures.upcoming).toLocaleString()} upcoming · ${Number(fixtures.football).toLocaleString()} football · ${Number(fixtures.basketball).toLocaleString()} basketball`} />
        </div>
      </section>

      {notice && <div className={`sp-dg-notice ${notice.tone}`} role="status" aria-live="polite">{notice.tone === 'success' ? <CheckCircle2 size={16} /> : <AlertCircle size={16} />}<span>{notice.text}</span><button onClick={() => setNotice(null)} aria-label="Dismiss">×</button></div>}

      {report.issues.length > 0 && tab === 0 && (
        <section className="sp-panel sp-dg-issues">
          <div className="sp-panel-head"><div><h3>Needs attention</h3><p>{report.issues.length} item(s). Each fix calls the same endpoint as the provider table.</p></div><AlertTriangle size={20} className="sp-dg-warn" aria-hidden="true" /></div>
          <ul>
            {report.issues.map((issue, index) => {
              const provider = report.providers.find(p => p.id === issue.provider_id);
              return (
                <li key={index} className={issue.tone}>
                  <div><strong>{issue.title}</strong><span>{issue.detail}</span></div>
                  {provider && issue.action && (
                    <button className="sp-secondary" disabled={busy[`${provider.id}:${issue.action}`]} onClick={() => act(provider, issue.action)}>
                      {issue.action === 'resume' ? <Play size={14} /> : <PlugZap size={14} />}
                      {busy[`${provider.id}:${issue.action}`] ? 'Working…' : issue.action === 'resume' ? 'Resume schedule' : issue.action === 'test' ? 'Test connection' : 'Collect now'}
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {tab === 0 && <Providers report={report} busy={busy} onAction={act} />}
      {tab === 1 && <Runtime report={report} />}
      {tab === 2 && <CollectionLog report={report} />}
    </div>
  );
}

function Providers({report, busy, onAction}) {
  const [query, setQuery] = useState('');
  const [state, setState] = useState('');
  const [sport, setSport] = useState('');
  const [open, setOpen] = useState(null);
  const counts = report.provider_counts || {};
  const rows = useMemo(() => report.providers
    .filter(p => (!state || p.state === state) && (!sport || p.sport === sport)
      && (!query || `${p.name} ${p.channel} ${p.source_url || ''}`.toLowerCase().includes(query.toLowerCase())))
    .sort((a, b) => STATE_ORDER.indexOf(a.state) - STATE_ORDER.indexOf(b.state) || a.name.localeCompare(b.name)),
  [report, query, state, sport]);

  return (
    <section className="sp-panel">
      <div className="sp-panel-head">
        <div><h3>Data providers</h3><p>{report.providers.length} registered sources, each listed once. Browser and SportyBet sources run on their own worker passes, so only scheduled API providers can be tested or collected on demand.</p></div>
      </div>
      <div className="sp-dg-toolbar">
        <div className="sp-dg-chips" role="group" aria-label="Filter by status">
          <button className={!state ? 'active' : ''} onClick={() => setState('')}>All <b>{report.providers.length}</b></button>
          {STATE_ORDER.filter(s => counts[s]).map(s => (
            <button key={s} className={state === s ? 'active' : ''} onClick={() => setState(state === s ? '' : s)}>
              <span className={`sp-dg-dot ${TONE[s]}`} aria-hidden="true" />{STATE_LABEL[s]} <b>{counts[s]}</b>
            </button>
          ))}
        </div>
        <div className="sp-dg-filters">
          <label className="sp-dg-search"><Search size={15} aria-hidden="true" /><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search providers" aria-label="Search providers" /></label>
          <select value={sport} onChange={e => setSport(e.target.value)} aria-label="Filter by sport">
            <option value="">All sports</option><option value="football">Football</option><option value="basketball">Basketball</option><option value="both">Multi-sport</option>
          </select>
        </div>
      </div>
      <div className="sp-table-scroll">
        <table className="sp-table sp-dg-table">
          <thead><tr><th aria-label="Expand" /><th>Provider</th><th>Status</th><th>Channel</th><th>Last success</th><th>Next run</th><th>Credential</th><th>Actions</th></tr></thead>
          <tbody>
            {rows.map(p => {
              const expanded = open === p.id;
              return (
                <React.Fragment key={p.id}>
                  <tr className={expanded ? 'sp-dg-open' : ''}>
                    <td><button className="sp-dg-expand" onClick={() => setOpen(expanded ? null : p.id)} aria-expanded={expanded} aria-label={`Details for ${p.name}`}>{expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</button></td>
                    <td>
                      <div className="sp-dg-name"><strong>{p.name}</strong><span>{p.sport_label}{host(p.source_url) ? ` · ${host(p.source_url)}` : ''}</span></div>
                    </td>
                    <td><StatePill state={p.state} label={p.label} /></td>
                    <td>{p.channel}</td>
                    <td title={absolute(p.last_success_at)}>{relative(p.last_success_at)}</td>
                    <td title={absolute(p.next_run_at)}>{p.state === 'paused' ? 'Paused' : p.next_run_at ? relative(p.next_run_at) : '—'}</td>
                    <td><span className={`sp-dg-cred ${p.credential === 'Missing' ? 'missing' : ''}`}>{p.credential === 'Missing' ? <KeyRound size={12} /> : <ShieldCheck size={12} />}{p.credential}</span></td>
                    <td>
                      <div className="sp-dg-actions">
                        {p.actions.resume && <button className="sp-secondary" disabled={busy[`${p.id}:resume`]} onClick={() => onAction(p, 'resume')}><Play size={13} />{busy[`${p.id}:resume`] ? '…' : 'Resume'}</button>}
                        {p.actions.test && <button className="sp-secondary" disabled={busy[`${p.id}:test`]} onClick={() => onAction(p, 'test')}><PlugZap size={13} />{busy[`${p.id}:test`] ? 'Testing…' : 'Test'}</button>}
                        {p.actions.collect && <button className="sp-secondary" disabled={busy[`${p.id}:collect`]} onClick={() => onAction(p, 'collect')}><RefreshCw size={13} />{busy[`${p.id}:collect`] ? 'Collecting…' : 'Collect'}</button>}
                        {p.source_url && <a className="sp-dg-link" href={p.source_url} target="_blank" rel="noreferrer noopener" aria-label={`Open ${p.name} source`}><ExternalLink size={14} /></a>}
                        {!p.actions.resume && !p.actions.test && !p.actions.collect && <span className="sp-dg-muted">Worker managed</span>}
                      </div>
                    </td>
                  </tr>
                  {expanded && (
                    <tr className="sp-dg-detail-row">
                      <td colSpan="8">
                        <div className="sp-dg-detail">
                          <p>{p.detail}</p>
                          <dl>
                            <div><dt>Adapter</dt><dd>{p.adapter}</dd></div>
                            <div><dt>Terms reviewed</dt><dd>{p.terms_reviewed ? 'Yes' : 'No'}</dd></div>
                            <div><dt>Enabled</dt><dd>{p.enabled ? 'Yes' : 'No'}</dd></div>
                            <div><dt>Interval</dt><dd>{p.interval_minutes ? `${p.interval_minutes} min` : '—'}</dd></div>
                            <div><dt>Last attempt</dt><dd>{absolute(p.last_attempt_at)}</dd></div>
                            <div><dt>Last success</dt><dd>{absolute(p.last_success_at)}</dd></div>
                            {p.schedule && <div><dt>Schedule</dt><dd>{p.schedule.paused ? 'Paused' : p.schedule.enabled ? 'Enabled' : 'Disabled'} · {p.schedule.updated_by || '—'} · {absolute(p.schedule.updated_at)}</dd></div>}
                            {p.credential_env && <div><dt>Credential variable</dt><dd><code>{p.credential_env}</code></dd></div>}
                          </dl>
                          {p.last_error && <p className="sp-dg-error"><AlertCircle size={14} />{p.last_error}</p>}
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
            {!rows.length && <tr><td colSpan="8" className="sp-empty">No provider matches these filters.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Runtime({report}) {
  const {browser, sportybet, scheduler} = report;
  return (
    <div className="sp-dg-cards">
      <section className="sp-panel">
        <div className="sp-dg-card-head"><Globe size={18} /><h3>Browser runtime</h3><StatePill state={browser.chromium ? 'healthy' : 'failing'} label={browser.chromium ? 'Ready' : 'Unavailable'} /></div>
        <dl className="sp-dg-dl">
          <div><dt>Playwright</dt><dd>{browser.installed ? 'Installed' : 'Not installed'}</dd></div>
          <div><dt>Chromium</dt><dd>{browser.chromium ? 'Available' : 'Missing'}</dd></div>
          <div><dt>Approved hosts</dt><dd>{report.approved_browser_hosts}</dd></div>
          <div><dt>Checked</dt><dd>{absolute(browser.checked_at)}</dd></div>
        </dl>
        {browser.detail && <p className="sp-dg-error"><AlertCircle size={14} />{browser.detail}</p>}
        <p className="sp-muted">Pages are opened only on approved hosts that robots.txt allows. Access challenges stop collection; nothing bypasses them.</p>
      </section>
      <section className="sp-panel">
        <div className="sp-dg-card-head"><Ticket size={18} /><h3>SportyBet</h3><StatePill state={sportybet.state} label={sportybet.label} /></div>
        <dl className="sp-dg-dl">
          <div><dt>Last displayed price</dt><dd>{absolute(sportybet.last_price_at)}</dd></div>
          <div><dt>Codes in 24 h</dt><dd>{sportybet.codes_24h}</dd></div>
          <div><dt>Last code</dt><dd>{absolute(sportybet.last_code_at)}</dd></div>
          <div><dt>Wager placement</dt><dd>Never</dd></div>
        </dl>
        <p className="sp-muted">{sportybet.detail}</p>
      </section>
      <section className="sp-panel">
        <div className="sp-dg-card-head"><Clock3 size={18} /><h3>Scheduler</h3><StatePill state={scheduler.state} label={scheduler.label} /></div>
        <dl className="sp-dg-dl">
          <div><dt>Leader</dt><dd>{scheduler.owner || '—'}</dd></div>
          <div><dt>Heartbeat</dt><dd>{absolute(scheduler.heartbeat_at)}</dd></div>
          <div><dt>Active API schedules</dt><dd>{scheduler.enabled_provider_schedules}</dd></div>
          <div><dt>Paused API schedules</dt><dd>{scheduler.paused_provider_schedules}</dd></div>
        </dl>
        <p className="sp-muted">{scheduler.detail}</p>
      </section>
    </div>
  );
}

function CollectionLog({report}) {
  const jobs = report.recent_collections || [];
  const tone = {complete: 'healthy', partial: 'stale', failed: 'failing', running: 'awaiting'};
  return (
    <div className="sp-intelligence-stack">
      <section className="sp-panel">
        <div className="sp-panel-head"><div><h3>API collection runs</h3><p>The 25 most recent scheduled or manual API collections. Browser history passes report on the Autonomy Health page.</p></div></div>
        <div className="sp-table-scroll">
          <table className="sp-table">
            <thead><tr><th>Provider</th><th>Status</th><th>Seen</th><th>Saved</th><th>Started</th><th>Duration</th><th>Error</th></tr></thead>
            <tbody>
              {jobs.map(job => (
                <tr key={job.id}>
                  <td><strong>{job.provider}</strong><small className="sp-intel-subline">{job.kind}</small></td>
                  <td><StatePill state={tone[job.status] || 'disabled'} label={job.status} /></td>
                  <td>{job.records_seen}</td>
                  <td>{job.records_saved}</td>
                  <td title={absolute(job.started_at)}>{relative(job.started_at)}</td>
                  <td>{job.duration_seconds == null ? '—' : `${job.duration_seconds}s`}</td>
                  <td className="sp-dg-errcell">{job.error || '—'}</td>
                </tr>
              ))}
              {!jobs.length && <tr><td colSpan="7" className="sp-empty">No API collection has run yet.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
      <section className="sp-panel">
        <div className="sp-panel-head"><div><h3>Stored fixtures by source</h3><p>{Number(report.fixtures.total).toLocaleString()} fixtures across {report.fixtures.by_source.length} source/sport pairs.</p></div></div>
        <div className="sp-table-scroll">
          <table className="sp-table">
            <thead><tr><th>Source</th><th>Sport</th><th>Fixtures</th><th>Share</th></tr></thead>
            <tbody>
              {report.fixtures.by_source.map(row => {
                const share = report.fixtures.total ? row.fixtures / report.fixtures.total : 0;
                return (
                  <tr key={`${row.sport}-${row.source}`}>
                    <td><strong>{row.source}</strong></td><td>{row.sport}</td><td>{Number(row.fixtures).toLocaleString()}</td>
                    <td><div className="sp-dg-share"><span style={{width: `${Math.max(share * 100, 1)}%`}} /></div><small className="sp-intel-subline">{(share * 100).toFixed(1)}%</small></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
