import { useEffect, useMemo, useState } from 'react';
import { Activity, AlertCircle, ArrowRight, BarChart3, Bot, CalendarDays, Check, CheckCircle2, ClipboardList, Clock3, Copy, Eye, History, Layers, RefreshCw, Settings2, Ticket, TrendingUp, XCircle, Zap } from 'lucide-react';
import { get } from './api';
import './ticket-builder.css';

const BANDS = [
  ['1.50 – 1.99', 1.5, 2],
  ['2.00 – 2.99', 2, 3],
  ['3.00 – 4.99', 3, 5],
  ['5.00 – 9.99', 5, 10],
  ['10.00 – 19.99', 10, 20],
  ['20.00+', 20, Infinity],
];

function lagosStamp(value, withWeekday = false) {
  const date = value ? new Date(value) : new Date();
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat('en-GB', {
    weekday: withWeekday ? 'short' : undefined,
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: withWeekday ? undefined : '2-digit',
    hour12: false,
    timeZone: 'Africa/Lagos',
  }).format(date);
}

function lagosDay(value) {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Africa/Lagos', year: 'numeric', month: '2-digit', day: '2-digit' }).format(value ? new Date(value) : new Date());
}

function countdown(value, clock) {
  if (!value) return 'Not scheduled';
  const target = new Date(value).getTime();
  if (Number.isNaN(target)) return 'Not scheduled';
  const delta = target - clock;
  if (delta <= 0) return 'Due now';
  const minutes = Math.floor(delta / 60000);
  const seconds = Math.floor((delta % 60000) / 1000);
  return `In ${minutes} min ${String(seconds).padStart(2, '0')} sec`;
}

function oddsText(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  return number.toLocaleString('en-US', { minimumFractionDigits: 3, maximumFractionDigits: 3 });
}

function percent(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  return Math.round(number * 1000) / 10;
}

function bandName(odds) {
  const number = Number(odds);
  const band = BANDS.find((item) => number >= item[1] && number < item[2]);
  return band ? band[0] : '—';
}

function marketText(leg) {
  const raw = leg.selection || leg.market || '';
  return String(raw).replaceAll('_', ' ');
}

function sportLabelFor(ticket) {
  const names = { football: 'Football', basketball: 'Basketball', ice_hockey: 'Ice hockey', frauenfussball: 'Frauenfußball' };
  const sports = [...new Set((ticket.legs || []).map((leg) => leg.sport).filter(Boolean))];
  if (sports.length === 1) return names[sports[0]] || 'Mixed';
  if (ticket.sport_mode === 'football') return 'Football';
  if (ticket.sport_mode === 'basketball') return 'Basketball';
  return 'Mixed';
}
function statusFor(ticket, code) {
  if (ticket.parameters?.coverage === 'high_probability' && ticket.status === 'unverified_probability') return 'High probability';
  if (code) {
    if (code.status === 'verified' && code.verification_method === 'displayed') return 'Verified';
    if (code.status === 'expired') return 'Expired';
    if (code.status === 'unverified') return 'Unverified';
    return String(code.status || 'Stored').replaceAll('_', ' ');
  }
  const labels = {
    qualified: 'Awaiting code',
    review_required: 'Risk Review',
    awaiting_odds: 'Awaiting odds',
    expired: 'Expired',
    void: 'Void',
    settled: 'Settled',
    unverified_probability: 'Unverified',
  };
  return labels[ticket.status] || String(ticket.status || 'Stored').replaceAll('_', ' ');
}

function statusClass(status) {
  return String(status).toLowerCase().replaceAll(' ', '-');
}

function latestTickets(rows) {
  const chosen = new Map();
  for (const ticket of rows || []) {
    const key = ticket.family_id || ticket.id;
    const current = chosen.get(key);
    if (!current || Number(ticket.version || 0) >= Number(current.version || 0)) chosen.set(key, ticket);
  }
  return [...chosen.values()].sort((left, right) => String(right.created_at).localeCompare(String(left.created_at)));
}

