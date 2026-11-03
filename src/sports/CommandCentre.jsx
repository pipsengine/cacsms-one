import React from 'react';
import {useNavigate} from 'react-router-dom';
import {AlertCircle,ArrowLeft,BarChart3,BrainCircuit,CalendarDays,Clock3,Database,Flame,Layers3,Link2,Play,RefreshCw,Target,Ticket} from 'lucide-react';
import {get} from './api';
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

export default function CommandCentre({data, nav, run, loading, refresh}) {
  const navigate = useNavigate();
  function openFixture(row) {
    const sport = row.sport === 'basketball' ? 'basketball' : 'football';
    navigate(`/sports/${sport}-analysis?fixture=${encodeURIComponent(row.fixture_id)}`, {
      state: {
        fixture: {
          id: row.fixture_id,
          sport: row.sport,
          competition: row.competition,
          home: row.home,
          away: row.away,
          kickoff_at: row.starts_at,
        },
      },
    });
  }
  const upcoming = (data?.football_fixtures || 0) + (data?.basketball_fixtures || 0);
  const fresh = data?.scheduler?.heartbeat_at && Date.now() - new Date(data.scheduler.heartbeat_at).getTime() < 120000;
  const activity = data?.activity_today || {};
  const activityRows = [
    ['Fixtures collected', activity.fixtures || 0, '#2563eb'],
    ['Predictions generated', activity.predictions || 0, '#7c3aed'],
    ['Odds updated', activity.odds || 0, '#0891b2'],
    ['Value rows evaluated', activity.opportunities || 0, '#059669'],
    ['Accumulator tickets', activity.tickets || 0, '#d97706'],
    ['Results stored', activity.results || 0, '#0f766e'],
  ];
  const peak = Math.max(1, ...activityRows.map(row => row[1]));
  const sports = data?.prediction_sports || [];
  const sportTotal = sports.reduce((sum, row) => sum + row.total, 0) || 1;
  const won = data?.settled_outcomes?.won || 0;
  const lost = data?.settled_outcomes?.lost || 0;
  const pending = upcoming;
  const settledTotal = won + lost + pending;
  const wonPct = settledTotal ? (won / settledTotal) * 100 : 0;
  const lostPct = settledTotal ? (lost / settledTotal) * 100 : 0;
  const providers = Object.fromEntries((data?.providers || []).map(item => [item.provider_key, item]));
  const providerLine = key => {
    const item = providers[key];
    if (!item || !item.enabled) return ['off', 'Not configured'];
    if (item.status === 'operational') return ['ok', 'Online'];
    return ['wait', item.status];
  };
  const oddsLine = data?.odds_snapshots ? ['ok', 'Quotes stored'] : ['wait', 'Awaiting Odds'];
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
  const tiers = [2, 5, 10, 25, 100, 1000, 5000, 25000];
  const tierCount = Object.fromEntries((data?.tier_counts || []).map(row => [row.tier, row.total]));
  const tierClass = ['', '', 'b', 'b', 'c', 'd', 'd', 'e'];
  return <div className="cc">
    <div className="cc-head">
      <div>
        <div className="eyebrow">CACSMS ONE / SPORTS AI</div>
        <h1>Sports Intelligence Command Centre</h1>
        <p>Monitor, analyse and manage football and basketball intelligence with stored records.</p>
      </div>
      <div className="cc-actions">
        <button className="sp-secondary" onClick={refresh}><RefreshCw size={15}/>Refresh</button>
        <button className="sp-secondary" onClick={() => window.location.assign(import.meta.env.VITE_CACSMS_HUB_URL || '/hub')}><ArrowLeft size={15}/>Central Hub</button>
        <button className="sp-primary" onClick={run} disabled={loading}><Play size={15}/>{loading ? 'Running…' : 'Run Analysis'}</button>
      </div>
    </div>
    <div className="cc-kpis">
      <button className="cc-kpi blue" onClick={() => nav('football-fixtures')}><i><CalendarDays size={20}/></i><div><span>Upcoming Fixtures</span><strong>{fmt(upcoming)}</strong><small>Scheduled</small></div></button>
      <button className="cc-kpi purple" onClick={() => nav('football-predictions')}><i><BrainCircuit size={20}/></i><div><span>Predictions</span><strong>{fmt(data?.predictions)}</strong><small>Stored rows</small></div></button>
      <button className="cc-kpi green" onClick={() => nav('value')}><i><Target size={20}/></i><div><span>Qualified Value</span><strong>{fmt(data?.qualified_opportunities)}</strong><small>{data?.qualified_opportunities ? 'Qualified' : 'Needs active model'}</small></div></button>
      <button className="cc-kpi orange" onClick={() => nav('tickets')}><i><Ticket size={20}/></i><div><span>Accumulator Tickets</span><strong>{fmt(data?.accumulator_tickets)}</strong><small>{data?.accumulator_tickets ? 'Stored' : 'None qualified'}</small></div></button>
      <button className="cc-kpi cyan" onClick={() => nav('odds')}><i><Database size={20}/></i><div><span>Odds Snapshots</span><strong>{fmt(data?.odds_snapshots)}</strong><small>{data?.odds_snapshots ? 'Stored quotes' : 'Awaiting Odds'}</small></div></button>
      <button className="cc-kpi red" onClick={() => nav('scheduler')}><i><AlertCircle size={20}/></i><div><span>Failed Jobs</span><strong>{fmt(data?.failed_jobs)}</strong><small>{data?.failed_jobs ? 'Needs attention' : 'Healthy'}</small></div></button>
    </div>
    <div className="cc-grid three">
      <section className="cc-panel">
        <header><strong><Layers3 size={18}/>Autonomous Pipeline</strong><Badge tone={fresh ? 'success' : 'warning'}>{fresh ? 'Running' : 'Idle'}</Badge></header>
        {pipeline.map(([name, time, tone]) => <div className="cc-row" key={name}><i className={`cc-dot ${name === 'Odds synchronization' && !data?.odds_snapshots ? 'wait' : tone === 'wait' ? 'wait' : 'ok'}`}/><span>{name}</span><Badge tone={name.startsWith('Odds') && !data?.odds_snapshots ? 'warning' : name.startsWith('Authorized') && !data?.browser?.authorized_urls ? 'warning' : tone === 'wait' ? 'warning' : 'success'}>{name.startsWith('Odds') && !data?.odds_snapshots ? 'Awaiting Odds' : name.startsWith('Authorized') && !data?.browser?.authorized_urls ? 'No authorized sites' : name.startsWith('Authorized') && data?.browser?.failed ? `${data.browser.failed} failed` : name.startsWith('Authorized') && data?.browser?.queued ? `${data.browser.queued} queued` : name.startsWith('Value') && !data?.qualified_opportunities ? 'Awaiting calibration' : name.startsWith('Accumulator') && !data?.accumulator_tickets ? 'Waiting' : ago(time)}</Badge></div>)}
      </section>
      <section className="cc-panel">
        <header><strong><Link2 size={18}/>System Status & Integrations</strong></header>
        {[
          ['Sports AI API', data ? 'ok' : 'bad', data ? 'Connected' : 'Unavailable'],
          ['SQLite database', 'ok', 'Connected'],
          ['football-data.org', ...providerLine('football-data')],
          ['OpenLigaDB', ...providerLine('openligadb')],
          ['OpenFootball dataset', ...providerLine('openfootball')],
          ['Basketball dataset', ...providerLine('sportsdataverse')],
          ['SportyBet integration', data?.browser?.sportybet_unattended?.operational ? 'ok' : 'wait', data?.browser?.sportybet_unattended?.operational ? 'Collecting displayed prices' : data?.browser?.sportybet_unattended?.authorized ? 'Waiting for prices' : 'Not collecting'],
          ['Authorized browser', data?.browser?.authorized_urls ? 'ok' : 'wait', data?.browser?.authorized_urls ? `${data.browser.inspected || 0} pages read` : 'No authorized sites'],
          ['Email / Telegram alerts', 'off', 'Not configured'],
        ].map(([name, tone, label]) => <div className="cc-row" key={name}><i className={`cc-dot ${tone}`}/><span>{name}</span><Badge tone={tone === 'ok' ? 'success' : tone === 'wait' ? 'warning' : 'neutral'}>{label}</Badge></div>)}
      </section>
      <section className="cc-panel">
        <header><strong><BarChart3 size={18}/>Today's Activity</strong><button className="cc-link" onClick={() => nav('activity')}>View Details</button></header>
        <div className="cc-activity">{activityRows.map(([name, count, color]) => <div key={name}><span>{name}</span><div className="cc-bar"><i style={{width: `${Math.max(count ? 4 : 0, (count / peak) * 100)}%`, background: color}}/></div><b>{fmt(count)}</b></div>)}</div>
      </section>
    </div>
    <div className="cc-grid bottom">
      <section className="cc-panel">
        <header><strong><Target size={18}/>Prediction Outcomes</strong></header>
        <div className="cc-outcomes">
          <div className="cc-donut" style={{background: `conic-gradient(#20b66c 0 ${wonPct}%, #ef4444 ${wonPct}% ${wonPct + lostPct}%, #cbd5e1 ${wonPct + lostPct}% 100%)`}}><div><b>{fmt(won + lost)}</b><small>Settled</small></div></div>
          <div className="cc-legend">
            <p><i className="cc-swatch" style={{background: '#20b66c'}}/>Won<b>{fmt(won)}</b></p>
            <p><i className="cc-swatch" style={{background: '#ef4444'}}/>Lost<b>{fmt(lost)}</b></p>
            <p><i className="cc-swatch" style={{background: '#94a3b8'}}/>Upcoming<b>{fmt(pending)}</b></p>
            <p className="cc-note">Settled counts use only predictions stored before kickoff. ROI is not shown because no bookmaker quote is stored.</p>
          </div>
        </div>
      </section>
      <section className="cc-panel">
        <header><strong><Target size={18}/>Predictions by sport</strong></header>
        <div className="cc-sports">{[...sports].sort((a, b) => a.name === 'football' ? -1 : 1).map(row => <div key={row.name}><b>{row.name[0].toUpperCase() + row.name.slice(1)}</b><div className="cc-bar"><i style={{width: `${(row.total / sportTotal) * 100}%`, background: row.name === 'football' ? '#2563eb' : '#7c3aed'}}/></div><strong>{((row.total / sportTotal) * 100).toFixed(1)}%</strong></div>)}</div>
        <p className="cc-note">Share of stored prediction rows. Qualified opportunities are {fmt(data?.qualified_opportunities)}.</p>
      </section>
      <section className="cc-panel">
        <header><strong><Flame size={18}/>Upcoming analysed fixtures</strong><button className="cc-link" onClick={() => nav('football-fixtures')}>View All</button></header>
        <div className="cc-table-scroll"><table className="cc-table"><thead><tr><th>Fixture</th><th>Sport</th><th>League</th><th>Kickoff</th><th>Pick</th><th>Prob.</th><th>SportyBet</th></tr></thead><tbody>
          {(data?.highlights || []).map(row => {
            const pick = pickTeam(row);
            return <tr key={row.fixture_id} className="cc-fixture" tabIndex={0} onClick={() => openFixture(row)} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openFixture(row); } }}><td className="cc-name"><span className={pick?.side === 'home' ? 'cc-fav' : ''}>{row.home}</span> vs <span className={pick?.side === 'away' ? 'cc-fav' : ''}>{row.away}</span></td><td>{row.sport}</td><td>{row.competition || '—'}</td><td>{when(row.starts_at)}</td><td>{pick ? <span className="cc-fav-pill">{pick.label}</span> : '—'}</td><td><span className="cc-prob">{(Number(row.probability) * 100).toFixed(1)}%</span></td><td>{row.decimal_odds == null ? 'Awaiting Odds' : Number(row.decimal_odds).toFixed(2)}</td></tr>;
          })}
          {!(data?.highlights || []).length && <tr><td colSpan="7">No upcoming fixture has a stored prediction yet.</td></tr>}
        </tbody></table></div>
      </section>
    </div>
    <div className="cc-grid last">
      <section className="cc-panel">
        <header><strong><Layers3 size={18}/>Accumulator Targets & Availability</strong></header>
        <div className="cc-tiers">{tiers.map((tier, index) => <button key={tier} className={`cc-tier ${tierClass[index]}`} onClick={() => nav('tiers')}><strong>{fmt(tier)}×</strong><small>{fmt(tierCount[tier] || 0)} tickets</small></button>)}</div>
      </section>
      <section className="cc-panel">
        <header><strong><Clock3 size={18}/>Next Scheduled Operations</strong><button className="cc-link" onClick={() => nav('scheduler')}>View Scheduler</button></header>
        {(data?.schedules || []).filter(item => item.enabled).map(item => <div className="cc-row" key={item.name}><Clock3 size={14}/><span>{item.name}</span><b>{ahead(item.next_run_at)}</b></div>)}
        {!(data?.schedules || []).some(item => item.enabled) && <p className="cc-note">No schedule is enabled.</p>}
      </section>
    </div>
    <p className="cc-foot">Figures come from the Sports AI database. Missing odds stay at zero snapshots, and no return is invented.</p>
  </div>;
}

