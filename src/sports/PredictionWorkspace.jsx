import React, {useEffect, useState} from 'react';
import {useNavigate} from 'react-router-dom';
import {BarChart3, BrainCircuit, CalendarDays, CheckCircle2, Clock3, Database, Download, Eye, Play, RefreshCw, ShieldCheck, Target, Ticket, TrendingUp, Trophy} from 'lucide-react';
import {get} from './api';
import './predictions.css';

const TABS = {
  basketball: [
    ['All Predictions', ''],
    ['Moneyline', 'moneyline'],
    ['Totals (Over/Under)', 'totals'],
    ['Spreads (Handicap)', 'spreads'],
  ],
  football: [
    ['All Predictions', ''],
    ['1X2 & Double Chance', 'result'],
    ['Goals & BTTS', 'goals'],
    ['Handicaps', 'handicap'],
  ],
};
const MARKETS = {
  basketball: [['', 'All Markets'], ['moneyline', 'Moneyline'], ['over', 'Over'], ['under', 'Under'], ['spread', 'Spread']],
  football: [['', 'All Markets'], ['home', 'Home'], ['draw', 'Draw'], ['away', 'Away'], ['double_chance', 'Double chance'], ['over', 'Over'], ['under', 'Under'], ['btts', 'BTTS'], ['handicap', 'Handicap']],
};
const STATUSES = [['', 'All Status'], ['qualified', 'Qualified'], ['awaiting_calibration', 'Awaiting calibration'], ['awaiting_odds', 'Awaiting odds'], ['rejected', 'Rejected']];
const STATUS_LABEL = {qualified: 'Qualified', awaiting_odds: 'Awaiting odds', awaiting_calibration: 'Awaiting calibration', under_analysis: 'Under analysis', rejected: 'Rejected', expired: 'Expired', suspended: 'Suspended'};
const BAR_COLORS = ['#1764ff', '#13952c', '#8138ec'];