function withinRange(createdAt, range) {
  if (range === 'All time') return true;
  const created = new Date(createdAt).getTime();
  if (Number.isNaN(created)) return false;
  const day = lagosDay(createdAt);
  if (range === 'Today') return day === lagosDay();
  const days = range === 'Last 7 days' ? 7 : 30;
  return Date.now() - created <= days * 86400000;
}

function legKey(leg) {
  const line = leg.quote_line ?? leg.line ?? '';
  const fixture = leg.fixture_id || `${leg.home}|${leg.away}|${leg.starts_at}`;
  return `${fixture}|${String(leg.market || '').toUpperCase()}|${line}`;
}

function combinedPartCount(count) {
  if (count <= 25) return 1;
  if (Math.ceil(count / 2) <= 30) return 2;
  return 3;
}

function buildCombined(members) {
  const rankedMembers = [...members].sort((left, right) => (right.probability ?? -1) - (left.probability ?? -1));
  const chosen = new Map();
  for (const ticket of rankedMembers) {
    for (const leg of ticket.legs || []) {
      if (Number(leg.decimal_odds) < 1.3) continue;
      const key = legKey(leg);
      if (!chosen.has(key)) chosen.set(key, leg);
    }
  }
  const legs = [...chosen.values()].sort((left, right) => (Number(right.probability) || 0) - (Number(left.probability) || 0));
  if (!legs.length) return [];
  const parts = combinedPartCount(legs.length);
  const size = Math.ceil(legs.length / parts);
  const newest = rankedMembers.reduce((latest, ticket) => String(ticket.created_at) > String(latest) ? ticket.created_at : latest, rankedMembers[0].created_at);
  return Array.from({ length: parts }, (_, index) => {
    const slice = legs.slice(index * size, (index + 1) * size);
    const prices = slice.map((leg) => Number(leg.decimal_odds));
    const odds = prices.every((price) => Number.isFinite(price) && price > 0) ? prices.reduce((product, price) => product * price, 1) : null;
    const probabilities = slice.map((leg) => Number(leg.probability));
    const joint = probabilities.every((value) => Number.isFinite(value) && value > 0 && value <= 1) ? probabilities.reduce((product, value) => product * value, 1) : null;
    const sports = new Set(slice.map((leg) => leg.sport).filter(Boolean));
    const sportMode = sports.size === 1 ? [...sports][0] : 'mixed';
    const title = parts === 1 ? 'Combined' : `Combined ${index + 1}`;
    return {
      id: `combined-${index + 1}`,
      combined: true,
      combinedPart: index + 1,
      combinedParts: parts,
      memberCount: rankedMembers.length,
      label: 'Combined',
      shownCode: title,
      code: null,
      probability: joint == null ? null : percent(joint),
      band: bandName(odds),
      combined_odds: odds,
      leg_count: slice.length,
      legs: slice,
      sport_mode: sportMode,
      sportLabel: sportMode === 'football' ? 'Football' : sportMode === 'basketball' ? 'Basketball' : 'Mixed',
      created_at: newest,
      status: 'combined',
      explanation: `${title} gathers ${rankedMembers.length} stored tickets at 75% or above. ${slice.length} selections are listed, with each fixture and market kept once. The percentage is the joint estimate of this group. This ticket is for reading those selections together.`,
    };
  }).filter((ticket) => ticket.leg_count);
}

function Badge({ status }) {
  const mark = status === 'Verified' ? '✓ ' : status === 'Expired' || status === 'Void' ? '✕ ' : status === 'Awaiting odds' ? '↻ ' : '';
  return <span className={`tb-status ${statusClass(status)}`}>{mark}{status}</span>;
}

function Prob({ value }) {
  if (value == null) return <span className="tb-muted">—</span>;
  const color = value >= 65 ? '#17bd3c' : value >= 45 ? '#f0a30a' : '#ed535d';
  return <div className="tb-prob"><div className="tb-prob-track"><div style={{ width: `${Math.max(0, Math.min(100, value))}%`, background: color }} /></div><strong style={{ color }}>{value}%</strong></div>;
}

