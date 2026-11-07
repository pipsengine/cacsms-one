import React, { useEffect, useMemo, useState } from 'react';
import { useLiveReload } from './live';
import { AlertCircle, BrainCircuit, RefreshCw, Search, ShieldCheck } from 'lucide-react';
import { get, post } from './api';

const tabs = [
  'Match Overview',
  'Head-to-Head',
  'Team Form',
  'Standings',
  'Match Statistics',
  'Odds Comparison',
  'AI Analysis',
];

const formatDate = (value) => value ? new Date(value).toLocaleString() : 'Not available';

function SourceFreshness({ rows = [], fallback }) {
  const sources = [...new Set(rows.map((row) => row.source || row.model_name).filter(Boolean))];
  const latest = rows.map((row) => row.observed_at || row.captured_at || row.analyzed_at)
    .filter(Boolean).sort().at(-1);
  return <div className="sp-acq-freshness"><span>Source: {sources.join(', ') || fallback || 'Not recorded'}</span><span>Freshness: {formatDate(latest)}</span></div>;
}

function Table({ rows, columns, empty }) {
  if (!rows?.length) return <div className="sp-emptybox">{empty}</div>;
  return <div className="sp-table-scroll"><table className="sp-table"><thead><tr>{columns.map(([key, title]) => <th key={key}>{title}</th>)}</tr></thead><tbody>
    {rows.map((row, index) => <tr key={row.id || index}>{columns.map(([key]) => <td key={key}>{typeof row[key] === 'object' ? JSON.stringify(row[key]) : String(row[key] ?? '—')}</td>)}</tr>)}
  </tbody></table></div>;
}

function BrowserResearch({ match, onComplete }) {
  const [urls, setUrls] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const result = await post('/acquisition/browser/research', {
        query: `${match.fixture.home} vs ${match.fixture.away} match research`,
        urls: urls.split(/\r?\n/).map((value) => value.trim()).filter(Boolean),
        fixture_id: match.fixture.id,
      });
      setMessage(`Browser session ${result.session_id} completed. Evidence is now stored with this match.`);
      await onComplete();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }

  return <form className="sp-acq-form sp-panel" onSubmit={submit}>
    <h3>Collect permitted web research</h3>
    <p className="sp-muted">Only administrator-approved, public HTTPS URLs are visited. The operator does not authenticate, bypass CAPTCHAs or defeat anti-bot controls.</p>
    <label>Approved page URLs (one per line)<textarea required rows="3" value={urls} onChange={(event) => setUrls(event.target.value)} placeholder="https://approved.example/match/..." /></label>
    <button className="sp-primary" disabled={busy}><Search size={15} />{busy ? 'Researching…' : 'Collect Match Evidence'}</button>
    {message && <p role="status">{message}</p>}
    {error && <p className="sp-ops-error">{error}</p>}
  </form>;
}

