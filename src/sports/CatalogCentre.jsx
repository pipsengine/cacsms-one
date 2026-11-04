import React, { useEffect, useState } from 'react';
import { get, post, put } from './api';

const formatDate = (value) => value ? new Date(value).toLocaleString() : 'Not yet';

function Feedback({ value, error }) {
  if (!value && !error) return null;
  return <p className={error ? 'sp-ops-error' : 'sp-acq-feedback'} role="status">{error || value}</p>;
}

function Pager({ page, total, pageSize, onPage }) {
  const pages = Math.max(1, Math.ceil((total || 0) / pageSize));
  const from = total ? (page - 1) * pageSize + 1 : 0;
  const to = Math.min(total || 0, page * pageSize);
  return <div className="sp-pager"><span>{from}–{to} of {total || 0}</span><button className="sp-secondary" type="button" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button><span>Page {page} of {pages}</span><button className="sp-secondary" type="button" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button></div>;
}

function useSports() {
  const [sports, setSports] = useState([]);
  useEffect(() => { get('/acquisition/catalog/sports').then(setSports).catch(() => setSports([])); }, []);
  return sports;
}

export default function CatalogCentre({ view }) {
  if (view === 'leagues') return <Leagues />;
  if (view === 'teams') return <Teams />;
  if (view === 'coverage') return <Coverage />;
  return <Sports />;
}

