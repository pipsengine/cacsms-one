import React, { useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, Clock3, Eye, Globe2, Pause, Play, Plus, RefreshCw, RotateCw, Save, ShieldCheck, XCircle } from 'lucide-react';
import { get, post, put } from './api';
import { parseCsv } from './Intelligence';
import CatalogCentre from './CatalogCentre';
import './acquisition.css';

const providerChoices = [
  ['football_data', 'football-data.org'],
  ['openligadb', 'OpenLigaDB'],
  ['openfootball', 'OpenFootball'],
  ['balldontlie', 'BALLDONTLIE'],
  ['sportsdataverse_csv', 'SportsDataverse dataset CSV'],
  ['the_odds_api', 'The Odds API'],
  ['authorized_json', 'Authorized JSON feed'],
];

const supportedFields = ['h2h', 'form', 'standings', 'statistics', 'odds'];
const screenshotBase = '/api/sports-ai/acquisition/browser/sessions';
const formatDate = (value) => value ? new Date(value).toLocaleString() : 'Not yet';

function Status({ value }) {
  const tone = value === 'operational' || value === 'complete' || value === 'approved'
    ? 'success'
    : value === 'error' || value === 'failed' || value === 'disabled'
      ? 'warning'
      : 'neutral';
  return <span className={`sp-status ${tone}`}>{value || 'untested'}</span>;
}

function Feedback({ value, error }) {
  if (!value && !error) return null;
  return <p className={error ? 'sp-ops-error' : 'sp-acq-feedback'} role="status">{error || value}</p>;
}

function AddSource({ onCreated }) {
  const [form, setForm] = useState({ source_key: '', display_name: '', adapter: 'openligadb', config: '{}' });
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await post('/acquisition/sources', {
        source_key: form.source_key,
        display_name: form.display_name,
        adapter: form.adapter,
        config: JSON.parse(form.config),
      });
      setMessage('Source registered disabled. Review its terms, configure credentials and test before activation.');
      setForm({ source_key: '', display_name: '', adapter: 'openligadb', config: '{}' });
      await onCreated();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="sp-panel sp-acq-form" onSubmit={submit}>
      <h3>Add provider source</h3>
      <div className="sp-form-grid">
        <label>Source key<input required pattern="[a-z0-9][a-z0-9_-]+" value={form.source_key} onChange={(event) => setForm({ ...form, source_key: event.target.value })} /></label>
        <label>Display name<input required value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} /></label>
        <label>Provider adapter<select value={form.adapter} onChange={(event) => setForm({ ...form, adapter: event.target.value })}>{providerChoices.map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label>
        <label>Adapter configuration (JSON)<textarea required rows="3" value={form.config} onChange={(event) => setForm({ ...form, config: event.target.value })} /></label>
      </div>
      <button className="sp-primary" disabled={busy}><Plus size={16} />{busy ? 'Adding…' : 'Add Source'}</button>
      <Feedback value={message} error={error} />
    </form>
  );
}

function DatasetImport({ source, onImported }) {
  const [file, setFile] = useState(null);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!file) return;
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const rows = parseCsv(await file.text());
      const result = await post(`/providers/${encodeURIComponent(source.id)}/basketball-import`, { rows });
      setMessage(`${result.imported} real result row(s) stored.`);
      await onImported();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }

  return <div className="sp-form-grid">
    <label>Licensed basketball results CSV<input type="file" accept=".csv,text/csv" onChange={(event) => setFile(event.target.files?.[0] || null)} /></label>
    <div className="sp-actions">
      <button className="sp-secondary" disabled={!file || busy || !source.enabled || !source.terms_reviewed} onClick={submit}><Upload size={15} />{busy ? 'Importing…' : 'Import Results'}</button>
      <Feedback value={message} error={error} />
    </div>
  </div>;
}

