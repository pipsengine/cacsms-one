import {useEffect, useState} from 'react';
import {useLiveReload} from './live';
import {get, post} from './api';

function percent(value) {
  return value == null || value === '' ? '—' : `${(Number(value) * 100).toFixed(1)}%`;
}

function decimal(value) {
  return value == null || value === '' ? '—' : Number(value).toFixed(2);
}

function when(value) {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

export default function WeeklyDrawCentre({tab = 0}) {
  if (tab === 1 || tab === 2) return <DrawResearch tab={tab} />;
  if (tab === 3) return <PredictionBoard mode="WEEKLY" />;
  if (tab === 4) return <SaturdayBoard />;
  if (tab === 5) return <TicketBuilder />;
  if (tab === 6) return <BookingCodes />;
  if (tab === 7) return <Performance />;
  return <Overview />;
}

function DrawResearch({tab}) {
  const [summary, setSummary] = useState(null);
  const [rows, setRows] = useState([]);
  const [competition, setCompetition] = useState('');
  const [quality, setQuality] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [loadedPath, setLoadedPath] = useState('');
  const path = tab === 2 ? '/draw/teams' : '/draw/leagues';

  useEffect(() => {
    let cancel = false;
    setRows([]);
    setLoadedPath('');
    const params = new URLSearchParams();
    if (competition) params.set('competition', competition);
    if (quality && tab !== 0) params.set('quality', quality);
    Promise.all([get('/draw/summary'), get(`${path}?${params.toString()}`)])
      .then(([stored, list]) => {
        if (!cancel) {
          setSummary(stored);
          setRows(list.items || []);
          setLoadedPath(path);
          setError('');
        }
      })
      .catch(requestError => { if (!cancel) setError(requestError.message); });
    return () => { cancel = true; };
  }, [path, competition, quality, tab, revision]);
  useLiveReload(() => {
    const params = new URLSearchParams();
    if (competition) params.set('competition', competition);
    if (quality && tab !== 0) params.set('quality', quality);
    return Promise.all([get('/draw/summary'), get(`${path}?${params.toString()}`)]).then(([stored, list]) => {
      setSummary(stored);
      setRows(list.items || []);
      setLoadedPath(path);
    });
  });

  async function refresh() {
    setBusy(true);
    try {
      await post('/draw/refresh', {});
      setRevision(value => value + 1);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }

  const validation = summary?.validation || {};
  const model = summary?.model || {};
  return <div className="sp-ops-stack">
    <p className="sp-muted">League and team draw rates come from stored full-time results. Upcoming fixtures are ordered by the stored calibrated draw probability and the fresh bookmaker edge. A historical draw percentage does not set the rank, and this page does not qualify a selection.</p>
    {error && <p className="sp-ops-error" role="alert">{error}</p>}
    <div className="sp-kpi-grid">
      <article className="sp-kpi"><span>Stored matches</span><strong>{summary?.matches ?? '—'}</strong></article>
      <article className="sp-kpi"><span>Leagues</span><strong>{summary?.leagues ?? '—'}</strong></article>
      <article className="sp-kpi"><span>Teams</span><strong>{summary?.teams ?? '—'}</strong></article>
      <article className="sp-kpi"><span>Ranked fixtures</span><strong>{summary?.ranked ?? '—'}</strong></article>
      <article className="sp-kpi"><span>Observed draw rate</span><strong>{percent(summary?.global_draw_rate)}</strong></article>
      <article className="sp-kpi"><span>Half-time draws</span><strong>Unavailable</strong></article>
    </div>
    <p className="sp-muted">{summary?.halftime_reason || 'No half-time score is stored.'} Validation sample {validation.sample_size ?? 0}. Brier {validation.brier_score == null ? '—' : Number(validation.brier_score).toFixed(3)}. {model.reason || 'Model status is stored after the next refresh.'} {model.dixon_coles_rho == null ? '' : `Stored Dixon–Coles rho ${Number(model.dixon_coles_rho).toFixed(2)}.`} Last refresh {when(summary?.refreshed_at)}.</p>
    <div className="sp-filter-bar">
      <label>Competition<input value={competition} onChange={event => setCompetition(event.target.value)} placeholder="League or division"/></label>
      {tab !== 0 && <label>Data quality<select value={quality} onChange={event => setQuality(event.target.value)}><option value="">All</option><option value="usable">Usable</option><option value="limited">Limited</option><option value="thin">Thin</option></select></label>}
      <button className="sp-primary" type="button" onClick={refresh} disabled={busy}>{busy ? 'Refreshing…' : 'Refresh from stored results'}</button>
    </div>
    {loadedPath === path && rows.length === 200 && <p className="sp-muted">Showing the first 200 stored rows for this filter.</p>}
    {loadedPath === path && tab === 1 && <LeagueTable rows={rows} />}
    {loadedPath === path && tab === 2 && <TeamTable rows={rows} />}
  </div>;
}

function LeagueTable({rows}) {
  return <section className="sp-panel"><div className="sp-table-scroll"><table className="sp-table"><thead><tr>{['League', 'Matches', 'Draw rate', 'Reliability', 'Uncertainty', '0–0', '1–1', '2–2', 'Years', 'Quality', 'Expected goals'].map(heading => <th key={heading}>{heading}</th>)}</tr></thead><tbody>
    {rows.map(row => <tr key={row.competition}><td>{row.competition || '—'}</td><td>{row.matches}</td><td>{percent(row.draw_rate)}</td><td>{percent(row.reliability)}</td><td>{percent(row.uncertainty)}</td><td>{percent(row.score_00)}</td><td>{percent(row.score_11)}</td><td>{percent(row.score_22)}</td><td>{row.seasons}</td><td>{row.quality}</td><td>{row.detail?.expected_home_goals == null ? '—' : `${decimal(row.detail.expected_home_goals)} / ${decimal(row.detail.expected_away_goals)}`}</td></tr>)}
    {!rows.length && <tr><td className="sp-empty" colSpan="11">No stored football result is available for this filter. Half-time frequencies are not calculated.</td></tr>}
  </tbody></table></div></section>;
}

function TeamTable({rows}) {
  return <section className="sp-panel"><div className="sp-table-scroll"><table className="sp-table"><thead><tr>{['Team', 'League', 'Matches', 'Draw rate', 'Home', 'Away', 'Last 5', 'Last 10', 'Last 20', 'Goals against', 'Reliability', 'Schedule', 'Quality'].map(heading => <th key={heading}>{heading}</th>)}</tr></thead><tbody>
    {rows.map(row => <tr key={`${row.competition}:${row.team}`}><td>{row.team}</td><td>{row.competition || '—'}</td><td>{row.matches}</td><td>{percent(row.draw_rate)}</td><td>{row.home_matches ? percent(row.home_draw_rate) : '—'}</td><td>{row.away_matches ? percent(row.away_draw_rate) : '—'}</td><td>{row.roll5_rate == null ? `${row.roll5_matches}/5` : percent(row.roll5_rate)}</td><td>{row.roll10_rate == null ? `${row.roll10_matches}/10` : percent(row.roll10_rate)}</td><td>{row.roll20_rate == null ? `${row.roll20_matches}/20` : percent(row.roll20_rate)}</td><td>{decimal(row.goals_against)}</td><td>{percent(row.reliability)}</td><td>{row.schedule_strength == null ? '—' : decimal(row.schedule_strength)}</td><td>{row.quality}</td></tr>)}
    {!rows.length && <tr><td className="sp-empty" colSpan="13">No stored team sample matches this filter.</td></tr>}
  </tbody></table></div></section>;
}

function Overview() {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => { get('/weekly-draw/overview').then(setData).catch(requestError => setError(requestError.message)); }, []);
  const schedule = data?.schedule || {};
  return <div className="sp-ops-stack">
    <p className="sp-muted">WEEKLY covers Monday through Sunday in West Africa Time. SATURDAY_ONLY covers Saturday kickoffs. A ticket is stored only when that exact number of qualified, ranked full-time draws is available.</p>
    {error && <p className="sp-ops-error" role="alert">{error}</p>}
    <div className="sp-kpi-grid">
      <article className="sp-kpi"><span>WAT phase</span><strong>{schedule.phase || '—'}</strong></article>
      <article className="sp-kpi"><span>Week</span><strong>{schedule.week_id || '—'}</strong></article>
      <article className="sp-kpi"><span>Places a wager</span><strong>No</strong></article>
    </div>
    <p className="sp-muted">{schedule.note}</p>
    <section className="sp-panel"><div className="sp-table-scroll"><table className="sp-table"><thead><tr>{['Mode', 'Picks', 'Eligible draws', 'Category', 'Status'].map(heading => <th key={heading}>{heading}</th>)}</tr></thead><tbody>
      {(data?.categories || []).map(row => <tr key={`${row.mode}-${row.pick_count}`}><td>{row.mode}</td><td>{row.pick_count}</td><td>{row.eligible}</td><td>{row.ticket_mode}</td><td>{row.status}</td></tr>)}
      {!data?.categories?.length && <tr><td className="sp-empty" colSpan="5">Category status appears after the overview loads.</td></tr>}
    </tbody></table></div></section>
  </div>;
}

function PredictionBoard({mode}) {
  const [payload, setPayload] = useState({eligible: [], excluded: []});
  const [error, setError] = useState('');
  useEffect(() => {
    get(`/weekly-draw/candidates?mode=${mode}`).then(setPayload).catch(requestError => setError(requestError.message));
  }, [mode]);
  return <div className="sp-ops-stack">
    <p className="sp-muted">{mode === 'SATURDAY_ONLY' ? 'Saturday kickoffs that are both qualified and ranked.' : 'Monday through Sunday kickoffs that are both qualified and ranked.'} Historical draw rate is not the selection rule.</p>
    {error && <p className="sp-ops-error" role="alert">{error}</p>}
    <section className="sp-panel"><div className="sp-table-scroll"><table className="sp-table"><thead><tr>{['Fixture', 'Kickoff', 'Draw probability', 'Bookmaker edge', 'Odds', 'Bookmaker'].map(heading => <th key={heading}>{heading}</th>)}</tr></thead><tbody>
      {(payload.eligible || []).map(row => <tr key={row.fixture_id}><td>{row.home} vs {row.away}<br/><small>{row.competition || '—'}</small></td><td>{when(row.starts_at)}</td><td>{percent(row.probability)}</td><td>{percent(row.edge)}</td><td>{decimal(row.decimal_odds)}</td><td>{row.bookmaker || '—'}</td></tr>)}
      {!payload.eligible?.length && <tr><td className="sp-empty" colSpan="6">No qualified ranked draw is stored in this window.</td></tr>}
    </tbody></table></div></section>
    {!!payload.excluded?.length && <p className="sp-muted">{payload.excluded.length} stored draw decisions are outside this window or lack a ranked price.</p>}
  </div>;
}

function SaturdayBoard() {
  const [watch, setWatch] = useState([]);
  useEffect(() => { get('/weekly-draw/watchlist').then(value => setWatch(value.items || [])).catch(() => setWatch([])); }, []);
  return <div className="sp-ops-stack">
    <PredictionBoard mode="SATURDAY_ONLY" />
    <section className="sp-panel"><h3>Friday watchlist</h3><p className="sp-muted">Prepared on Friday from qualified Saturday draws. An empty watchlist stays empty.</p><div className="sp-table-scroll"><table className="sp-table"><thead><tr>{['Fixture', 'Kickoff', 'State'].map(heading => <th key={heading}>{heading}</th>)}</tr></thead><tbody>
      {watch.map(row => <tr key={row.fixture_id}><td>{row.home} vs {row.away}</td><td>{when(row.starts_at)}</td><td>{row.state}</td></tr>)}
      {!watch.length && <tr><td className="sp-empty" colSpan="3">No Saturday watchlist is stored for this week.</td></tr>}
    </tbody></table></div></section>
  </div>;
}

function TicketBuilder() {
  const [mode, setMode] = useState('WEEKLY');
  const [overview, setOverview] = useState(null);
  const [tickets, setTickets] = useState([]);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  function load() {
    return Promise.all([get('/weekly-draw/overview'), get(`/weekly-draw/tickets?mode=${mode}`)])
      .then(([summary, list]) => { setOverview(summary); setTickets(list.items || []); });
  }
  useEffect(() => { load().catch(requestError => setError(requestError.message)); }, [mode]);
  async function build() {
    setBusy(true);
    setError('');
    try {
      setResult(await post('/weekly-draw/build', {mode}));
      await load();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }
  const categories = (overview?.categories || []).filter(row => row.mode === mode);
  return <div className="sp-ops-stack">
    <p className="sp-muted">Each category holds exactly 2, 5, 8 or 10 full-time draws. Eight- and ten-pick tickets stay in simulation. A short list returns INSUFFICIENT_QUALIFIED_DRAWS.</p>
    {error && <p className="sp-ops-error" role="alert">{error}</p>}
    <div className="sp-filter-bar">
      <label>Mode<select value={mode} onChange={event => setMode(event.target.value)}><option value="WEEKLY">WEEKLY</option><option value="SATURDAY_ONLY">SATURDAY_ONLY</option></select></label>
      <button className="sp-primary" type="button" onClick={build} disabled={busy}>{busy ? 'Building…' : 'Build from qualified draws'}</button>
    </div>
    <div className="sp-kpi-grid">
      {categories.map(row => <article className="sp-kpi" key={row.pick_count}><span>{row.pick_count} picks</span><strong>{row.status}</strong><small>{row.ticket_mode}</small></article>)}
    </div>
    {result && <section className="sp-panel">{result.categories?.map(row => <p key={row.pick_count}>{row.pick_count} picks: {row.status}{row.reason ? ` — ${row.reason}` : ''}</p>)}</section>}
    <section className="sp-panel"><div className="sp-table-scroll"><table className="sp-table"><thead><tr>{['Ticket', 'Picks', 'Status', 'Combined odds', 'Joint probability', 'Expected value', 'Selections'].map(heading => <th key={heading}>{heading}</th>)}</tr></thead><tbody>
      {tickets.map(row => <tr key={row.id}><td>{row.parameters?.mode}</td><td>{row.parameters?.pick_count}</td><td>{row.status}{row.parameters?.tier_mode === 'simulation' ? ' · simulation' : ''}</td><td>{decimal(row.combined_odds)}</td><td>{percent(row.joint_probability)}</td><td>{percent(row.estimated_edge)}</td><td>{(row.legs || []).map(leg => `${leg.home} vs ${leg.away}`).join('; ') || '—'}</td></tr>)}
      {!tickets.length && <tr><td className="sp-empty" colSpan="7">No weekly draw ticket is stored for this mode.</td></tr>}
    </tbody></table></div></section>
  </div>;
}

function BookingCodes() {
  const [items, setItems] = useState([]);
  const [error, setError] = useState('');
  useEffect(() => { get('/weekly-draw/codes').then(value => setItems(value.items || [])).catch(requestError => setError(requestError.message)); }, []);
  return <div className="sp-ops-stack">
    <p className="sp-muted">A code appears here only after the SportyBet page displays it during a publication window. Eight- and ten-pick simulation tickets are not sent to the betslip.</p>
    {error && <p className="sp-ops-error" role="alert">{error}</p>}
    <section className="sp-panel"><div className="sp-table-scroll"><table className="sp-table"><thead><tr>{['Code', 'Status', 'Combined odds', 'Generated', 'Expires', 'Evidence'].map(heading => <th key={heading}>{heading}</th>)}</tr></thead><tbody>
      {items.map(row => <tr key={row.code}><td>{row.code}</td><td>{row.status}</td><td>{decimal(row.combined_odds)}</td><td>{when(row.verified_at || row.created_at)}</td><td>{when(row.expires_at)}</td><td>{row.evidence ? 'Screenshot stored' : '—'}</td></tr>)}
      {!items.length && <tr><td className="sp-empty" colSpan="6">No verified weekly draw booking code is stored.</td></tr>}
    </tbody></table></div></section>
  </div>;
}

function Performance() {
  const [items, setItems] = useState([]);
  const [error, setError] = useState('');
  useEffect(() => { get('/weekly-draw/performance').then(value => setItems(value.items || [])).catch(requestError => setError(requestError.message)); }, []);
  return <div className="sp-ops-stack">
    <p className="sp-muted">Draw accuracy counts settled selections. Ticket win rate counts settled tickets in which every selection won. The two figures stay separate for each pick size and mode.</p>
    {error && <p className="sp-ops-error" role="alert">{error}</p>}
    <section className="sp-panel"><div className="sp-table-scroll"><table className="sp-table"><thead><tr>{['Mode', 'Picks', 'Settled selections', 'Draw accuracy', 'Settled tickets', 'Ticket win rate'].map(heading => <th key={heading}>{heading}</th>)}</tr></thead><tbody>
      {items.map(row => <tr key={`${row.mode}-${row.pick_count}`}><td>{row.mode}</td><td>{row.pick_count}</td><td>{row.leg_sample}</td><td>{percent(row.draw_accuracy)}</td><td>{row.ticket_sample}</td><td>{percent(row.ticket_win_rate)}</td></tr>)}
      {!items.length && <tr><td className="sp-empty" colSpan="6">No settled weekly draw sample is stored.</td></tr>}
    </tbody></table></div></section>
  </div>;
}
