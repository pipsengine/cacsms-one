import React from 'react';
import {useNavigate} from 'react-router-dom';
import {BadgeCheck,BarChart3,ChevronDown,Clock3,Copy,Dribbble,Eye,Goal,Hash,Layers3,Link2,Play,RefreshCw,Target,TriangleAlert,Trophy,X} from 'lucide-react';
import {get} from './api';
import WorkflowBoard from './WorkflowBoard';
import BookingDashboard from './BookingDashboard';
import useLiveResource from './useLiveResource';
import './command.css';

const fmt = value => Number(value || 0).toLocaleString('en-US');
const when = value => {
  if (!value) return '—';
  const parsed = new Date(String(value).includes('T') ? value : String(value).replace(' ', 'T') + 'Z');
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString([], {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'});
};
function edgeText(value) {
  return value === null || value === undefined ? 'Awaiting Odds' : `${(Number(value) * 100).toFixed(1)}%`;
}
function pickTeam(row) {
  const market = String(row.market || '');
  if (market === 'HOME' || market === 'MONEYLINE_HOME') return {side: 'home', label: `${row.home} to win`};
  if (market === 'AWAY' || market === 'MONEYLINE_AWAY') return {side: 'away', label: `${row.away} to win`};
  if (market === 'DRAW') return {side: 'draw', label: 'Draw'};
  return null;
}
function stamp(value) {
  if (!value) return null;
  const parsed = new Date(String(value).includes('T') ? value : String(value).replace(' ', 'T') + 'Z');
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}
function ago(value) {
  const parsed = stamp(value);
  if (!parsed) return 'Not run';
  const minutes = Math.round((Date.now() - parsed.getTime()) / 60000);
  if (minutes < 1) return 'Completed just now';
  if (minutes < 60) return `Completed ${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `Completed ${hours} hr ago`;
  return `Completed ${Math.round(hours / 24)} days ago`;
}
function clock(value) {
  const parsed = stamp(value);
  if (!parsed) return 'not scheduled';
  return parsed.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
}
function shown(value) {
  return value === null || value === undefined ? '—' : fmt(value);
}
function ahead(value) {
  const parsed = stamp(value);
  if (!parsed) return 'Unscheduled';
  const minutes = Math.round((parsed.getTime() - Date.now()) / 60000);
  if (minutes < 0) return 'Due now';
  if (minutes < 60) return `In ${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 36) return `In ${hours} hr`;
  return `In ${Math.round(hours / 24)} days`;
}
function Badge({children, tone}) {
  return <span className={`sp-status ${tone || 'neutral'}`}>{children}</span>;
}

function integrationRows(data) {
  const providers = Object.fromEntries((data?.providers || []).map(item => [item.provider_key, item]));
  const providerLine = key => {
    const item = providers[key];
    if (!item || !item.enabled) return ['off', 'Not configured'];
    if (item.status === 'operational') return ['ok', 'Online'];
    return ['wait', item.status];
  };
  return [
    ['Sports AI API', data ? 'ok' : 'bad', data ? 'Connected' : 'Unavailable'],
    ['SQLite database', 'ok', 'Connected'],
    ['football-data.org', ...providerLine('football-data')],
    ['OpenLigaDB', ...providerLine('openligadb')],
    ['OpenFootball dataset', ...providerLine('openfootball')],
    ['Basketball dataset', ...providerLine('sportsdataverse')],
    ['SportyBet integration', data?.browser?.sportybet_unattended?.operational ? 'ok' : 'wait', data?.browser?.sportybet_unattended?.operational ? 'Collecting displayed prices' : data?.browser?.sportybet_unattended?.authorized ? 'Waiting for prices' : 'Not collecting'],
    ['Authorized browser', data?.browser?.authorized_urls ? 'ok' : 'wait', data?.browser?.authorized_urls ? `${data.browser.inspected || 0} pages read` : 'No authorized sites'],
    ['Email / Telegram alerts', 'off', 'Not configured'],
  ];
}

export function IntegrationStatus({data}) {
  return <section className="cc-panel">
    <header><strong><Link2 size={18}/>System Status & Integrations</strong></header>
    {integrationRows(data).map(([name, tone, label]) => <div className="cc-row" key={name}><i className={`cc-dot ${tone}`}/><span>{name}</span><Badge tone={tone === 'ok' ? 'success' : tone === 'wait' ? 'warning' : 'neutral'}>{label}</Badge></div>)}
  </section>;
}

export function AutonomousPipeline({data}) {
  const fresh = data?.scheduler?.heartbeat_at && Date.now() - new Date(data.scheduler.heartbeat_at).getTime() < 120000;
  const providers = Object.fromEntries((data?.providers || []).map(item => [item.provider_key, item]));
  const pipeline = [
    ['Fixture collection', providers.openligadb?.last_success_at || providers.openfootball?.last_success_at, 'ok'],
    ['Historical sync', providers.openligadb?.last_success_at, 'ok'],
    ['Feature engineering', data?.last_analysis || data?.last_run, (data?.last_analysis || data?.last_run) ? 'ok' : 'wait'],
    ['AI predictions', data?.last_analysis || data?.last_run, data?.predictions ? 'ok' : 'wait'],
    ['Odds synchronization', null, data?.odds_snapshots ? 'ok' : 'wait'],
    ['Authorized browser', data?.browser?.last_finished_at, data?.browser?.authorized_urls ? 'ok' : 'wait'],
    ['Value analysis', null, data?.qualified_opportunities ? 'ok' : 'wait'],
    ['Accumulator generation', null, data?.accumulator_tickets ? 'ok' : 'wait'],
    ['Result monitoring', (data?.schedules || []).find(item => item.job_kind === 'settlement')?.last_run_at, 'ok'],
  ];
  return <section className="cc-panel">
    <header><strong><Layers3 size={18}/>Autonomous Pipeline</strong><Badge tone={fresh ? 'success' : 'warning'}>{fresh ? 'Running' : 'Idle'}</Badge></header>
    {pipeline.map(([name, time, tone]) => {
      const oddsFresh = name.startsWith('Odds') && data?.fresh_odds_fixtures;
      const valueReady = name.startsWith('Value') && data?.qualified_opportunities;
      const ticketsReady = name.startsWith('Accumulator') && data?.accumulator_tickets;
      const label = oddsFresh ? `${fmt(data.fresh_odds_fixtures)} fresh` : name.startsWith('Odds') ? 'No fresh quote' : name.startsWith('Authorized') && !data?.browser?.authorized_urls ? 'No authorized sites' : name.startsWith('Authorized') && data?.browser?.failed ? `${data.browser.failed} failed` : name.startsWith('Authorized') && data?.browser?.queued ? `${data.browser.queued} queued` : valueReady ? `${fmt(data.qualified_opportunities)} qualified` : name.startsWith('Value') ? 'Blocked' : ticketsReady ? `${fmt(data.accumulator_tickets)} stored` : name.startsWith('Accumulator') ? 'Waiting' : ago(time);
      const warning = (name.startsWith('Odds') && !oddsFresh) || (name.startsWith('Authorized') && !data?.browser?.authorized_urls) || (name.startsWith('Value') && !valueReady) || (name.startsWith('Accumulator') && !ticketsReady) || tone === 'wait';
      return <div className="cc-row" key={name}><i className={`cc-dot ${warning ? 'wait' : 'ok'}`}/><span>{name}</span><Badge tone={warning ? 'warning' : 'success'}>{label}</Badge></div>;
    })}
    {(data?.model_governance?.decisions || []).map(item => <p className="cc-note" key={item.sport}><b>{item.sport}.</b> {item.action === 'activated' || item.action === 'already_active' ? `Active after holdout validation. Calibration error ${(Number(item.calibration_error) * 100).toFixed(1)}%.` : (item.reasons || []).join(' ')}</p>)}
    {(data?.pipeline_blockers || []).map(item => <p className="cc-note" key={item.stage}><b>{item.stage}.</b> {item.reason}</p>)}
  </section>;
}

function selectionLabel(leg) {
  const market = String(leg.market || leg.selection || '');
  if (market === 'HOME' || market === 'MONEYLINE_HOME') return `${leg.home} to win`;
  if (market === 'AWAY' || market === 'MONEYLINE_AWAY') return `${leg.away} to win`;
  if (market === 'DRAW') return 'Draw';
  return market.replaceAll('_', ' ');
}

function BookingCentre({data, nav}) {
  const centre = data?.booking_centre;
  const [open, setOpen] = React.useState(null);
  const [copied, setCopied] = React.useState('');
  React.useEffect(() => {
    if (!copied) return undefined;
    const timer = setTimeout(() => setCopied(''), 1600);
    return () => clearTimeout(timer);
  }, [copied]);
  async function copy(code) {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(code);
      return;
    } catch {
      const field = document.createElement('textarea');
      field.value = code;
      field.setAttribute('readonly', '');
      field.style.position = 'fixed';
      field.style.left = '-9999px';
      document.body.appendChild(field);
      field.select();
      const copiedOk = document.execCommand('copy');
      field.remove();
      setCopied(copiedOk ? code : '');
    }
  }
  const counts = centre?.counts || {};
  const tickets = centre?.tickets || [];
  const tone = {verified: 'success', ready: 'success', pending: 'warning', awaiting_code: 'warning', unverified: 'neutral', expired: 'neutral'};
  const category = {football: 'Football', basketball: 'Basketball', mixed: 'Mixed'};
  return <section className="cc-panel booking">
    <header>
      <strong><Hash size={18}/><span>SportyBet Booking Codes<small>Ticket Centre</small></span></strong>
      <button className="cc-link" onClick={() => nav('codes')}>View All Codes</button>
    </header>
    {!centre && <p className="cc-note">Loading stored tickets…</p>}
    {centre && <>
      <div className="cc-book-stats">
        <div className="verified"><strong>{fmt(counts.verified_codes)}</strong><span>Displayed codes</span></div>
        <div className="prepared"><strong>{fmt(counts.ready_for_window)}</strong><span>Ready for the next window</span></div>
        <div className="awaiting"><strong>{fmt(counts.not_bookable)}</strong><span>Stored, not bookable</span></div>
      </div>
      <p className="cc-note">One ready ticket is sent to SportyBet only while a publication window is open. A code is stored only when SportyBet displays it. Place bet is never used. Stored tickets that are not ready are not a queue.{centre.last_attempt?.error ? ` Latest attempt: ${centre.last_attempt.error}` : ''}</p>
      <p className="cc-book-caption">Ready tickets and displayed codes</p>
      {!tickets.length && <p className="cc-note">No prepared accumulator ticket is stored.</p>}
      {tickets.map(ticket => <article className={`cc-slip ${ticket.code_status}`} key={ticket.id}>
        <div className="cc-slip-head">
          <button type="button" className="cc-slip-toggle" aria-expanded={open === ticket.id} onClick={() => setOpen(open === ticket.id ? null : ticket.id)}>
            <span className={`cc-chip ${ticket.sport_mode}`}>{category[ticket.sport_mode] || ticket.sport_mode}</span>
            <span className="cc-slip-meta">{ticket.matchup || `${ticket.leg_count} selections`} · {Number(ticket.combined_odds || 0).toFixed(2)} combined · {ticket.code ? 'Displayed' : 'Checked'} {when(ticket.generated_at || ticket.created_at)}</span>
            <ChevronDown size={14} className={open === ticket.id ? 'open' : ''}/>
          </button>
          <div className={`cc-slip-side${ticket.code ? ' has-code' : ''}`}>
            {ticket.code && <div className="cc-code-row"><code>{ticket.code}</code><button type="button" className="cc-copy" onClick={() => copy(ticket.code)}><Copy size={12}/>{copied === ticket.code ? 'Copied' : 'Copy'}</button></div>}
            <Badge tone={tone[ticket.code_status] || 'neutral'}>{ticket.code_label || 'Awaiting Code'}</Badge>
          </div>
        </div>
        {open === ticket.id && <div className="cc-legs">
          {(ticket.legs || []).map(leg => <div className="cc-leg" key={leg.id}>
            <b>{leg.home} vs {leg.away}</b>
            <span>{leg.competition}</span>
            <span>{selectionLabel(leg)} · {leg.market} · {Number(leg.decimal_odds).toFixed(2)} · {leg.probability == null ? 'Probability not stored' : `${(Number(leg.probability) * 100).toFixed(1)}%`}</span>
          </div>)}
          {!ticket.legs?.length && <p className="cc-note">No selections are stored on this ticket.</p>}
          <p className="cc-note">Product target {ticket.tier}×. Combined price {Number(ticket.combined_odds || 0).toFixed(2)}.</p>
        </div>}
      </article>)}
    </>}
  </section>;
}

function lagosClock() {
  const text = new Date().toLocaleString('en-GB', {weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Africa/Lagos'});
  return `${text.replace(',', '')} WAT (Africa/Lagos)`;
}
function wat(value) {
  if (!value) return '—';
  const parsed = new Date(String(value).includes('T') ? value : String(value).replace(' ', 'T') + 'Z');
  if (Number.isNaN(parsed.getTime())) return '—';
  return new Intl.DateTimeFormat('en-GB', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Africa/Lagos',
  }).format(parsed);
}
function ProbBar({value}) {
  const width = value == null ? 0 : Math.max(0, Math.min(100, Number(value)));
  const tone = value == null ? 'empty' : width >= 65 ? 'high' : width >= 30 ? 'mid' : 'low';
  return <span className={`ccx-prob ${tone}`}><i style={{width: `${width}%`}}/><b>{value == null ? '—' : `${Number(value).toFixed(1)}%`}</b></span>;
}
function stillToPlay(row) {
  const legs = row.legs || [];
  const pending = legs.length ? legs : [{starts_at: row.starts_at, result: 'pending'}];
  return pending.every(leg => {
    const kickoff = Date.parse(leg.starts_at);
    const open = !leg.result || leg.result === 'pending';
    return open && Number.isFinite(kickoff) && kickoff > Date.now();
  });
}
function pillClass(status) {
  if (['Qualified', 'Active', 'Ready', 'Open', 'Won'].includes(status)) return 'qualified';
  if (status === 'Risk Review' || status === 'Lost') return 'risk';
  if (['Monitoring', 'Research', 'In progress', 'Expired'].includes(status)) return 'monitoring';
  return 'unsupported';
}

export function SportsCoveragePipeline({data, nav}) {
  const view = data?.command_view || {};
  const pipelineMax = Math.max(1, ...(view.pipeline || []).map(item => Number(item.count) || 0));
  return (<div className="ccx ccx-monitor-panels">
      <section className="ccx-card">
        <header><strong><Dribbble size={16}/>Sports Coverage</strong><button className="ccx-link" onClick={() => nav('sources')}>View All</button></header>
        <div className="ccx-coverage ccx-card-scroll">{(view.sports || []).map(item => <div key={item.name}><b>{item.name}</b><span>{fmt(item.fixtures)} fixtures</span><em className={pillClass(item.status)}>{item.status}</em></div>)}{!(view.sports || []).length && <p className="ccx-empty">No upcoming fixture is stored.</p>}</div>
      </section>
      <section className="ccx-card">
        <header><strong><BarChart3 size={16}/>Pipeline <small>(Current Cycle)</small></strong><span className={`ccx-pill ${view.engine_online ? 'qualified' : 'monitoring'}`}>{view.engine_online ? 'Operational' : 'Idle'}</span></header>
        <div className="ccx-pipeline ccx-card-scroll">{(view.pipeline || []).map(item => <div key={item.name}><span>{item.name}</span><i><b style={{width: `${Math.max(4, (Number(item.count) || 0) / pipelineMax * 100)}%`}}/></i><strong>{fmt(item.count)}</strong></div>)}{!(view.pipeline || []).length && <p className="ccx-empty">The latest worker cycle has not reported a snapshot.</p>}</div>
      </section>
    </div>);
}

export function LiveOpportunities({data, nav}) {
  const navigate = useNavigate();
  const view = data?.command_view || {};
  const [sport, setSport] = React.useState('All');
  const live = useLiveResource(`/high-odds/opportunities${sport === 'All' ? '' : `?sport=${encodeURIComponent(sport)}`}`);
  function openFixture(row) {
    if (!row.fixture_id) return;
    const sportKey = row.sport === 'basketball' ? 'basketball' : 'football';
    navigate(`/sports/${sportKey}-analysis?fixture=${encodeURIComponent(row.fixture_id)}`, {
      state: {fixture: {id: row.fixture_id, sport: row.sport, competition: row.competition, home: row.home, away: row.away, kickoff_at: row.starts_at}},
    });
  }
  const filters = [{code:'All',name:'All'}, ...(live.data?.sports || []).filter(item => ['football','basketball','tennis','ice_hockey'].includes(item.code))];
  const rows = live.data?.items || view.opportunities || [];
  return (<div className="ccx ccx-primary ccx-monitor-opportunities">
      <section className="ccx-card">
        <header><strong><Target size={16}/>Live Opportunities <small>(Top 10)</small></strong><div className="ccx-filters">{filters.map(item => <button key={item.code} className={sport === item.code ? 'on' : ''} onClick={() => setSport(item.code)}>{item.name}</button>)}<select aria-label="Filter live opportunities by sport" value={sport} onChange={e=>setSport(e.target.value)}><option value="All">All sports</option>{(live.data?.sports||[]).map(item=><option key={item.code} value={item.code}>{item.name}</option>)}</select><button className="ccx-link" onClick={() => nav('qualification')}>View All</button></div></header>
        <p className="ccx-high-note" role="status">{live.error || `Refreshes every 15 seconds · Updated ${wat(live.data?.generated_at)} WAT · Current qualified opportunities, ranked by probability`}</p>
        <div className="ccx-table-scroll"><table className="ccx-table"><thead><tr><th>#</th><th>Sport</th><th>Fixture</th><th>League</th><th>Start Date/Time</th><th>Market</th><th>Selection</th><th>AI Probability</th><th>Odds</th><th>Status</th></tr></thead><tbody>
          {rows.map((row, index) => <tr key={`${row.fixture_id || row.fixture}-${row.selection}`} onClick={() => openFixture(row)}><td>{index + 1}</td><td><span className="ccx-sport">{row.sport === 'basketball' ? <Dribbble size={13}/> : <Goal size={13}/>}{row.sport_label}</span></td><td>{row.fixture}</td><td>{row.competition || '—'}</td><td>{wat(row.starts_at)}{row.starts_at ? <small className="ccx-wat">WAT</small> : null}</td><td>{row.market}</td><td>{row.selection}</td><td><ProbBar value={row.probability}/></td><td className="ccx-odds">{row.odds == null ? '—' : Number(row.odds).toFixed(2)}</td><td><span className={`ccx-pill ${pillClass(row.status)}`}>{row.status}</span></td></tr>)}
          {!rows.length && <tr><td colSpan="10">{live.loading ? 'Checking current opportunities…' : live.data?.empty_reason || 'No current qualified opportunities.'}{(live.data?.qualification_reasons||[]).slice(0,3).map(item=><small style={{display:'block',marginTop:5}} key={item.reason_code}>{item.reason_code.replaceAll('_',' ')}: {item.total}</small>)}</td></tr>}
        </tbody></table></div>
      </section>

    </div>);
}

export function KeyAlerts({data, nav}) {
  const view = data?.command_view || {};
  return <div className="ccx ccx-monitor-alerts"><section className="ccx-card ccx-alerts">
        <header><strong><TriangleAlert size={16}/>Key Alerts</strong><button className="ccx-link" onClick={() => nav('alerts')}>View All</button></header>
        {(view.alerts || []).map(item => <p key={item.id}><TriangleAlert size={14}/>{item.message}</p>)}
        {!(view.alerts || []).length && <p className="ccx-empty">No alert is stored for this cycle.</p>}
      </section></div>;
}

export default function CommandCentre({data, nav, run, loading, refresh}) {
  const view = data?.command_view || {};
  const sure = useLiveResource('/sure-odds');
  const sureView = sure.data || view;
  const [now, setNow] = React.useState(lagosClock);
  React.useEffect(() => {
    const timer = setInterval(() => setNow(lagosClock()), 30000);
    return () => clearInterval(timer);
  }, []);
  const performance = view.performance || {};
  const windowLabel = view.publication_open ? 'Open now' : clock(view.next_window);
  return <div className="ccx">
    <header className="ccx-top">
      <div>
        <h1>Sports Intelligence Command Centre</h1>
        <p>Multi-sport · Multi-market · AI probabilities · Booking codes · Draw games · Autonomous</p>
      </div>
      <div className="ccx-top-side">
        <span className="ccx-clock"><Clock3 size={15}/>{now}</span>
        <span className="ccx-env">{view.environment || 'Development'}</span>
        <button className="ccx-refresh" onClick={() => { refresh(); sure.refresh(); }}><RefreshCw size={15}/>Refresh</button>
        <span className="ccx-env">Live · worker</span>
      </div>
    </header>
    <WorkflowBoard workflow={view.workflow} nav={nav} />
    <div className="ccx-sure-row">
      <section className="ccx-card ccx-sure">
        <header><strong><Goal size={16}/>Sure Odds For (Today) <small>{sureView.sure_odds_date || 'Today'}</small></strong><button className="ccx-link" onClick={() => nav('tickets')}>View All</button></header>
        {sure.error && <p className="ccx-high-note" role="status">{sure.error}{sure.data ? ' Showing the last successful update.' : ''}</p>}
        <div className="ccx-table-scroll ccx-card-scroll"><table className="ccx-table compact"><colgroup><col className="ccx-sure-num"/><col className="ccx-sure-fixture"/><col className="ccx-sure-league"/><col className="ccx-sure-code"/><col className="ccx-sure-time"/><col className="ccx-sure-price"/><col className="ccx-sure-confidence"/></colgroup><thead><tr><th>#</th><th>Fixture</th><th>League</th><th>Booking code</th><th>Match Date/time</th><th>Odds</th><th>AI Confidence</th></tr></thead><tbody>
          {(sureView.sure_odds || []).map(ticket => <React.Fragment key={ticket.id || ticket.selections.map(leg => `${leg.fixture}-${leg.starts_at}`).join('|')}>
            <tr className="ccx-sure-ticket"><td colSpan="7"><b>{ticket.label}</b><span>{ticket.odds_band} · {ticket.selection_count} selections · {ticket.probability_source === 'market' ? 'stored combined' : 'combined'} {ticket.combined_odds == null ? '—' : Number(ticket.combined_odds).toFixed(2)} · joint {ticket.joint_probability == null ? '—' : `${Number(ticket.joint_probability).toFixed(1)}%`} · {ticket.code || ticket.booking_note || 'Awaiting SportyBet booking code'}</span>{ticket.probability_source === 'market' && <small className="ccx-wat">Market-priced ticket; probabilities are derived from bookmaker prices; AI confidence is unavailable.</small>}</td></tr>
            {(ticket.selections || []).map((row, index) => <tr key={`${row.fixture}-${row.starts_at}-${index}`}><td>{index + 1}</td><td className="ccx-sure-match"><b>{row.fixture}</b><small>{row.selection || 'Selection not stored'}{row.probability == null ? '' : ` · ${Number(row.probability).toFixed(1)}%`}</small></td><td>{row.league || '—'}</td><td>{ticket.code || '—'}</td><td>{wat(row.starts_at)}{row.starts_at ? <small className="ccx-wat">WAT</small> : null}</td><td className="ccx-odds">{row.odds == null ? '—' : Number(row.odds).toFixed(2)}</td><td>{ticket.probability_source === 'market' ? <small>Not model-rated</small> : <ProbBar value={row.confidence}/>}</td></tr>)}
          </React.Fragment>)}
          {!(sureView.sure_odds || []).length && <tr><td colSpan="7">
            {!sure.data && !data?.command_view ? <p>{sure.loading ? "Checking today's qualified tickets..." : 'Ticket data is currently unavailable. Retrying automatically.'}</p> : <>
            <p>{sureView.sure_odds_status?.message || 'No ready ticket priced from 2.00 to 2.95 is still to play today.'}</p>
            {!!sureView.sure_odds_status?.blockers?.length && <p>{sureView.sure_odds_status.blockers.slice(0, 4).map(item => `${item.label}: ${item.count}`).join(' · ')}<small className="ccx-wat">Latest decisions per match and market; counts are not matches.</small></p>}
            <p>Last source collection: {sureView.sure_odds_status?.last_collection_at ? `${wat(sureView.sure_odds_status.last_collection_at)} WAT` : 'Not recorded'} · Last Sure Odds ticket: {sureView.sure_odds_status?.last_ticket_at ? `${wat(sureView.sure_odds_status.last_ticket_at)} WAT` : 'Not recorded'}</p>
            <small>Tickets disappear once their first match starts. Model validation and price checks still apply.</small>
            </>}
          </td></tr>}
        </tbody></table></div>
      </section>
    </div>
    <div className="ccx-bottom ccx-bottom-summary">
      <section className="ccx-card">
        <header><strong><BarChart3 size={16}/>Performance</strong></header>
        <div className="ccx-metrics">
          <div><i className="green"><Target size={16}/></i><span>Bet Win Rate</span><strong>{performance.win_rate == null ? 'Awaiting results' : `${performance.win_rate}%`}</strong><small>{shown(performance.bets_placed)} real-money bets placed</small></div>
          <div><i className="blue"><BarChart3 size={16}/></i><span>Predictions (30 days)</span><strong>{shown(performance.predictions_30d)}</strong><small>Every market and line priced</small></div>
          <div><i className="teal"><Hash size={16}/></i><span>Average Bet Odds</span><strong>{performance.average_odds == null ? '—' : Number(performance.average_odds).toFixed(2)}</strong><small>Real-money bets placed</small></div>
        </div>
      </section>
      <section className="ccx-card ccx-action">
        <header><strong><Clock3 size={16}/>Next Key Action</strong></header>
        <p>Next publication window</p>
        <strong>{windowLabel} WAT</strong>
        <small>{!view.publication_open ? 'Africa/Lagos.' : (view.kpis?.slips || 0) > 0 ? 'One ready ticket can be sent to SportyBet for a booking code.' : 'The window is open, but no ticket is ready for a booking code yet.'}</small>
      </section>

    </div>
    <BookingDashboard embedded/>
    <footer className="ccx-engine"><span className={view.engine_online ? 'on' : 'off'}/>{view.engine_online ? 'Engine Online' : 'Engine idle'}<small>Last sync {when(view.updated_at)}</small><small>Next window {windowLabel} WAT</small></footer>

  </div>;
}

const PREDICTION_GROUPS = {
  football: ['', 'result', 'goals', 'handicap'],
  basketball: ['', 'moneyline', 'totals', 'spreads'],
};

export function PredictionBoard({sport, tab = 0}) {
  const marketGroup = (PREDICTION_GROUPS[sport] || [])[tab] || '';
  const [payload, setPayload] = React.useState({items: [], total: 0, page: 1, page_size: 25});
  const [page, setPage] = React.useState(1);
  const [pageSize, setPageSize] = React.useState(25);
  const [selected, setSelected] = React.useState(null);
  const [markets, setMarkets] = React.useState([]);
  const [error, setError] = React.useState('');
  React.useEffect(() => { setPage(1); }, [sport, marketGroup, pageSize]);
  React.useEffect(() => {
    let cancel = false;
    setPayload({items: [], total: 0, page, page_size: pageSize});
    const params = new URLSearchParams({group: 'fixture', page: String(page), page_size: String(pageSize)});
    if (sport) params.set('sport', sport);
    if (marketGroup) params.set('market_group', marketGroup);
    get('/predictions?' + params.toString()).then(value => {
      if (cancel) return;
      const rows = value.items || [];
      if (sport && rows.some(row => row.sport && row.sport !== sport)) return;
      setPayload(value);
    }).catch(reason => { if (!cancel) setError(reason.message); });
    return () => { cancel = true; };
  }, [sport, marketGroup, page, pageSize]);
  async function open(row) {
    setSelected(row);
    setMarkets([]);
    setMarkets(await get(`/fixtures/${encodeURIComponent(row.fixture_id)}/analysis`));
  }
  const items = payload.items || [];
  const total = payload.total || 0;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return <div>
    {error && <p className="sp-ops-error">{error}</p>}
    <div className="sp-filter-bar">
      <label>Rows<select value={pageSize} onChange={event => setPageSize(Number(event.target.value))}><option value="25">25</option><option value="50">50</option><option value="100">100</option></select></label>
    </div>
    <div className="sp-table-scroll sp-table-limit"><table className="sp-table"><thead><tr><th>Fixture</th><th>Competition</th><th>Kickoff</th><th>Analysis</th><th>Top market</th><th>AI Probability</th><th>Odds</th><th>Edge</th><th>Model</th><th></th></tr></thead><tbody>
      {items.map(row => <tr key={row.fixture_id}><td>{row.home} vs {row.away}</td><td>{row.competition}</td><td>{when(row.starts_at)}</td><td>{row.analysis_status || 'stored prediction'}</td><td>{row.market}</td><td>{`${(Number(row.probability) * 100).toFixed(1)}%`}</td><td>{row.odds == null ? 'Awaiting Odds' : Number(row.odds).toFixed(2)}</td><td>{edgeText(row.edge)}</td><td>{row.model_name} v{row.model_version}{row.model_active ? '' : ' · inactive'}</td><td><button className="sp-text-action" onClick={() => open(row)}>View</button></td></tr>)}
      {!items.length && <tr><td className="sp-empty" colSpan="10">No upcoming fixture has a stored prediction yet. The scheduler analyses a small batch at a time.</td></tr>}
    </tbody></table></div>
    <div className="sp-pager"><span>{total ? (page - 1) * pageSize + 1 : 0}–{Math.min(total, page * pageSize)} of {fmt(total)}</span><button className="sp-secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button><span>Page {page} of {pages}</span><button className="sp-secondary" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button></div>
    {selected && <div className="sp-intel-overlay" onMouseDown={event => { if (event.target === event.currentTarget) setSelected(null); }}><section className="sp-panel sp-intel-dialog" role="dialog" aria-modal="true"><div className="sp-panel-head"><div><h2>{selected.home} vs {selected.away}</h2><p>{selected.competition} · {when(selected.starts_at)} · {selected.market_count} markets in the latest run</p></div><button className="sp-secondary" onClick={() => setSelected(null)}>Close</button></div><div className="sp-table-scroll sp-table-limit"><table className="sp-table"><thead><tr><th>Market</th><th>Probability</th><th>Model</th></tr></thead><tbody>{markets.slice(0, 40).map(row => <tr key={row.id}><td>{row.market}</td><td>{`${(Number(row.probability) * 100).toFixed(1)}%`}</td><td>{row.model_name} v{row.model_version}</td></tr>)}</tbody></table></div></section></div>}
  </div>;
}