function ProviderRegistry({ reload, onReload }) {
  const [sources, setSources] = useState([]);
  const [configs, setConfigs] = useState({});
  const [messages, setMessages] = useState({});
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState({});

  async function refresh() {
    const records = await get('/acquisition/providers');
    setSources(records);
    setConfigs(Object.fromEntries(records.map((source) => [source.id, {
      configText: JSON.stringify(source.config, null, 2),
      termsReviewed: source.terms_reviewed,
      enabled: source.enabled,
      interval: source.interval_minutes,
    }])));
  }

  useEffect(() => { refresh().catch((error) => setErrors({ load: error.message })); }, [reload]);

  function update(id, key, value) {
    setConfigs((current) => ({ ...current, [id]: { ...current[id], [key]: value } }));
  }

  async function save(source) {
    const form = configs[source.id];
    setBusy((current) => ({ ...current, [source.id]: true }));
    setErrors((current) => ({ ...current, [source.id]: '' }));
    try {
      const config = JSON.parse(form.configText);
      await put(`/acquisition/sources/${source.id}`, {
        config,
        terms_reviewed: form.termsReviewed,
        enabled: form.enabled,
        interval_minutes: Number(form.interval),
      });
      setMessages((current) => ({ ...current, [source.id]: 'Source settings saved.' }));
      await refresh();
    } catch (error) {
      setErrors((current) => ({ ...current, [source.id]: error.message }));
    } finally {
      setBusy((current) => ({ ...current, [source.id]: false }));
    }
  }

  async function runAction(source, action) {
    setBusy((current) => ({ ...current, [source.id]: true }));
    setErrors((current) => ({ ...current, [source.id]: '' }));
    try {
      if (action === 'test') {
        const result = await post(`/acquisition/sources/${source.id}/test`, {});
        setMessages((current) => ({ ...current, [source.id]: result.message }));
      } else {
        await post(`/acquisition/sources/${source.id}/${action}`, {});
        setMessages((current) => ({ ...current, [source.id]: action === 'disable' ? 'Source disabled.' : 'Source paused.' }));
      }
      await refresh();
      await onReload();
    } catch (error) {
      setErrors((current) => ({ ...current, [source.id]: error.message }));
    } finally {
      setBusy((current) => ({ ...current, [source.id]: false }));
    }
  }

  async function collect(source) {
    setBusy((current) => ({ ...current, [source.id]: true }));
    setErrors((current) => ({ ...current, [source.id]: '' }));
    try {
      const job = await post(`/acquisition/sources/${source.id}/collect`, {});
      setMessages((current) => ({ ...current, [source.id]: `${job.status}: ${job.records_saved} record(s) persisted (job ${job.job_id}).` }));
      await onReload();
    } catch (error) {
      setErrors((current) => ({ ...current, [source.id]: error.message }));
    } finally {
      setBusy((current) => ({ ...current, [source.id]: false }));
    }
  }

  return (
    <div className="sp-acq-stack">
      <div className="sp-panel-head">
        <div><h3>Provider Registry</h3><p>Credentials are referenced by environment-variable name only; secrets are never stored in SQLite.</p></div>
        <button className="sp-secondary" onClick={refresh}><RefreshCw size={15} />Refresh Registry</button>
      </div>
      {errors.load && <Feedback error={errors.load} />}
      {sources.map((source) => {
        const form = configs[source.id] || {};
        const browser = source.adapter === 'browser';
        const manual = source.adapter === 'csv_import';
        return (
          <section className="sp-panel sp-acq-provider" key={source.id}>
            <div className="sp-panel-head">
              <div><h3>{source.display_name}</h3><p>{source.sport} · {source.adapter} · <a href={source.terms_url} target="_blank" rel="noreferrer">Review provider terms</a></p></div>
              <Status value={source.status} />
            </div>
            <div className="sp-acq-facts">
              <span>Activation: {source.enabled ? 'Enabled' : 'Disabled'}</span>
              <span>Terms reviewed: {source.terms_reviewed ? 'Yes' : 'No'}</span>
              <span>API credential: {source.requires_api_key ? (source.credential_configured ? 'Configured in environment' : `Set ${source.credential_env}`) : 'Not required'}</span>
              <span>Last success: {formatDate(source.last_success_at)}</span>
              {source.sync_info?.credits_remaining && <span>API credits remaining: {source.sync_info.credits_remaining}</span>}
            </div>
            {!browser ? (
              <div className="sp-form-grid">
                <label>Adapter configuration JSON<textarea rows="4" value={form.configText || '{}'} onChange={(event) => update(source.id, 'configText', event.target.value)} /></label>
                <label>Collection interval (minutes)<input type="number" min="5" max="10080" value={form.interval || 360} onChange={(event) => update(source.id, 'interval', event.target.value)} /></label>
                <label className="sp-acq-check"><input type="checkbox" checked={Boolean(form.termsReviewed)} onChange={(event) => update(source.id, 'termsReviewed', event.target.checked)} />I reviewed the provider's current terms and permitted use.</label>
                <label className="sp-acq-check"><input type="checkbox" checked={Boolean(form.enabled)} disabled={!form.termsReviewed} onChange={(event) => update(source.id, 'enabled', event.target.checked)} />Enable scheduled collection.</label>
              </div>
            ) : <p className="sp-muted">BetExplorer is a research reference only. Browser extraction remains off until its hostname has explicit administrator permission.</p>}
            {manual && source.terms_reviewed && <DatasetImport source={source} onImported={async () => { await refresh(); await onReload(); }} />}
            <div className="sp-actions sp-acq-actions">
              <button className="sp-secondary" disabled={busy[source.id] || browser || manual} onClick={() => runAction(source, 'test')}><CheckCircle2 size={15} />Test Connection</button>
              <button className="sp-primary" disabled={busy[source.id] || browser || manual || !source.enabled} onClick={() => collect(source)}><Play size={15} />Run Collection</button>
              <button className="sp-secondary" disabled={busy[source.id]} onClick={() => save(source)}><Save size={15} />Save Settings</button>
              <button className="sp-secondary" disabled={busy[source.id]} onClick={() => runAction(source, 'pause')}><Pause size={15} />Pause</button>
              <button className="sp-text-action" disabled={busy[source.id]} onClick={() => runAction(source, 'disable')}>Disable Source</button>
            </div>
            <Feedback value={messages[source.id]} error={errors[source.id]} />
          </section>
        );
      })}
      <AddSource onCreated={refresh} />
      <p className="sp-muted">A successful connection test is not a collection. A source is marked operational only after records are actually persisted.</p>
    </div>
  );
}