export default function MatchResearch() {
  const [matches, setMatches] = useState([]);
  const [selectedId, setSelectedId] = useState('');
  const [match, setMatch] = useState(null);
  const [filter, setFilter] = useState('');
  const [sport, setSport] = useState('');
  const [tab, setTab] = useState(0);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function loadMatches() {
    const records = await get(`/research/matches?limit=500${sport ? `&sport=${sport}` : ''}${filter ? `&q=${encodeURIComponent(filter)}` : ''}`);
    setMatches(records);
    if (!selectedId && records.length) setSelectedId(records[0].id);
    if (selectedId && !records.some((record) => record.id === selectedId)) {
      setSelectedId(records[0]?.id || '');
    }
  }

  async function loadMatch(fixtureId = selectedId) {
    if (!fixtureId) {
      setMatch(null);
      return;
    }
    setLoading(true);
    try {
      setMatch(await get(`/research/matches/${encodeURIComponent(fixtureId)}`));
      setError('');
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadMatches().catch((requestError) => setError(requestError.message)); }, [sport]);
  useEffect(() => {
    if (selectedId) loadMatch(selectedId);
    else setMatch(null);
  }, [selectedId]);
  useLiveReload(() => refreshAll());

  const fixture = match?.match?.fixture;
  const formRows = useMemo(() => Object.entries(match?.team_form || {}).flatMap(([team, games]) =>
    games.map((game) => ({ ...game, team }))), [match]);

  async function refreshAll() {
    try {
      await loadMatches();
      await loadMatch();
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  return <div className="sp-acq-stack">
    <section className="sp-panel">
      <div className="sp-panel-head">
        <div><h3>Match Research Centre</h3><p>Match-linked research, source lineage and dataset freshness.</p></div>
        <button className="sp-secondary" onClick={refreshAll} disabled={loading}><RefreshCw size={15} />Refresh</button>
      </div>
      <div className="sp-acq-match-select">
        <label>Sport<select value={sport} onChange={(event) => setSport(event.target.value)}><option value="">All sports</option><option value="football">Football</option><option value="basketball">Basketball</option></select></label>
        <label>Find a match<input value={filter} onChange={(event) => setFilter(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); loadMatches().catch((requestError) => setError(requestError.message)); } }} placeholder="Team or competition" /></label>
        <label>Stored fixture<select value={selectedId} onChange={(event) => setSelectedId(event.target.value)}><option value="">Select a match</option>{matches.map((item) => <option key={item.id} value={item.id}>{item.home} vs {item.away} · {item.sport} · {new Date(item.starts_at).toLocaleDateString()}</option>)}</select></label>
      </div>
      {error && <p className="sp-ops-error"><AlertCircle size={15} />{error}</p>}
    </section>
    <div className="sp-tabs sp-acq-match-tabs" role="tablist">
      {tabs.map((name, index) => <button key={name} role="tab" aria-selected={tab === index} className={tab === index ? 'selected' : ''} onClick={() => setTab(index)}>{name}</button>)}
    </div>
    {match && fixture ? <>
      <section className="sp-panel sp-acq-match-head">
        <div><span className="sp-overline">{fixture.competition} · {fixture.sport}</span><h2>{fixture.home} <span>vs</span> {fixture.away}</h2><p>{formatDate(fixture.starts_at)} · {fixture.status} · Fixture source: {fixture.source}</p></div>
        <div className="sp-acq-freshness"><ShieldCheck size={16} /><span>Match data observed {formatDate(match.match.observed_at)}</span></div>
      </section>
      <section className="sp-panel">
        {tab === 0 && <>
          <h3>Match Overview</h3>
          <SourceFreshness rows={[{ source: match.match.source, observed_at: match.match.observed_at }]} />
          <div className="sp-acq-match-grid">
            <div><small>Kickoff</small><strong>{formatDate(fixture.starts_at)}</strong></div>
            <div><small>Competition</small><strong>{fixture.competition}</strong></div>
            <div><small>Status</small><strong>{fixture.status}</strong></div>
            <div><small>Final score</small><strong>{match.match.result ? `${match.match.result.home_score} – ${match.match.result.away_score}` : 'Not recorded'}</strong></div>
          </div>
          <BrowserResearch match={match.match} onComplete={() => loadMatch()} />
        </>}
        {tab === 1 && <><h3>Head-to-Head</h3><SourceFreshness rows={match.head_to_head} /><Table rows={match.head_to_head} columns={[['starts_at', 'Date'], ['home', 'Home'], ['home_score', 'Home score'], ['away_score', 'Away score'], ['away', 'Away'], ['source', 'Source'], ['observed_at', 'Observed']]} empty="No stored head-to-head results are available for this team pair." /></>}
        {tab === 2 && <><h3>Team Form</h3><SourceFreshness rows={formRows} /><Table rows={formRows} columns={[['team', 'Team'], ['starts_at', 'Date'], ['home', 'Home'], ['home_score', 'Home score'], ['away_score', 'Away score'], ['away', 'Away'], ['source', 'Source'], ['observed_at', 'Observed']]} empty="No stored final scores are available to calculate team form." /></>}
        {tab === 3 && <><h3>Standings</h3><SourceFreshness rows={match.standings} /><Table rows={match.standings} columns={[['position', 'Pos'], ['team', 'Team'], ['played', 'P'], ['won', 'W'], ['drawn', 'D'], ['lost', 'L'], ['points', 'Pts'], ['goal_difference', 'GD'], ['source', 'Source'], ['observed_at', 'Observed']]} empty="No standings snapshots have been collected for this competition." /></>}
        {tab === 4 && <><h3>Match Statistics</h3><SourceFreshness rows={match.statistics} fallback={fixture.source} /><Table rows={match.statistics} columns={[['metric', 'Statistic'], ['value', 'Value'], ['unit', 'Unit'], ['source', 'Source'], ['observed_at', 'Observed']]} empty="No match statistics are stored for this fixture." /></>}
        {tab === 5 && <><h3>Odds Comparison</h3><SourceFreshness rows={match.odds} fallback={fixture.source} /><Table rows={match.odds} columns={[['bookmaker', 'Bookmaker'], ['market', 'Market'], ['outcome', 'Outcome'], ['line', 'Line'], ['decimal_odds', 'Decimal odds'], ['source', 'Source'], ['observed_at', 'Observed']]} empty="No bookmaker odds snapshots are stored for this fixture." /></>}
        {tab === 6 && <><h3><BrainCircuit size={17} /> AI Analysis</h3><p className="sp-muted">Stored statistical-baseline predictions only; they are not trained or calibrated recommendations.</p><SourceFreshness rows={match.analysis} fallback="Sports AI baseline" /><Table rows={match.analysis} columns={[['market', 'Market'], ['odds', 'Decimal odds'], ['probability', 'Estimated probability'], ['edge', 'Estimated edge'], ['model_name', 'Model'], ['analyzed_at', 'Analyzed']]} empty="No analysis run has produced predictions for this fixture." /><h4>Research evidence</h4><SourceFreshness rows={match.evidence} />{match.evidence?.map((evidence) => <article className="sp-acq-history" key={`${evidence.field}-${evidence.source_url}`}><div><strong>{evidence.field}</strong><p>{formatDate(evidence.captured_at)}</p><pre>{JSON.stringify(evidence.value, null, 2)}</pre><a href={evidence.source_url} target="_blank" rel="noreferrer">{evidence.source_url}</a></div></article>)}</>}
      </section>
    </> : !error && <section className="sp-panel"><h3>{tabs[tab]}</h3><p className="sp-muted">{matches.length ? 'Select a stored match to load this dataset.' : 'No fixtures are stored yet. Enable a permitted source and run a collection before researching matches.'}</p></section>}
  </div>;
}