const count = value => Number(value || 0).toLocaleString('en-US');
function stamp(value) {
  if (!value) return null;
  const parsed = new Date(String(value).includes('T') ? value : String(value).replace(' ', 'T') + 'Z');
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}
function when(value) {
  const parsed = stamp(value);
  if (!parsed) return '—';
  const dateOnly = /T00:00:00/.test(String(value));
  const date = new Intl.DateTimeFormat('en-GB', {timeZone: 'Africa/Lagos', month: 'short', day: 'numeric'}).format(parsed);
  if (dateOnly) return `${date} · time not stored`;
  const clock = new Intl.DateTimeFormat('en-GB', {timeZone: 'Africa/Lagos', hour: '2-digit', minute: '2-digit'}).format(parsed);
  return `${date}, ${clock}`;
}
function countdown(value, now) {
  const parsed = stamp(value);
  if (!parsed) return 'Unscheduled';
  const seconds = Math.round((parsed.getTime() - now) / 1000);
  if (seconds <= 0) return 'Due now';
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours >= 48) return `In ${Math.round(hours / 24)} days`;
  if (hours > 0) return `In ${hours}h ${String(minutes).padStart(2, '0')}m`;
  return `In ${String(minutes).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
}
function tipLabel(row) {
  const market = String(row.market || '');
  const side = market.endsWith('HOME') || market.startsWith('HOME') ? row.home : row.away;
  if (market === 'MONEYLINE_HOME' || market === 'HOME') return `${row.sport === 'football' && market === 'HOME' ? 'Home' : 'Moneyline'} (${row.home})`;
  if (market === 'MONEYLINE_AWAY' || market === 'AWAY') return `${row.sport === 'football' && market === 'AWAY' ? 'Away' : 'Moneyline'} (${row.away})`;
  if (market === 'DRAW') return 'Draw';
  if (market.startsWith('DOUBLE_CHANCE_')) return `Double chance ${market.slice(14)}`;
  if (market.startsWith('DNB_')) return `Draw no bet (${side})`;
  if (market === 'BTTS_YES') return 'BTTS Yes';
  if (market === 'BTTS_NO') return 'BTTS No';
  const total = market.match(/^(HOME_|AWAY_)?(OVER|UNDER)_(.+)$/);
  if (total) {
    const owner = total[1] === 'HOME_' ? row.home : total[1] === 'AWAY_' ? row.away : 'Total';
    return `${total[2] === 'OVER' ? 'Over' : 'Under'} ${total[3]} (${owner})`;
  }
  const line = market.match(/^(HOME|AWAY)_(SPREAD|HANDICAP)_(.+)$/);
  if (line) return `${line[2] === 'SPREAD' ? 'Spread' : 'Handicap'} (${line[1] === 'HOME' ? row.home : row.away} ${line[3]})`;
  return market;
}
function band(probability) {
  const value = Number(probability);
  if (value >= 0.65) return ['high', 'High'];
  if (value >= 0.55) return ['medium', 'Medium'];
  return ['low', 'Low'];
}
function edgeLabel(value) {
  if (value === null || value === undefined) return null;
  const points = Number(value) * 100;
  return {text: `${points > 0 ? '+' : ''}${points.toFixed(1)}%`, down: points < 0};
}

function Chart({series}) {
  if (!series?.length) return <div className="pw-empty-chart"><b>—</b><small>No settled pre-match picks in the last 30 days</small></div>;
  const won = series.reduce((sum, row) => sum + Number(row.won || 0), 0);
  const settled = series.reduce((sum, row) => sum + Number(row.settled || 0), 0);
  const coords = series.map((row, index) => {
    const x = series.length === 1 ? 175 : index / (series.length - 1) * 340 + 5;
    const rate = Number(row.settled) ? Number(row.won) / Number(row.settled) : 0;
    return [x, 100 - rate * 80];
  });
  return <>
    <div className="pw-chart"><svg viewBox="0 0 350 110" preserveAspectRatio="none">
      <path d="M0 100 H350 M0 60 H350 M0 20 H350" stroke="#e3eafa" fill="none"/>
      <polyline points={coords.map(point => point.join(',')).join(' ')} stroke="#753bff" strokeWidth="3" fill="none"/>
      {coords.map(([x, y], index) => <circle key={index} cx={x} cy={y} r="4" fill="white" stroke="#753bff" strokeWidth="2"/>)}
    </svg></div>
    <div className="pw-accuracy">{settled ? `${(100 * won / settled).toFixed(1)}%` : '—'}<small>Settled pre-match picks, last 30 days</small></div>
  </>;
}

export default function PredictionWorkspace({sport, run, loading}) {
  const navigate = useNavigate();
  const tabs = TABS[sport];
  const [tab, setTab] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [competition, setCompetition] = useState('');
  const [market, setMarket] = useState('');
  const [status, setStatus] = useState('');
  const [team, setTeam] = useState('');
  const [appliedTeam, setAppliedTeam] = useState('');
  const [startsAfter, setStartsAfter] = useState('');
  const [startsBefore, setStartsBefore] = useState('');
  const [summary, setSummary] = useState(null);
  const [payload, setPayload] = useState({items: [], total: 0});
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState([]);
  const [error, setError] = useState('');
  const [clock, setClock] = useState(Date.now());
  const [revision, setRevision] = useState(0);
  const group = tabs[tab][1];
  useEffect(() => { const timer = setTimeout(() => setAppliedTeam(team), 300); return () => clearTimeout(timer); }, [team]);
  useEffect(() => { const timer = setInterval(() => setClock(Date.now()), 1000); return () => clearInterval(timer); }, []);
  useEffect(() => { setPage(1); }, [sport, tab, pageSize, competition, market, status, appliedTeam, startsAfter, startsBefore]);
  useEffect(() => {
    let cancel = false;
    get(`/predictions/summary?sport=${sport}&market_group=${encodeURIComponent(group)}`).then(value => { if (!cancel) setSummary(value); }).catch(reason => { if (!cancel) setError(reason.message); });
    return () => { cancel = true; };
  }, [sport, group, revision]);
  useEffect(() => {
    let cancel = false;
    const params = new URLSearchParams({group: 'fixture', sport, page: String(page), page_size: String(pageSize)});
    if (group) params.set('market_group', group);
    if (market) params.set('market', market);
    if (competition) params.set('competition', competition);
    if (status) params.set('status', status);
    if (appliedTeam) params.set('team', appliedTeam);
    if (startsAfter) params.set('starts_after', startsAfter);
    if (startsBefore) params.set('starts_before', startsBefore);
    get('/predictions?' + params.toString()).then(value => { if (!cancel) setPayload(value); }).catch(reason => { if (!cancel) setError(reason.message); });
    return () => { cancel = true; };
  }, [sport, group, page, pageSize, market, competition, status, appliedTeam, startsAfter, startsBefore, revision]);
  async function open(row) {
    setSelected(row);
    setDetail([]);
    try { setDetail(await get(`/fixtures/${encodeURIComponent(row.fixture_id)}/analysis`)); } catch (reason) { setError(reason.message); }
  }
  async function exportCsv() {
    const params = new URLSearchParams({group: 'fixture', sport, page: '1', page_size: '100'});
    if (group) params.set('market_group', group);
    if (market) params.set('market', market);
    if (competition) params.set('competition', competition);
    if (status) params.set('status', status);
    if (appliedTeam) params.set('team', appliedTeam);
    if (startsAfter) params.set('starts_after', startsAfter);
    if (startsBefore) params.set('starts_before', startsBefore);
    const first = await get('/predictions?' + params.toString());
    const rows = [...(first.items || [])];
    const pages = Math.min(5, Math.ceil((first.total || rows.length) / 100));
    for (let next = 2; next <= pages; next += 1) {
      params.set('page', String(next));
      const more = await get('/predictions?' + params.toString());
      rows.push(...(more.items || []));
    }
    const lines = [['Fixture', 'Competition', 'Kickoff', 'Market', 'Probability', 'Odds', 'Edge', 'Probability band', 'Analysis', 'Status'], ...rows.map(row => [row.home + ' vs ' + row.away, row.competition, row.starts_at, tipLabel(row), row.probability, row.odds ?? '', row.edge ?? '', band(row.probability)[1], row.analysis_status || '', row.opportunity_status || ''])];
    const blob = new Blob([lines.map(line => line.map(cell => `"${String(cell ?? '').replaceAll('"', '""')}"`).join(',')).join('\n')], {type: 'text/csv'});
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `${sport}-predictions.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
  }
  const items = payload.items || [];
  const total = payload.total || 0;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const bands = summary?.bands || {high: 0, medium: 0, low: 0, total: 0};
  const bandTotal = bands.total || 0;
  const highShare = bandTotal ? bands.high / bandTotal * 100 : 0;
  const mediumShare = bandTotal ? bands.medium / bandTotal * 100 : 0;
  const analysed = summary?.fixtures_analysed || 0;
  const model = summary?.model;
  return <div className={`pw ${sport}`}>
    {error && <p className="pw-error">{error}</p>}
    <div className="pw-head">
      <div className="pw-title"><div className={`pw-ball ${sport}`}>{sport === 'basketball' ? '🏀' : '⚽'}</div><div>
        <div className="pw-kicker">CACSMS ONE / SPORTS AI</div>
        <h1>{sport === 'basketball' ? 'Basketball Predictions' : 'Football Predictions'}</h1>
        <p>Stored {sport} probabilities, displayed prices and value checks for upcoming matches. A blank figure has not been calculated.</p>
      </div></div>
      <div className="pw-actions">
        <button className="pw-outline" onClick={() => window.location.assign(import.meta.env.VITE_CACSMS_HUB_URL || '/hub')}>Central Hub</button>
        <button className="pw-primary" onClick={run} disabled={loading}><Play size={16}/>{loading ? 'Running…' : 'Run Analysis'}</button>
      </div>
    </div>
    <div className="pw-kpis">
      {[[CalendarDays, 'c0', 'Fixtures Analysed', count(summary?.fixtures_analysed), 'Upcoming', false], [Database, 'c1', 'Predictions Generated', count(summary?.predictions_stored), 'Stored rows', false], [Target, 'c2', 'Value Opportunities', count(summary?.qualified), summary?.qualified ? 'Qualified' : 'None qualified', Boolean(summary?.qualified)], [Trophy, 'c3', 'Prediction Accuracy', summary?.accuracy == null ? '—' : `${summary.accuracy}%`, summary?.settled ? 'Settled picks' : 'No settled results', summary?.accuracy != null], [TrendingUp, 'c4', 'Model Edge (Avg)', summary?.average_edge == null ? '—' : `+${summary.average_edge}%`, summary?.average_edge == null ? 'No qualified edge' : 'Qualified picks', summary?.average_edge != null], [Ticket, 'c5', 'Tickets Generated', count(summary?.tickets), summary?.tickets ? 'Stored' : 'None stored', Boolean(summary?.tickets)]].map(([Icon, tone, label, value, note, positive]) => <article className="pw-kpi" key={label}><div className={`pw-kicon ${tone}`}><Icon size={22}/></div><div><label>{label}</label><strong>{value}</strong><em className={positive ? 'up' : 'flat'}>{note}</em></div></article>)}
    </div>
    <div className="pw-tabs" role="tablist">{tabs.map(([label], index) => <button key={label} className={tab === index ? 'on' : ''} aria-selected={tab === index} onClick={() => setTab(index)}>{label}</button>)}</div>
    <article className="pw-panel">
      <div className="pw-head"><div><h2>Predictions</h2><p>Each row shows the stored market with the highest AI Probability in this group. A booking uses the qualified market with the highest expected edge, which can be a different selection.</p></div>
        <div className="pw-tools">
          <button className="pw-outline" onClick={() => setRevision(value => value + 1)}><RefreshCw size={15}/> Refresh</button>
          <button className="pw-outline" onClick={exportCsv}><Download size={15}/> Export</button>
          <button className="pw-primary" onClick={run} disabled={loading}><Play size={14}/> Run Analysis</button>
        </div>
      </div>
      <div className="pw-filters">
        <label>Kickoff <span className="pw-dates"><input type="date" value={startsAfter} onChange={event => setStartsAfter(event.target.value)} aria-label="From date"/><input type="date" value={startsBefore} onChange={event => setStartsBefore(event.target.value)} aria-label="To date"/></span></label>
        <label>Competition <select value={competition} onChange={event => setCompetition(event.target.value)}><option value="">All Competitions</option>{(summary?.competitions || []).map(name => <option key={name}>{name}</option>)}</select></label>
        <label>Prediction Market <select value={market} onChange={event => setMarket(event.target.value)}>{MARKETS[sport].map(([value, label]) => <option key={label} value={value}>{label}</option>)}</select></label>
        <label>Status <select value={status} onChange={event => setStatus(event.target.value)}>{STATUSES.map(([value, label]) => <option key={label} value={value}>{label}</option>)}</select></label>
        <label>Team <input value={team} placeholder="Search team" onChange={event => setTeam(event.target.value)}/></label>
        <label>Rows <select value={pageSize} onChange={event => setPageSize(Number(event.target.value))}><option value="10">10</option><option value="25">25</option><option value="50">50</option></select></label>
      </div>
      <div className="pw-scroll"><table><thead><tr>{['#', 'Fixture', 'Competition', 'Tip / Market', 'AI Probability', 'Odds', 'Edge', 'Confidence', 'Analysis', 'Status', 'Action'].map(heading => <th key={heading}>{heading}</th>)}</tr></thead><tbody>
        {items.map((row, index) => {
          const tone = band(row.probability);
          const edge = edgeLabel(row.edge);
          return <tr key={row.fixture_id}><td>{(page - 1) * pageSize + index + 1}</td><td><b>{sport === 'basketball' ? '🏀' : '⚽'} {row.home} <span className="vs">vs</span> {row.away}</b></td><td>{row.competition || '—'}</td><td>{tipLabel(row)}</td><td><b>{(Number(row.probability) * 100).toFixed(1)}%</b></td><td>{row.odds == null ? <span className="pw-blank">Awaiting odds</span> : Number(row.odds).toFixed(2)}</td><td>{edge ? <span className={edge.down ? 'pw-edge down' : 'pw-edge'}>{edge.text}</span> : <span className="pw-blank">—</span>}</td><td><span className={`pw-conf ${tone[0]}`}>{tone[1]}</span></td><td>{row.analysis_status ? <span className="pw-ok"><CheckCircle2 size={13}/>{row.analysis_status}</span> : <span className="pw-blank">Stored prediction</span>}</td><td><span className={`pw-status ${row.opportunity_status || ''}`}>{STATUS_LABEL[row.opportunity_status] || 'Not assessed'}</span></td><td><button className="pw-view" onClick={() => open(row)}><Eye size={13}/> View</button></td></tr>;
        })}
        {!items.length && <tr><td className="pw-blank" colSpan="11">No upcoming fixture has a stored prediction for this filter.</td></tr>}
      </tbody></table></div>
      <div className="pw-pager"><span>{total ? (page - 1) * pageSize + 1 : 0}–{Math.min(total, page * pageSize)} of {count(total)}</span><div><button className="pw-outline" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button><span>Page {page} of {pages}</span><button className="pw-outline" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button></div></div>
    </article>
    <div className="pw-lower">
      <article className="pw-panel"><h3><BarChart3 size={18}/> Prediction Summary</h3>
        {(summary?.families || []).map((family, index) => {
          const share = analysed ? Math.round(family.count / analysed * 100) : 0;
          return <div className="pw-bar" key={family.key}><span>{family.label}</span><div className="pw-track"><i style={{width: `${share}%`, background: BAR_COLORS[index]}}/></div><b>{share}%</b></div>;
        })}
        <p className="pw-note">Share of upcoming analysed fixtures that have a stored market in each group.</p>
      </article>
      <article className="pw-panel"><h3><Target size={18}/> Probability Distribution</h3>
        <div className="pw-donut-row"><div className="pw-donut" style={{background: bandTotal ? `conic-gradient(#109a30 0 ${highShare}%, #0985f5 ${highShare}% ${highShare + mediumShare}%, #ffc225 ${highShare + mediumShare}% 100%)` : '#e2e5e9'}}><b>{count(bandTotal)}</b><small>Predictions</small></div>
          <div className="pw-legend"><p><i style={{background: '#109a30'}}/> High (≥65%) {count(bands.high)}</p><p><i style={{background: '#0985f5'}}/> Medium (55–64%) {count(bands.medium)}</p><p><i style={{background: '#ffc225'}}/> Low (&lt;55%) {count(bands.low)}</p></div>
        </div>
        <p className="pw-note">Bands describe the stored model probability. They are not a separate confidence score.</p>
      </article>
      <article className="pw-panel"><h3><TrendingUp size={18}/> Recent Performance (Last 30 Days)</h3><Chart series={summary?.series}/></article>
    </div>
    <div className="pw-foot">
      <span><Clock3/> Next Analysis Cycle <b>{countdown(summary?.next_run_at, clock)}</b></span>
      <span><BrainCircuit/> Model lifecycle <b>{model ? `${model.name} v${model.version} · ${model.lifecycle || 'research'}` : 'None stored'}</b></span>
      <span><Database/> Data Sources <b>{summary?.sources?.length ? summary.sources.join(', ') : 'None operational'}</b></span>
      <span><Target/> Opportunities Today <b>{count(summary?.opportunities_today)}</b></span>
      <span><Ticket/> Tickets Generated <b>{count(summary?.tickets)}</b></span>
      <span><ShieldCheck/> System Status <b>{summary?.scheduler_fresh ? 'Scheduler running' : 'Heartbeat idle'}</b></span>
    </div>
    {selected && <div className="pw-overlay" onMouseDown={event => { if (event.target === event.currentTarget) setSelected(null); }}><section className="pw-modal" role="dialog" aria-modal="true">
      <button className="pw-close" onClick={() => setSelected(null)} aria-label="Close">×</button>
      <h2>{selected.home} vs {selected.away}</h2>
      <p className="pw-note">{selected.competition} · {when(selected.starts_at)} · {selected.model_name} v{selected.model_version}{selected.model_active ? '' : ' · not activated'}</p>
      <p><b>Tip:</b> {tipLabel(selected)}</p>
      <p><b>Model probability:</b> {(Number(selected.probability) * 100).toFixed(1)}%</p>
      <p><b>Lifecycle:</b> {selected.lifecycle || 'research'}. A research, validated or suspended model cannot qualify a booking.</p>
      <p><b>Market suitability:</b> {selected.suitability_rank == null ? 'Not ranked from stored features' : `${Number(selected.suitability_rank).toFixed(0)} / 100`}{selected.booking_ready ? ' · booking ready' : ' · not booking ready'}</p>
      <p><b>Displayed odds:</b> {selected.odds == null ? 'Awaiting odds' : Number(selected.odds).toFixed(2)}</p>
      <p><b>Expected edge:</b> {edgeLabel(selected.edge)?.text || 'Not stored'}</p>
      <p><b>Value status:</b> {STATUS_LABEL[selected.opportunity_status] || 'Not assessed'}{selected.opportunity_reason ? ` — ${selected.opportunity_reason}` : ''}</p>
      <p className="pw-note">Injuries, lineups and player ratings are not in the stored feeds. No wager is placed from this page.</p>
      <div className="pw-scroll"><table><thead><tr><th>Market</th><th>Probability</th><th>Model</th></tr></thead><tbody>{detail.slice(0, 16).map(row => <tr key={row.id}><td>{row.market}</td><td>{(Number(row.probability) * 100).toFixed(1)}%</td><td>{row.model_name} v{row.model_version}</td></tr>)}</tbody></table></div>
      <div className="pw-actions" style={{marginTop: 12}}><button className="pw-outline" onClick={() => navigate(`/sports/${sport}-analysis?fixture=${encodeURIComponent(selected.fixture_id)}`)}>Open match analysis</button><button className="pw-primary" onClick={() => setSelected(null)}>Close</button></div>
    </section></div>}
  </div>;
}