function WebsiteExplorer() {
  const [query, setQuery] = useState('');
  const [urls, setUrls] = useState('');
  const [fixtureId, setFixtureId] = useState('');
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function explore(event) {
    event.preventDefault();
    setBusy(true);
    setError('');
    setResult(null);
    try {
      const pages = urls.split(/\r?\n/).map((url) => url.trim()).filter(Boolean);
      setResult(await post('/acquisition/browser/research', {
        query,
        urls: pages,
        fixture_id: fixtureId || null,
      }));
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="sp-panel sp-acq-form">
      <h3>Website Explorer</h3>
      <p className="sp-muted">Research only explicitly approved HTTPS pages. This operator does not sign in, solve CAPTCHAs, bypass paywalls or evade anti-bot controls.</p>
      <form onSubmit={explore}>
        <div className="sp-form-grid">
          <label>Research question<input required value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Find this match's standings or recent form" /></label>
          <label>Match ID (optional)<input value={fixtureId} onChange={(event) => setFixtureId(event.target.value)} placeholder="Associate evidence with a stored fixture" /></label>
          <label>Approved HTTPS page URLs<textarea required rows="4" value={urls} onChange={(event) => setUrls(event.target.value)} placeholder="https://approved.example/match/..." /></label>
        </div>
        <button className="sp-primary" disabled={busy}><Globe2 size={16} />{busy ? 'Researching…' : 'Start Browser Research'}</button>
      </form>
      <Feedback error={error} />
      {result && <div className="sp-acq-result">
        <Status value={result.status} /><p>Session: {result.session_id}</p>
        {result.pages?.map((page) => <article key={page.url}><h4>{page.title || page.url}</h4><a href={page.url} target="_blank" rel="noreferrer">{page.url}</a><p>{page.text}</p>{page.screenshot && <img src={`${screenshotBase}/${result.session_id}/screenshots/page-${result.pages.indexOf(page) + 1}.png`} alt={`Diagnostic screenshot of ${page.url}`} />}</article>)}
      </div>}
    </div>
  );
}

function BrowserSessions({ reload }) {
  const [sessions, setSessions] = useState([]);
  const [selected, setSelected] = useState(null);
  const [evidence, setEvidence] = useState([]);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  async function refresh() {
    setSessions(await get('/acquisition/browser/sessions'));
  }
  useEffect(() => {
    refresh().catch((requestError) => setError(requestError.message));
  }, [reload]);

  async function retry(session) {
    try {
      const result = await post(`/acquisition/browser/sessions/${session.id}/retry`, {});
      setMessage(`Retry ${result.status}: ${result.session_id}`);
      await refresh();
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  async function viewEvidence(id) {
    setSelected(id);
    try {
      setEvidence(await get(`/acquisition/browser/sessions/${id}/evidence`));
      setError('');
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  return <section className="sp-panel">
    <div className="sp-panel-head"><div><h3>Browser Sessions</h3><p>Page visits, extracted evidence and diagnostic screenshots.</p></div><Status value={`${sessions.length} sessions`} /></div>
    {error && <Feedback error={error} />}
    {message && <Feedback value={message} />}
    {!sessions.length && <p className="sp-muted">No browser research sessions are recorded.</p>}
    {sessions.map((session) => <article className="sp-acq-history" key={session.id}>
      <div><strong>{session.query}</strong><p>{formatDate(session.started_at)} · {session.pages.length} page(s) · <Status value={session.status} /></p></div>
      <button className="sp-secondary" onClick={() => viewEvidence(session.id)}><Eye size={14} />View Evidence</button>
      {(session.status === 'failed' || session.status === 'partial') && <button className="sp-secondary" onClick={() => retry(session)}><RotateCw size={14} />Retry</button>}
      {session.error && <p className="sp-ops-error">{session.error}</p>}
      {session.pages.map((page, index) => <div key={`${session.id}-${page.url}`}><a href={page.url} target="_blank" rel="noreferrer">{page.title || page.url}</a>{page.error && <p className="sp-ops-error">{page.error}</p>}{page.screenshot && <img className="sp-acq-thumb" src={`${screenshotBase}/${session.id}/screenshots/page-${index + 1}.png`} alt="Browser diagnostic screenshot" />}</div>)}
    </article>)}
    {selected && <div className="sp-acq-result"><h4>Extracted evidence</h4>{evidence.map((entry) => <div key={entry.id}><strong>{entry.field}</strong> · {entry.captured_at}<p>{entry.value_json}</p><a href={entry.source_url} target="_blank" rel="noreferrer">{entry.source_url}</a></div>)}</div>}
  </section>;
}

function ExtractionConfiguration() {
  const [sources, setSources] = useState([]);
  const [sourceId, setSourceId] = useState('');
  const [fields, setFields] = useState({ h2h: '', form: '', standings: '', statistics: '', odds: '' });
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    get('/acquisition/providers').then((items) => {
      const browsers = items.filter((item) => item.adapter === 'browser');
      setSources(browsers);
      if (browsers[0]) setSourceId(browsers[0].id);
      return get('/acquisition/extraction-config');
    }).then((configs) => {
      if (configs[0]) setFields({ ...fields, ...configs[0].fields });
    }).catch((requestError) => setError(requestError.message));
  }, []);

  async function save(event) {
    event.preventDefault();
    try {
      await put(`/acquisition/extraction-config/${sourceId}`, {
        fields: Object.fromEntries(Object.entries(fields).filter(([, selector]) => selector.trim())),
      });
      setMessage('Extraction selectors saved. They run only for approved pages.');
      setError('');
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  return <form className="sp-panel sp-acq-form" onSubmit={save}>
    <h3>Extraction Configuration</h3>
    <p className="sp-muted">CSS selectors collect visible, user-authorized content only. Keep selectors scoped to requested match data.</p>
    <label>Browser reference<select value={sourceId} onChange={(event) => setSourceId(event.target.value)}>{sources.map((source) => <option key={source.id} value={source.id}>{source.display_name}</option>)}</select></label>
    <div className="sp-form-grid">{supportedFields.map((field) => <label key={field}>{field.toUpperCase()} selector<input value={fields[field] || ''} onChange={(event) => setFields({ ...fields, [field]: event.target.value })} placeholder={`CSS selector for ${field} (optional)`} /></label>)}</div>
    <button className="sp-primary" disabled={!sourceId}><Save size={16} />Save Extraction Configuration</button>
    <Feedback value={message} error={error} />
  </form>;
}

function DataQuality() {
  const [issues, setIssues] = useState([]);
  const [error, setError] = useState('');
  async function refresh() {
    try {
      setIssues(await get('/acquisition/quality'));
      setError('');
    } catch (requestError) {
      setError(requestError.message);
    }
  }
  useEffect(() => { refresh(); }, []);
  async function resolve(issue) {
    try {
      await post(`/acquisition/quality/${issue.id}/resolve`, {});
      await refresh();
    } catch (requestError) {
      setError(requestError.message);
    }
  }
  return <section className="sp-panel">
    <div className="sp-panel-head"><div><h3>Data Quality</h3><p>Collection failures and records rejected during normalization.</p></div><button className="sp-secondary" onClick={refresh}><RefreshCw size={14} />Refresh</button></div>
    {error && <Feedback error={error} />}
    {!issues.length && <p className="sp-muted">No unresolved data-quality issues.</p>}
    {issues.map((issue) => <div className="sp-acq-history" key={issue.id}><div><strong><AlertTriangle size={15} /> {issue.issue_type} · {issue.severity}</strong><p>{issue.source_name || 'Source unavailable'} · {formatDate(issue.created_at)}</p><pre>{JSON.stringify(JSON.parse(issue.details_json || '{}'), null, 2)}</pre></div><button className="sp-secondary" onClick={() => resolve(issue)}>Resolve</button></div>)}
  </section>;
}

function CollectionScheduler({ reload }) {
  const [sources, setSources] = useState([]);
  const [intervals, setIntervals] = useState({});
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  async function refresh() {
    const items = await get('/acquisition/providers');
    setSources(items.filter((source) => source.adapter !== 'browser'));
    setIntervals(Object.fromEntries(items.map((source) => [source.id, source.interval_minutes])));
  }
  useEffect(() => { refresh().catch((requestError) => setError(requestError.message)); }, [reload]);
  async function save(source) {
    try {
      await put(`/acquisition/sources/${source.id}`, {
        config: source.config,
        enabled: source.enabled,
        terms_reviewed: source.terms_reviewed,
        interval_minutes: Number(intervals[source.id]),
      });
      setMessage(`Schedule saved for ${source.display_name}. Start the acquisition worker to run due sources.`);
      await refresh();
    } catch (requestError) {
      setError(requestError.message);
    }
  }
  return <section className="sp-panel">
    <div className="sp-panel-head"><div><h3>Collection Scheduler</h3><p>Polling runs independently in backend/worker.py. Disabled or unreviewed sources never run.</p></div><Clock3 size={18} /></div>
    {error && <Feedback error={error} />}{message && <Feedback value={message} />}
    {sources.map((source) => <div className="sp-acq-schedule" key={source.id}>
      <div><strong>{source.display_name}</strong><p><Status value={source.status} /> · Next run {formatDate(source.next_run_at)}</p></div>
      <label>Interval (minutes)<input type="number" min="5" max="10080" value={intervals[source.id] || 360} onChange={(event) => setIntervals({ ...intervals, [source.id]: event.target.value })} /></label>
      <span>{source.enabled ? 'Enabled' : 'Paused'}</span>
      <button className="sp-secondary" disabled={!source.terms_reviewed} onClick={() => save(source)}><Save size={14} />Save</button>
    </div>)}
  </section>;
}

function CollectionHistory({ reload }) {
  const [jobs, setJobs] = useState([]);
  const [logs, setLogs] = useState(null);
  const [error, setError] = useState('');
  async function refresh() {
    try {
      setJobs(await get('/acquisition/jobs'));
      setError('');
    } catch (requestError) {
      setError(requestError.message);
    }
  }
  useEffect(() => { refresh(); }, [reload]);
  async function viewLogs(job) {
    try {
      setLogs(await get(`/acquisition/jobs/${job.id}/logs`));
      setError('');
    } catch (requestError) {
      setError(requestError.message);
    }
  }
  async function retry(job) {
    try {
      await post(`/acquisition/jobs/${job.id}/retry`, {});
      await refresh();
    } catch (requestError) {
      setError(requestError.message);
    }
  }
  return <section className="sp-panel">
    <div className="sp-panel-head"><div><h3>Collection History</h3><p>Persisted provider job status, results, errors and retry history.</p></div><button className="sp-secondary" onClick={refresh}><RefreshCw size={14} />Refresh</button></div>
    {error && <Feedback error={error} />}
    {!jobs.length && <p className="sp-muted">No provider collection jobs have run.</p>}
    {jobs.map((job) => <div className="sp-acq-history" key={job.id}>
      <div><strong>{job.source_name} · {job.kind}</strong><p>{formatDate(job.created_at)} · attempts {job.attempts} · <Status value={job.status} /></p>{job.error && <p className="sp-ops-error">{job.error}</p>}</div>
      <div className="sp-actions"><button className="sp-secondary" onClick={() => viewLogs(job)}><Eye size={14} />View Logs</button>{job.status === 'failed' && <button className="sp-secondary" onClick={() => retry(job)}><RotateCw size={14} />Retry</button>}</div>
    </div>)}
    {logs && <div className="sp-acq-result"><button className="sp-text-action" onClick={() => setLogs(null)}><XCircle size={14} />Close Logs</button><pre>{JSON.stringify(logs, null, 2)}</pre></div>}
  </section>;
}

function SourcePermissions() {
  const [permissions, setPermissions] = useState([]);
  const [form, setForm] = useState({ url: '', terms_url: '', notes: '', approved: false });
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  async function refresh() {
    try {
      setPermissions(await get('/acquisition/permissions'));
      setError('');
    } catch (requestError) {
      setError(requestError.message);
    }
  }
  useEffect(() => { refresh(); }, []);
  async function submit(event) {
    event.preventDefault();
    try {
      await put('/acquisition/permissions', form);
      setMessage(form.approved ? 'Permission recorded. Browser visits are still limited to this approved host.' : 'Permission revoked.');
      setForm({ ...form, approved: false });
      await refresh();
    } catch (requestError) {
      setError(requestError.message);
    }
  }
  async function revoke(permission) {
    try {
      await put('/acquisition/permissions', {
        url: `https://${permission.hostname}`,
        terms_url: permission.terms_url,
        notes: permission.notes,
        approved: false,
      });
      await refresh();
    } catch (requestError) {
      setError(requestError.message);
    }
  }
  return <div className="sp-acq-stack">
    <form className="sp-panel sp-acq-form" onSubmit={submit}>
      <h3>Source Permissions</h3>
      <p className="sp-muted">Record an administrator's review before any browser navigation. BetExplorer remains disabled until its terms and permission are reviewed.</p>
      <div className="sp-form-grid">
        <label>HTTPS website URL<input required type="url" value={form.url} onChange={(event) => setForm({ ...form, url: event.target.value })} placeholder="https://www.betexplorer.com/" /></label>
        <label>Terms / permission reference URL<input required type="url" value={form.terms_url} onChange={(event) => setForm({ ...form, terms_url: event.target.value })} /></label>
        <label>Review notes<input value={form.notes} onChange={(event) => setForm({ ...form, notes: event.target.value })} /></label>
        <label className="sp-acq-check"><input type="checkbox" checked={form.approved} onChange={(event) => setForm({ ...form, approved: event.target.checked })} />I am authorized to approve visible-page research on this exact hostname.</label>
      </div>
      <button className="sp-primary"><ShieldCheck size={16} />Save Permission Review</button>
      <Feedback value={message} error={error} />
    </form>
    <section className="sp-panel"><h3>Reviewed hostnames</h3>
      {!permissions.length && <p className="sp-muted">No hostname has been approved.</p>}
      {permissions.map((permission) => <div className="sp-acq-history" key={permission.id}><div><strong>{permission.hostname} · <Status value={permission.approved ? 'approved' : 'revoked'} /></strong><p>{permission.approved_by || 'Not recorded'} · {formatDate(permission.reviewed_at)}</p><p>{permission.notes}</p><a href={permission.terms_url} target="_blank" rel="noreferrer">{permission.terms_url}</a></div>{permission.approved && <button className="sp-secondary" onClick={() => revoke(permission)}>Revoke</button>}</div>)}
    </section>
  </div>;
}

export default function Acquisition({ tab = 0 }) {
  const [reload, setReload] = useState(0);
  function refresh() { setReload((value) => value + 1); }
  if (tab === 1) return <WebsiteExplorer />;
  if (tab === 2) return <BrowserSessions reload={reload} />;
  if (tab === 3) return <ExtractionConfiguration />;
  if (tab === 4) return <DataQuality />;
  if (tab === 5) return <CollectionScheduler reload={reload} />;
  if (tab === 6) return <CollectionHistory reload={reload} />;
  if (tab === 7) return <SourcePermissions />;
  if (tab === 8) return <CatalogCentre view="sports" />;
  if (tab === 9) return <CatalogCentre view="leagues" />;
  if (tab === 10) return <CatalogCentre view="teams" />;
  if (tab === 11) return <CatalogCentre view="coverage" />;
  return <ProviderRegistry reload={reload} onReload={refresh} />;
}