function Sports() {
  const [rows, setRows] = useState([]);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [form, setForm] = useState({ code: '', name: '', notes: '' });
  const [loading, setLoading] = useState(true);

  function refresh() {
    setLoading(true);
    get('/acquisition/catalog/sports').then(setRows).catch((requestError) => setError(requestError.message)).finally(() => setLoading(false));
  }
  useEffect(() => { refresh(); }, []);

  async function submit(event) {
    event.preventDefault();
    setError('');
    setMessage('');
    try {
      await post('/acquisition/catalog/sports', form);
      setForm({ code: '', name: '', notes: '' });
      setMessage('Sport registered. No fixtures were created.');
      refresh();
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  return <div className="sp-acq-stack">
    <section className="sp-panel">
      <h3>Sports registry</h3>
      <p className="sp-muted">Football and basketball already have collectors and models. Other sports stay empty until an authorized feed returns records. Counts below are stored rows, not estimates.</p>
      {loading && <p className="sp-muted">Loading sports…</p>}
      <div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>Sport</th><th>Prediction</th><th>Acquisition</th><th>Leagues</th><th>Teams</th><th>Fixtures</th><th>Notes</th></tr></thead><tbody>
        {rows.map((row) => <tr key={row.code}><td>{row.name}</td><td>{row.prediction_status}</td><td>{row.acquisition_status}</td><td>{row.leagues}</td><td>{row.teams}</td><td>{row.fixtures}</td><td>{row.notes}</td></tr>)}
        {!loading && !rows.length && <tr><td colSpan={7} className="sp-empty">No sports are registered.</td></tr>}
      </tbody></table></div>
    </section>
    <form className="sp-panel sp-acq-form" onSubmit={submit}>
      <h3>Register another sport</h3>
      <p className="sp-muted">This only adds the sport to the registry. Attach an approved JSON feed before any match can be stored.</p>
      <div className="sp-form-grid">
        <label>Code<input required pattern="[a-z][a-z0-9_]+" value={form.code} onChange={(event) => setForm({ ...form, code: event.target.value })} placeholder="handball" /></label>
        <label>Name<input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
        <label>Notes<input value={form.notes} onChange={(event) => setForm({ ...form, notes: event.target.value })} /></label>
      </div>
      <button className="sp-primary" type="submit">Register sport</button>
      <Feedback value={message} error={error} />
    </form>
  </div>;
}

function Leagues() {
  const sports = useSports();
  const [filters, setFilters] = useState({ q: '', sport: '', country: '', freshness: '' });
  const [page, setPage] = useState(1);
  const [payload, setPayload] = useState({ items: [], total: 0, page_size: 50 });
  const [countries, setCountries] = useState([]);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => { get('/acquisition/catalog/countries').then(setCountries).catch(() => setCountries([])); }, []);
  useEffect(() => {
    const params = new URLSearchParams({ page: String(page), page_size: '50' });
    if (filters.q) params.set('q', filters.q);
    if (filters.sport) params.set('sport', filters.sport);
    if (filters.country) params.set('country', filters.country);
    if (filters.freshness) params.set('freshness', filters.freshness);
    let cancel = false;
    setLoading(true);
    get('/acquisition/catalog/leagues?' + params.toString()).then((value) => { if (!cancel) setPayload(value); }).catch((requestError) => { if (!cancel) setError(requestError.message); }).finally(() => { if (!cancel) setLoading(false); });
    return () => { cancel = true; };
  }, [filters, page, message]);

  async function queue(sourceId) {
    setError('');
    setMessage('');
    try {
      const result = await post('/acquisition/catalog/backfill', { source_ids: [sourceId] });
      setMessage(result.queued ? 'Historical collection queued. It runs on the scheduler and does not place a wager.' : 'This source has no historical collector.');
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  return <section className="sp-panel">
    <h3>Leagues and countries</h3>
    <p className="sp-muted">Rows appear only after an authorized provider returns them, or after a collected fixture names its competition. Country is blank when the provider did not send one.</p>
    <div className="sp-catalog-filters">
      <input value={filters.q} placeholder="Search league, season or country" onChange={(event) => { setPage(1); setFilters({ ...filters, q: event.target.value }); }} />
      <select value={filters.sport} onChange={(event) => { setPage(1); setFilters({ ...filters, sport: event.target.value }); }}><option value="">All sports</option>{sports.map((sport) => <option key={sport.code} value={sport.code}>{sport.name}</option>)}</select>
      <select value={filters.country} onChange={(event) => { setPage(1); setFilters({ ...filters, country: event.target.value }); }}><option value="">All countries</option>{countries.map((country) => <option key={country.code} value={country.code}>{country.name}</option>)}</select>
      <select value={filters.freshness} onChange={(event) => { setPage(1); setFilters({ ...filters, freshness: event.target.value }); }}><option value="">Any freshness</option><option value="fresh">Fresh</option><option value="current">Current</option><option value="stale">Stale</option><option value="not_collected">Not collected</option></select>
    </div>
    {!countries.length && <p className="sp-muted">No country has been supplied by a source yet.</p>}
    {loading && <p className="sp-muted">Loading leagues…</p>}
    <Feedback value={message} error={error} />
    <div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>League</th><th>Sport</th><th>Country</th><th>Season</th><th>Source</th><th>Fixtures</th><th>Quality</th><th>Freshness</th><th>Backfill</th></tr></thead><tbody>
      {(payload.items || []).map((row) => <tr key={row.source_id || row.id}><td>{row.name}{row.division ? ` · ${row.division}` : ''}</td><td>{row.sport_code}</td><td>{row.country_name || 'Not provided'}</td><td>{row.season || '—'}</td><td>{row.provider_key || '—'}</td><td>{row.fixture_count}</td><td>{row.quality_score ?? 0}</td><td>{row.freshness}</td><td>{row.source_id ? <button className="sp-secondary" type="button" onClick={() => queue(row.source_id)}>{row.backfill_status || 'Queue'}</button> : '—'}</td></tr>)}
      {!loading && !(payload.items || []).length && <tr><td colSpan={9} className="sp-empty">No leagues have been discovered from an authorized source.</td></tr>}
    </tbody></table></div>
    <Pager page={page} total={payload.total} pageSize={payload.page_size || 50} onPage={setPage} />
  </section>;
}

function Teams() {
  const sports = useSports();
  const [filters, setFilters] = useState({ q: '', sport: '' });
  const [page, setPage] = useState(1);
  const [payload, setPayload] = useState({ items: [], total: 0, page_size: 50 });
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const params = new URLSearchParams({ page: String(page), page_size: '50' });
    if (filters.q) params.set('q', filters.q);
    if (filters.sport) params.set('sport', filters.sport);
    let cancel = false;
    setLoading(true);
    get('/acquisition/catalog/teams?' + params.toString()).then((value) => { if (!cancel) setPayload(value); }).catch((requestError) => { if (!cancel) setError(requestError.message); }).finally(() => { if (!cancel) setLoading(false); });
    return () => { cancel = true; };
  }, [filters, page]);
  return <section className="sp-panel">
    <h3>Canonical teams</h3>
    <p className="sp-muted">A team is created only when a collected fixture or standing names it. Accent-insensitive names share one record.</p>
    <div className="sp-catalog-filters">
      <input value={filters.q} placeholder="Search team" onChange={(event) => { setPage(1); setFilters({ ...filters, q: event.target.value }); }} />
      <select value={filters.sport} onChange={(event) => { setPage(1); setFilters({ ...filters, sport: event.target.value }); }}><option value="">All sports</option>{sports.map((sport) => <option key={sport.code} value={sport.code}>{sport.name}</option>)}</select>
    </div>
    {error && <p className="sp-ops-error" role="alert">{error}</p>}
    {loading && <p className="sp-muted">Loading teams…</p>}
    <div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>Team</th><th>Sport</th><th>Key</th><th>Aliases</th><th>Observed</th><th>Source</th></tr></thead><tbody>
      {(payload.items || []).map((row) => <tr key={row.id}><td>{row.name}</td><td>{row.sport_code}</td><td>{row.canonical_key}</td><td>{row.aliases}</td><td>{formatDate(row.observed_at)}</td><td>{row.source_url ? <a href={row.source_url} target="_blank" rel="noreferrer">View source</a> : '—'}</td></tr>)}
      {!loading && !(payload.items || []).length && <tr><td colSpan={6} className="sp-empty">No team has been named by a collected source.</td></tr>}
    </tbody></table></div>
    <Pager page={page} total={payload.total} pageSize={payload.page_size || 50} onPage={setPage} />
  </section>;
}

function Coverage() {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [perDay, setPerDay] = useState('0');
  const [loading, setLoading] = useState(true);

  function refresh() {
    setLoading(true);
    get('/acquisition/catalog/overview').then((value) => { setData(value); setPerDay(String(value.backfill?.per_day ?? 0)); }).catch((requestError) => setError(requestError.message)).finally(() => setLoading(false));
  }
  useEffect(() => { refresh(); }, []);

  async function discover() {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const result = await post('/acquisition/catalog/discover', {});
      setMessage(`Discovery ${result.status}. ${result.saved || 0} league source row(s) saved. No match was invented.`);
      refresh();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }

  async function savePolicy(event) {
    event.preventDefault();
    setError('');
    try {
      const saved = await put('/acquisition/catalog/backfill-policy', { per_day: Number(perDay) });
      setMessage(`Scheduler may release ${saved.per_day} held league(s) per UTC day. Zero keeps historical collection manual.`);
      refresh();
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  const errors = data?.errors || { discovery_errors: [], quality_issues: [], backfill_errors: [] };
  return <div className="sp-acq-stack">
    <section className="sp-panel">
      <div className="sp-panel-head"><div><h3>Acquisition coverage</h3><p>Stored catalog counts and provider readiness. Missing credentials stay missing.</p></div><button className="sp-primary" type="button" disabled={busy} onClick={discover}>{busy ? 'Discovering…' : 'Discover authorized leagues'}</button></div>
      {loading && <p className="sp-muted">Loading coverage…</p>}
      <Feedback value={message} error={error} />
      {data && <div className="sp-acq-facts"><span>{data.sports} sports</span><span>{data.countries} countries</span><span>{data.leagues} leagues</span><span>{data.teams} teams</span><span>{data.fixtures_linked} fixtures linked</span><span>Last discovery {formatDate(data.last_discovery?.finished_at)}</span></div>}
      <form className="sp-catalog-filters" onSubmit={savePolicy}>
        <label>Held leagues released per day<input type="number" min="0" max="24" value={perDay} onChange={(event) => setPerDay(event.target.value)} /></label>
        <button className="sp-secondary" type="submit">Save backfill cap</button>
      </form>
    </section>
    <section className="sp-panel">
      <h3>Source health</h3>
      <div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>Provider</th><th>Adapter</th><th>Status</th><th>Enabled</th><th>Credential</th><th>Leagues</th><th>Last success</th><th>Last error</th></tr></thead><tbody>
        {(data?.providers || []).map((row) => <tr key={row.provider_key}><td>{row.display_name}</td><td>{row.adapter}</td><td>{row.status}</td><td>{row.enabled ? 'Yes' : 'No'}</td><td>{row.credential_configured ? 'Configured' : 'Not configured'}</td><td>{row.leagues}</td><td>{formatDate(row.last_success_at)}</td><td>{row.last_error || '—'}</td></tr>)}
      </tbody></table></div>
    </section>
    <section className="sp-panel">
      <h3>Error log</h3>
      {!errors.discovery_errors.length && !errors.quality_issues.length && !errors.backfill_errors.length && <p className="sp-muted">No catalog discovery, quality or backfill errors are open.</p>}
      {errors.discovery_errors.map((row) => <p key={row.id} className="sp-ops-error">{row.provider_key}: {row.error}</p>)}
      {errors.quality_issues.map((row) => <p key={row.id} className="sp-ops-error">{row.issue_type} · {row.severity} · {formatDate(row.created_at)}</p>)}
      {errors.backfill_errors.map((row) => <p key={row.id} className="sp-ops-error">{row.provider_key} {row.external_id}: {row.last_error}</p>)}
    </section>
  </div>;
}