export function PredictionBoard({sport}) {
  const [payload, setPayload] = React.useState({items: [], total: 0, page: 1, page_size: 25});
  const [page, setPage] = React.useState(1);
  const [pageSize, setPageSize] = React.useState(25);
  const [selected, setSelected] = React.useState(null);
  const [markets, setMarkets] = React.useState([]);
  const [error, setError] = React.useState('');
  React.useEffect(() => { setPage(1); }, [sport, pageSize]);
  React.useEffect(() => {
    let cancel = false;
    const params = new URLSearchParams({group: 'fixture', page: String(page), page_size: String(pageSize)});
    if (sport) params.set('sport', sport);
    get('/predictions?' + params.toString()).then(value => { if (!cancel) setPayload(value); }).catch(reason => { if (!cancel) setError(reason.message); });
    return () => { cancel = true; };
  }, [sport, page, pageSize]);
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
    <div className="sp-table-scroll sp-table-limit"><table className="sp-table"><thead><tr><th>Fixture</th><th>Competition</th><th>Kickoff</th><th>Analysis</th><th>Top market</th><th>Model P</th><th>Odds</th><th>Edge</th><th>Model</th><th></th></tr></thead><tbody>
      {items.map(row => <tr key={row.fixture_id}><td>{row.home} vs {row.away}</td><td>{row.competition}</td><td>{when(row.starts_at)}</td><td>{row.analysis_status || 'stored prediction'}</td><td>{row.market}</td><td>{`${(Number(row.probability) * 100).toFixed(1)}%`}</td><td>{row.odds == null ? 'Awaiting Odds' : Number(row.odds).toFixed(2)}</td><td>{edgeText(row.edge)}</td><td>{row.model_name} v{row.model_version}{row.model_active ? '' : ' · inactive'}</td><td><button className="sp-text-action" onClick={() => open(row)}>View</button></td></tr>)}
      {!items.length && <tr><td className="sp-empty" colSpan="10">No upcoming fixture has a stored prediction yet. The scheduler analyses a small batch at a time.</td></tr>}
    </tbody></table></div>
    <div className="sp-pager"><span>{total ? (page - 1) * pageSize + 1 : 0}–{Math.min(total, page * pageSize)} of {fmt(total)}</span><button className="sp-secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button><span>Page {page} of {pages}</span><button className="sp-secondary" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button></div>
    {selected && <div className="sp-intel-overlay" onMouseDown={event => { if (event.target === event.currentTarget) setSelected(null); }}><section className="sp-panel sp-intel-dialog" role="dialog" aria-modal="true"><div className="sp-panel-head"><div><h2>{selected.home} vs {selected.away}</h2><p>{selected.competition} · {when(selected.starts_at)} · {selected.market_count} markets in the latest run</p></div><button className="sp-secondary" onClick={() => setSelected(null)}>Close</button></div><div className="sp-table-scroll sp-table-limit"><table className="sp-table"><thead><tr><th>Market</th><th>Probability</th><th>Model</th></tr></thead><tbody>{markets.slice(0, 40).map(row => <tr key={row.id}><td>{row.market}</td><td>{`${(Number(row.probability) * 100).toFixed(1)}%`}</td><td>{row.model_name} v{row.model_version}</td></tr>)}</tbody></table></div></section></div>}
  </div>;
}