function Panel({ title, icon: Icon, action, children }) {
  return <section className="tb-panel"><header className="tb-panel-head"><div><Icon size={18} /><h3>{title}</h3></div>{action}</header>{children}</section>;
}

export default function TicketBuilder() {
  const [tab, setTab] = useState('Generated Tickets');
  const [sport, setSport] = useState('All Sports');
  const [tier, setTier] = useState('All Tiers');
  const [status, setStatus] = useState('All Status');
  const [automation, setAutomation] = useState('All');
  const [range, setRange] = useState('All time');
  const [query, setQuery] = useState('');
  const [selectedId, setSelectedId] = useState('');
  const [modal, setModal] = useState(false);
  const [notice, setNotice] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [records, setRecords] = useState([]);
  const [codes, setCodes] = useState([]);
  const [overview, setOverview] = useState(null);
  const [command, setCommand] = useState(null);
  const [now, setNow] = useState(Date.now());
  const [page, setPage] = useState(1);

  async function load(options) {
    const quiet = options?.quiet;
    if (!quiet) setLoading(true);
    const [ticketResult, codeResult, overviewResult, commandResult] = await Promise.allSettled([
      get('/accumulator/tickets'),
      get('/accumulator/booking-codes'),
      get('/autonomous/overview'),
      get('/accumulator/command'),
    ]);
    if (ticketResult.status === 'fulfilled') {
      let rows = latestTickets(ticketResult.value);
      const storedCodes = codeResult.status === 'fulfilled' ? codeResult.value || [] : [];
      const present = new Set(rows.map((ticket) => ticket.id));
      const missing = [...new Set(storedCodes.map((code) => code.accumulator_ticket_id).filter((id) => id && !present.has(id)))];
      if (missing.length) {
        const extras = await Promise.all(missing.map((id) => get(`/accumulator/tickets/${encodeURIComponent(id)}`).catch(() => null)));
        rows = latestTickets([...rows, ...extras.filter(Boolean)]);
      }
      setRecords(rows);
    }
    if (codeResult.status === 'fulfilled') setCodes(codeResult.value || []);
    if (overviewResult.status === 'fulfilled') setOverview(overviewResult.value);
    if (commandResult.status === 'fulfilled') setCommand(commandResult.value);
    setError(ticketResult.status === 'fulfilled' ? '' : ticketResult.reason?.message || 'Stored tickets could not be loaded');
    setLoading(false);
  }

  useEffect(() => { load(); const timer = setInterval(() => load({quiet: true}), 60000); return () => clearInterval(timer); }, []);
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);

  const codeByTicket = useMemo(() => {
    const map = new Map();
    for (const code of codes) {
      const key = code.accumulator_ticket_id;
      if (key && !map.has(key)) map.set(key, code);
    }
    return map;
  }, [codes]);

  const rows = useMemo(() => records.map((ticket) => {
    const code = codeByTicket.get(ticket.id) || null;
    const basis = ticket.probability_basis === 'independent' ? percent(ticket.joint_probability) : null;
    return {
      ...ticket,
      code,
      label: statusFor(ticket, code),
      shownCode: code?.code || '',
      probability: basis,
      band: bandName(ticket.combined_odds),
      sportLabel: sportLabelFor(ticket),
    };
  }).filter((row) => (row.legs || []).every((leg) => Number(leg.decimal_odds) >= 1.3)), [records, codeByTicket]);

  const scopeRows = useMemo(() => rows.filter((row) => (sport === 'All Sports' || row.sportLabel === sport)
    && (tier === 'All Tiers' || row.band === tier)
    && (automation === 'All' || (automation === 'Has code' ? row.shownCode : !row.shownCode))
    && withinRange(row.created_at, range)), [rows, sport, tier, automation, range]);

  const combinedTickets = useMemo(() => buildCombined(scopeRows.filter((row) => row.probability != null && row.probability >= 75)), [scopeRows]);
  const memberIds = useMemo(() => new Set(scopeRows.filter((row) => row.probability != null && row.probability >= 75).map((row) => row.id)), [scopeRows]);
  const queryText = query.trim().toLowerCase();
  const visibleCombined = (status === 'All Status' || status === 'Combined')
    ? combinedTickets.filter((ticket) => {
      if (!queryText) return true;
      const text = `${ticket.shownCode} ${(ticket.legs || []).map((leg) => `${leg.home} ${leg.away} ${leg.market}`).join(' ')}`.toLowerCase();
      return text.includes(queryText);
    })
    : [];

  const shown = useMemo(() => scopeRows.filter((row) => {
    const text = `${row.shownCode} ${row.id} ${(row.legs || []).map((leg) => `${leg.home} ${leg.away} ${leg.market}`).join(' ')}`.toLowerCase();
    const textOk = !queryText || text.includes(queryText);
    if (status === 'Combined') return memberIds.has(row.id) && textOk;
    return textOk && (status === 'All Status' || row.label === status);
  }), [scopeRows, status, memberIds, queryText]);

  const ranked = useMemo(() => [...shown].sort((left, right) => (right.probability ?? -1) - (left.probability ?? -1)), [shown]);
  const pageSize = 10;
  const pageCount = Math.max(1, Math.ceil(ranked.length / pageSize));
  const currentPage = Math.min(page, pageCount);
  const pageRows = ranked.slice((currentPage - 1) * pageSize, currentPage * pageSize);
  const tableRows = currentPage === 1 ? [...visibleCombined, ...pageRows] : pageRows;
  useEffect(() => { setPage(1); }, [sport, tier, status, automation, range, query]);

  const selected = visibleCombined.find((row) => row.id === selectedId) || rows.find((row) => row.id === selectedId) || visibleCombined[0] || ranked[0] || null;
  const summary = overview?.summary || {};
  const todayCount = rows.filter((row) => lagosDay(row.created_at) === lagosDay()).length;
  const verified = codes.filter((code) => code.status === 'verified' && code.verification_method === 'displayed').length;
  const expired = codes.filter((code) => code.status === 'expired').length;
  const awaiting = rows.filter((row) => row.label === 'Awaiting code').length;
  const tierCounts = BANDS.map(([name]) => [name, rows.filter((row) => row.band === name).length]);
  const tierMax = Math.max(1, ...tierCounts.map((item) => item[1]));
  const active = overview?.status === 'running' && overview?.scheduler_fresh;
  const run = command?.latest_run;
  const buildSeconds = run?.started_at && run?.finished_at ? Math.max(0, Math.round((new Date(run.finished_at) - new Date(run.started_at)) / 1000)) : null;

  async function copy(value, label) {
    if (!value) { setNotice('No booking code is stored for this ticket'); return; }
    try { await navigator.clipboard.writeText(value); setNotice(`${label} copied`); }
    catch { setNotice('Clipboard is unavailable'); }
  }

  const stats = [
    ['Tickets Built', summary.tickets ?? rows.length, todayCount ? `+${todayCount} today in this list` : 'Stored tickets', Layers, 'blue'],
    ['Codes Stored', codes.length, 'SportyBet codes on file', Ticket, 'green'],
    ['Verified', verified, 'Displayed and still valid', CheckCircle2, 'green'],
    ['Awaiting Code', awaiting, 'In this list', Clock3, 'blue'],
    ['Failed Jobs', summary.failures ?? 0, 'Last stored jobs', XCircle, 'red'],
    ['Expired', expired, 'Stored codes past their window', CalendarDays, 'pink'],
    ['Qualified', summary.qualified_decisions ?? summary.opportunities ?? 0, 'Current qualified decisions', Layers, 'blue'],
    ['Job Success', summary.health == null ? '—' : `${summary.health}%`, 'Completed jobs, last 24 hours', Zap, 'orange'],
  ];
  const stages = [
    ['Discover', summary.fixtures ?? '—', 'fixtures'],
    ['Analyze', summary.analysed ?? '—', 'predictions, 24h'],
    ['Qualify', summary.opportunities ?? '—', 'qualified'],
    ['Build', summary.tickets ?? '—', 'tickets'],
    ['Generate Code', codes.length, 'stored'],
    ['Displayed Code', codes.filter((code) => code.verification_method === 'displayed').length, 'displayed'],
    ['Verify', verified, 'still valid'],
  ];

  return <div className="tb">
    <div className="tb-top">
      <div>
        <h1>Ticket Builder <span>(Autonomous)</span></h1>
        <p>Discover • Analyze • Qualify • Build Tickets • Generate Codes • Register when SportyBet displays a code • Track • Settle</p>
      </div>
      <div className="tb-top-right">
        <span className="tb-date"><CalendarDays size={16} />{lagosStamp(now, true)} WAT</span>
        <span className={`tb-live ${active ? '' : 'off'}`}>{active ? 'Autonomous: ACTIVE' : overview?.status === 'paused' ? 'Autonomous: PAUSED' : 'Scheduler heartbeat stale'}</span>
      </div>
    </div>
    {error && <div className="tb-notice"><AlertCircle size={15} />{error}</div>}
    <div className="tb-stats">{stats.map(([label, value, sub, Icon, color]) => <div className={`tb-stat ${color}`} key={label}><Icon size={26} /><div><span>{label}</span><strong>{typeof value === 'number' ? value.toLocaleString('en-US') : value}</strong><small>{sub}</small></div></div>)}</div>
    <section className="tb-engine">
      <div className="tb-engine-title"><Bot size={32} /><div><h2>Autonomous Ticket Engine</h2><p>Tickets are built from stored qualified selections. A booking code is kept only after SportyBet displays it.</p></div></div>
      <div className="tb-stages">{stages.map(([name, count, unit], index) => <div className="tb-stage-wrap" key={name}><div className="tb-stage"><CheckCircle2 size={15} /><b>{name}</b><small>{Number.isFinite(Number(count)) ? `${Number(count).toLocaleString('en-US')} ${unit}` : '—'}</small></div>{index < stages.length - 1 && <ArrowRight className="tb-stage-arrow" size={15} />}</div>)}</div>
    </section>
    <div className="tb-tabs" role="tablist">
      {[['Generated Tickets', Layers], ['Ticket Details', ClipboardList], ['Code History', History], ['Automation Log', Activity], ['Auto Settings', Settings2]].map(([name, Icon]) => <button key={name} role="tab" aria-selected={tab === name} className={tab === name ? 'active' : ''} onClick={() => setTab(name)}><Icon size={16} />{name}{name === 'Generated Tickets' && <em>{shown.length}</em>}</button>)}
    </div>
    <div className="tb-filters">
      <label>Date Range<select value={range} onChange={(event) => setRange(event.target.value)}>{['Today', 'Last 7 days', 'Last 30 days', 'All time'].map((item) => <option key={item}>{item}</option>)}</select></label>
      <label>Sport<select value={sport} onChange={(event) => setSport(event.target.value)}>{['All Sports', 'Football', 'Basketball', 'Ice hockey', 'Mixed'].map((item) => <option key={item}>{item}</option>)}</select></label>
      <label>Odds Tier<select value={tier} onChange={(event) => setTier(event.target.value)}><option>All Tiers</option>{BANDS.map(([name]) => <option key={name}>{name}</option>)}</select></label>
      <label>Status<select value={status} onChange={(event) => setStatus(event.target.value)}>{['All Status', 'Combined', 'High probability', 'Verified', 'Awaiting code', 'Unverified', 'Expired', 'Risk Review', 'Awaiting odds', 'Settled', 'Void'].map((item) => <option key={item}>{item}</option>)}</select></label>
      <label>Code<select value={automation} onChange={(event) => setAutomation(event.target.value)}>{['All', 'Has code', 'Awaiting code'].map((item) => <option key={item}>{item}</option>)}</select></label>
      <label className="tb-search">Search<input placeholder="Ticket, code, fixture or market" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
    </div>
    {tab === 'Generated Tickets' && <div className="tb-grid">
      <Panel title="Automatically Generated Tickets" icon={Layers} action={<div className="tb-actions">{combinedTickets.length > 0 && <button type="button" onClick={() => { setStatus(status === 'Combined' ? 'All Status' : 'Combined'); setPage(1); setSelectedId(combinedTickets[0].id); }}>{status === 'Combined' ? 'All tickets' : 'Combined'}</button>}<span className="tb-mini">{loading ? 'Loading' : 'Stored records'}</span><button onClick={load}><RefreshCw size={14} /> Refresh</button></div>}>
        <div className="tb-table-wrap"><table><thead><tr><th>#</th><th>Ticket / Code</th><th>Sport</th><th>Selections</th><th>Combined Odds</th><th>AI Probability</th><th>Status</th><th>Registered</th><th>Action</th></tr></thead><tbody>
          {tableRows.map((row, index) => {
            const individualIndex = row.combined ? index : (currentPage - 1) * pageSize + (index - (currentPage === 1 ? visibleCombined.length : 0)) + 1;
            return <tr key={row.id} className={`${selected?.id === row.id ? 'selected' : ''} ${row.combined ? 'combined' : ''}`.trim()}><td>{row.combined ? `C${row.combinedPart}` : individualIndex}</td><td><strong>{row.shownCode || 'No code'}</strong>{row.combined && <div className="tb-sub">From {row.memberCount} tickets at 75%+</div>}{memberIds.has(row.id) && <span className="tb-mark">Combined</span>}{row.shownCode && !row.combined && <button className="tb-icon" title="Copy code" onClick={() => copy(row.shownCode, row.shownCode)}><Copy size={13} /></button>}</td><td title={row.sportLabel}>{row.sportLabel === 'Basketball' ? '🏀' : row.sportLabel === 'Football' ? '⚽' : row.sportLabel === 'Ice hockey' ? '🏒' : '🌐'}</td><td>{row.leg_count}</td><td><strong>{oddsText(row.combined_odds)}</strong></td><td><Prob value={row.probability} /></td><td><Badge status={row.label} /></td><td>{lagosStamp(row.code?.created_at || row.created_at)}</td><td><button className="tb-view" onClick={() => { setSelectedId(row.id); setTab('Ticket Details'); }}><Eye size={14} /> View</button></td></tr>;
          })}
        </tbody></table>{!tableRows.length && <div className="tb-empty">{loading ? 'Loading stored tickets…' : 'No stored ticket matches these filters.'}</div>}
          {ranked.length > pageSize && <div className="tb-pager"><button type="button" disabled={currentPage <= 1} onClick={() => setPage(currentPage - 1)}>Previous</button><span>{currentPage} / {pageCount}</span><button type="button" disabled={currentPage >= pageCount} onClick={() => setPage(currentPage + 1)}>Next</button></div>}
        </div>
      </Panel>
      <Panel title={`Ticket Preview — ${selected?.shownCode || 'No code'}`} icon={Ticket} action={selected && <Badge status={selected.label} />}>
        {selected ? <>
          <div className="tb-metrics"><span>Selections: <b>{selected.leg_count}</b></span><span>Combined Odds: <b>{oddsText(selected.combined_odds)}</b></span><span>AI Probability: <b>{selected.probability == null ? 'Unverified' : `${selected.probability}%`}</b></span></div>
          <table className="tb-preview"><thead><tr><th>#</th><th>Fixture</th><th>Market</th><th>Odds</th><th>AI Prob.</th></tr></thead><tbody>
            {(selected.legs || []).map((leg, index) => <tr key={leg.id || index}><td>{index + 1}</td><td>{leg.home} vs {leg.away}</td><td>{marketText(leg)}</td><td>{oddsText(leg.decimal_odds)}</td><td>{leg.probability == null ? '—' : <span className="tb-pill">{percent(leg.probability)}%</span>}</td></tr>)}
          </tbody></table>
          <div className="tb-preview-actions"><button onClick={() => setModal(true)}>Full Ticket</button><button onClick={() => setTab('Code History')}>Code Evidence</button><button onClick={() => (selected.combined ? setNotice('This combined ticket has no booking code') : copy(selected.shownCode, selected.shownCode || 'Code'))}>Copy Code</button></div>
          <p className="tb-foot">{selected.explanation || 'Combined probability is the stored joint estimate for the whole ticket.'}</p>
        </> : <div className="tb-empty">Select a stored ticket.</div>}
      </Panel>
    </div>}
    {tab === 'Ticket Details' && <Panel title="Ticket Details" icon={ClipboardList} action={<button onClick={() => setTab('Generated Tickets')}>Back to tickets</button>}>
      {selected ? <div className="tb-detail"><div><small>{selected.combined ? 'Combined ticket' : 'Booking code'}</small><h2>{selected.combined ? selected.shownCode : (selected.shownCode || 'Not stored')}</h2><p>{selected.sportLabel} · {selected.leg_count} selections{selected.combined ? ` · ${selected.memberCount} tickets at 75%+` : ''}</p><Badge status={selected.label} /></div><div><small>Combined odds</small><h2>{oddsText(selected.combined_odds)}</h2><p>Joint probability: {selected.probability == null ? 'Unverified' : `${selected.probability}%`}</p></div><div><small>Evidence</small><h2>{selected.combined ? 'Not requested' : selected.code?.verification_method === 'displayed' ? 'Displayed' : 'Not displayed'}</h2><p>{selected.combined ? selected.explanation : 'A code is verified only while SportyBet has displayed it and it has not expired.'}</p><button disabled={!selected.shownCode || selected.combined} onClick={() => copy(selected.shownCode, selected.shownCode)}>Copy code</button></div></div> : <div className="tb-empty">No ticket is selected.</div>}
      {selected && <table className="tb-preview"><thead><tr><th>#</th><th>Fixture</th><th>Competition</th><th>Market</th><th>Odds</th><th>Model probability</th></tr></thead><tbody>{(selected.legs || []).map((leg, index) => <tr key={leg.id || index}><td>{index + 1}</td><td>{leg.home} vs {leg.away}</td><td>{leg.competition}</td><td>{marketText(leg)}</td><td>{oddsText(leg.decimal_odds)}</td><td>{leg.probability == null ? '—' : `${percent(leg.probability)}%`}</td></tr>)}</tbody></table>}
    </Panel>}
    {tab === 'Code History' && <Panel title="Booking Code History" icon={History}><table><thead><tr><th>Code</th><th>Status</th><th>Method</th><th>Bookmaker</th><th>Stored</th></tr></thead><tbody>
      {codes.map((code) => <tr key={code.id}><td><strong>{code.code}</strong></td><td><Badge status={code.status === 'verified' && code.verification_method === 'displayed' ? 'Verified' : code.status === 'expired' ? 'Expired' : 'Unverified'} /></td><td>{code.verification_method || '—'}</td><td>{code.bookmaker || '—'}</td><td>{lagosStamp(code.created_at)}</td></tr>)}
    </tbody></table>{!codes.length && <div className="tb-empty">No booking code is stored.</div>}</Panel>}
    {tab === 'Automation Log' && <Panel title="Scheduler Events" icon={Activity}><table><thead><tr><th>Time</th><th>Event</th><th>Level</th></tr></thead><tbody>
      {(overview?.logs || []).map((item, index) => <tr key={`${item.time}-${index}`}><td>{lagosStamp(item.time)}</td><td>{item.message}</td><td>{item.level}</td></tr>)}
    </tbody></table>{!(overview?.logs || []).length && <div className="tb-empty">No scheduler event is stored.</div>}</Panel>}
    {tab === 'Auto Settings' && <Panel title="Automation Rules" icon={Settings2}>
      <div className="tb-setting"><div><b>Scheduler</b><p>The worker keeps its own loop. This page does not pause it and does not place a wager.</p></div><span className={`tb-live ${active ? '' : 'off'}`}>{active ? 'Running' : 'Not fresh'}</span></div>
      <div className="tb-setting"><div><b>Booking codes</b><p>A code is stored when SportyBet displays it. Prepared tickets stay without a code until that happens.</p></div><span className="tb-mini">Displayed evidence</span></div>
      <div className="tb-setting"><div><b>Paid wagering</b><p>Stake, place bet, and deposit are not submitted by this page.</p></div><span className="tb-status failed">Disabled</span></div>
    </Panel>}
    <div className="tb-bottom">
      <Panel title="Automation Performance" icon={BarChart3} action={<small>Stored</small>}>
        <div className="tb-perf">{[
          ['Qualified in view', rows.filter((row) => row.status === 'qualified').length, CheckCircle2],
          ['Codes stored', codes.length, Check],
          ['Still verified', codes.length ? `${verified}/${codes.length}` : '—', TrendingUp],
          ['Last build', buildSeconds == null ? '—' : buildSeconds < 60 ? `${buildSeconds}s` : `${Math.floor(buildSeconds / 60)}m ${buildSeconds % 60}s`, Clock3],
        ].map(([label, value, Icon]) => <div className="tb-perf-item" key={label}><Icon size={22} /><span>{label}</span><b>{value}</b></div>)}</div>
      </Panel>
      <Panel title="Tickets by Odds Tier" icon={Layers}>
        <div className="tb-tiers">{tierCounts.map(([name, count]) => <div className="tb-tier" key={name}><span>{name}</span><div><i style={{ width: `${count / tierMax * 100}%` }} /></div><b>{count}</b></div>)}</div>
        <p className="tb-foot">Counts use the latest stored version of each ticket in this list.</p>
      </Panel>
      <Panel title="Autonomous Status" icon={Bot}>
        <div className="tb-status-grid"><div>{(overview?.tasks || []).slice(0, 6).map((task) => <div className="tb-service" key={task.name}><CheckCircle2 size={15} /><span>{task.name}</span><b>{task.status}</b></div>)}{!(overview?.tasks || []).length && <div className="tb-empty">No schedule is stored.</div>}</div>
          <div className="tb-cycles"><div><Clock3 size={22} /><span>Next cycle<b>{countdown(overview?.next_cycle, now)}</b></span></div><div><RefreshCw size={22} /><span>Heartbeat<b>{overview?.scheduler_fresh ? 'Fresh' : 'Stale'}</b></span></div></div>
        </div>
      </Panel>
    </div>
    {notice && <div className="tb-toast" role="status">{notice}<button onClick={() => setNotice('')}>×</button></div>}
    {modal && selected && <div className="tb-modal-back" onClick={() => setModal(false)}><div className="tb-modal" onClick={(event) => event.stopPropagation()}><button className="tb-modal-close" onClick={() => setModal(false)}>×</button><h2>{selected.shownCode || 'No booking code'}</h2><p>{selected.sportLabel} · {selected.leg_count} selections · Odds {oddsText(selected.combined_odds)}</p><p>Joint probability: {selected.probability == null ? 'Unverified' : `${selected.probability}%`}</p><Badge status={selected.label} />{(selected.legs || []).map((leg, index) => <p key={leg.id || index}>{index + 1}. {leg.home} vs {leg.away} · {marketText(leg)} · {oddsText(leg.decimal_odds)}</p>)}</div></div>}
  </div>;
}
