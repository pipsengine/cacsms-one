import React from 'react';
import {get, post} from './api';
import './sportybet.css';

const emptyForm = {
  sport: 'football', competition: '', home: '', away: '', starts_at: '',
  market_label: '1X2', selection_label: 'Home', decimal_odds: '',
};

function tone(state) {
  if (state === 'AWAITING_CONFIRMATION' || state === 'INSPECTING' || state === 'NAVIGATING' || state === 'STARTING') return 'wait';
  if (state === 'BLOCKED' || state === 'FAILED') return 'bad';
  if (state === 'COMPLETED') return 'on';
  return '';
}

export default function SportyBetOperator({tab = 0}) {
  const [status, setStatus] = React.useState(null);
  const [health, setHealth] = React.useState(null);
  const [rows, setRows] = React.useState({items: [], total: 0, page: 1});
  const [events, setEvents] = React.useState({items: [], total: 0, page: 1});
  const [page, setPage] = React.useState(1);
  const [sport, setSport] = React.useState('');
  const [confirmation, setConfirmation] = React.useState('');
  const [selected, setSelected] = React.useState(null);
  const [detail, setDetail] = React.useState(null);
  const [form, setForm] = React.useState(emptyForm);
  const [message, setMessage] = React.useState('');
  const [busy, setBusy] = React.useState(false);

  const refreshStatus = React.useCallback(() => {
    get('/sportybet/browser/status').then(setStatus).catch(error => setMessage(error.message));
    get('/sportybet/browser/health').then(setHealth).catch(() => {});
  }, []);
  React.useEffect(() => { refreshStatus(); }, [refreshStatus]);
  React.useEffect(() => {
    if (tab !== 0) return undefined;
    const timer = setInterval(refreshStatus, 4000);
    return () => clearInterval(timer);
  }, [tab, refreshStatus]);
  React.useEffect(() => {
    const params = new URLSearchParams({page: String(page), page_size: '8'});
    if (sport) params.set('sport', sport);
    if (confirmation) params.set('confirmation', confirmation);
    get('/sportybet/observations?' + params.toString()).then(setRows).catch(error => setMessage(error.message));
  }, [page, sport, confirmation, status?.observation_count, selected]);
  React.useEffect(() => {
    if (tab !== 4) return;
    get('/sportybet/audit?page=1&page_size=8').then(setEvents).catch(error => setMessage(error.message));
  }, [tab, status?.state]);

  async function control(path, body) {
    setBusy(true);
    setMessage('');
    try { setStatus(await post(path, body || {})); }
    catch (error) { setMessage(error.message); }
    finally { setBusy(false); }
  }
  async function open(row) {
    setSelected(row);
    setDetail(await get(`/sportybet/observations/${encodeURIComponent(row.id)}`));
  }
  async function act(path, body) {
    setMessage('');
    try {
      await post(path, body || {});
      const next = await get(`/sportybet/observations/${encodeURIComponent(selected.id)}`);
      setDetail(next);
      setSelected(next.observation);
    } catch (error) { setMessage(error.message); }
  }
  async function record(event) {
    event.preventDefault();
    setMessage('');
    try {
      const payload = {...form, decimal_odds: Number(form.decimal_odds)};
      if (form.starts_at) payload.starts_at = new Date(form.starts_at).toISOString();
      else delete payload.starts_at;
      await post('/sportybet/observations', payload);
      setForm(emptyForm);
      refreshStatus();
    } catch (error) { setMessage(error.message); }
  }

  const state = status?.state || 'IDLE';
  const pages = Math.max(1, Math.ceil((rows.total || 0) / 8));
  const shot = (events.items || []).find(item => item.has_screenshot);
  return <div className="sbo">
    <div className="sbo-head">
      <div>
        <div className="sbo-kicker">VALUE INTELLIGENCE</div>
        <h2>SportyBet Browser Operator</h2>
        <p>Supervised Chromium research. A person confirms every visible quote. Unattended collection is not operational, and no wager is placed.</p>
      </div>
      <div className="sbo-actions">
        <button className="sp-primary" disabled={busy} onClick={() => control('/sportybet/browser/start', {section: 'football'})}>Start football</button>
        <button className="sp-secondary" disabled={busy} onClick={() => control('/sportybet/browser/start', {section: 'basketball'})}>Start basketball</button>
        <button className="sp-secondary" disabled={busy} onClick={() => control('/sportybet/browser/pause')}>Pause</button>
        <button className="sp-secondary" disabled={busy} onClick={() => control('/sportybet/browser/stop')}>Stop</button>
        <button className="sp-secondary" disabled={busy} onClick={() => control('/sportybet/browser/restart', {section: 'home'})}>Restart</button>
      </div>
    </div>
    {message && <p className="sp-ops-error">{message}</p>}
    {tab === 0 && <div className="sbo-grid">
      <section className="sbo-card">
        <header><h3>Browser session</h3><span className="sbo-state"><i className={'sbo-dot ' + tone(state)}/>{state}{status?.paused ? ' · paused' : ''}</span></header>
        <div className="sbo-meta">
          <span>Mode</span><b>Supervised</b>
          <span>Last operation</span><b>{status?.last_operation || '—'}</b>
          <span>Source page</span><b>{status?.source_url || '—'}</b>
          <span>Observations</span><b>{status?.observation_count || 0}</b>
          <span>Heartbeat</span><b>{status?.heartbeat_at || '—'}</b>
          <span>Browser</span><b>{health?.chromium ? 'Chromium available' : (health?.detail || 'Checking')}</b>
          <span>Unattended</span><b>{status?.unattended?.operational ? 'Operational' : 'Not operational'}</b>
        </div>
        {status?.error && <p className="sbo-note">{status.error}</p>}
        <p className="sbo-note">{status?.unattended?.reason}</p>
      </section>
      <section className="sbo-card">
        <header><h3>Latest audit image</h3></header>
        {shot ? <img className="sbo-shot" alt="Browser audit screenshot" src={`/api/sports-ai/sportybet/screenshots/${shot.id}`}/> : <p className="sbo-note">A screenshot is stored after the browser opens a page.</p>}
      </section>
    </div>}
    {tab === 1 && <section className="sbo-card">
      <header><h3>Record a visible quote</h3></header>
      <p className="sbo-note">Use this only for a price you can see in the open SportyBet page. It stays unconfirmed until you approve it.</p>
      <form className="sbo-form" onSubmit={record}>
        <select value={form.sport} onChange={event => setForm({...form, sport: event.target.value})}><option value="football">Football</option><option value="basketball">Basketball</option></select>
        <input placeholder="Competition" value={form.competition} onChange={event => setForm({...form, competition: event.target.value})}/>
        <input placeholder="Home" required value={form.home} onChange={event => setForm({...form, home: event.target.value})}/>
        <input placeholder="Away" required value={form.away} onChange={event => setForm({...form, away: event.target.value})}/>
        <input type="datetime-local" value={form.starts_at} onChange={event => setForm({...form, starts_at: event.target.value})}/>
        <input placeholder="Market label" required value={form.market_label} onChange={event => setForm({...form, market_label: event.target.value})}/>
        <input placeholder="Selection" required value={form.selection_label} onChange={event => setForm({...form, selection_label: event.target.value})}/>
        <input placeholder="Decimal odds" required value={form.decimal_odds} onChange={event => setForm({...form, decimal_odds: event.target.value})}/>
        <button className="sp-primary" type="submit">Save for confirmation</button>
      </form>
    </section>}
    {(tab === 1 || tab === 2 || tab === 3) && <section className="sbo-card">
      <header><h3>{tab === 3 ? 'Matching and validation' : 'Odds observations'}</h3></header>
      <div className="sbo-filters">
        <select value={sport} onChange={event => { setSport(event.target.value); setPage(1); }}><option value="">All sports</option><option value="football">Football</option><option value="basketball">Basketball</option></select>
        <select value={confirmation} onChange={event => { setConfirmation(event.target.value); setPage(1); }}><option value="">Any confirmation</option><option value="proposed">Proposed</option><option value="confirmed">Confirmed</option><option value="rejected">Rejected</option></select>
      </div>
      <div className="sbo-table-wrap"><table className="sbo-table"><thead><tr><th>Fixture</th><th>Market</th><th>Odds</th><th>Confirmation</th><th>Match</th><th>Value</th></tr></thead><tbody>
        {(rows.items || []).map(row => <tr key={row.id}><td><button onClick={() => open(row)}>{row.home} vs {row.away}</button></td><td>{row.market_label} · {row.selection_label}</td><td>{row.decimal_odds ?? '—'}</td><td>{row.confirmation_status}</td><td>{row.match_status}{row.match_confidence != null ? ` ${(row.match_confidence * 100).toFixed(0)}%` : ''}</td><td>{row.value_status || '—'}</td></tr>)}
        {!(rows.items || []).length && <tr><td colSpan="6">No SportyBet observations are stored.</td></tr>}
      </tbody></table></div>
      <div className="sbo-pager"><span>{rows.total || 0} observations</span><button className="sp-secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button><span>Page {page} of {pages}</span><button className="sp-secondary" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button></div>
    </section>}
    {tab === 4 && <section className="sbo-card">
      <header><h3>Activity and audit</h3></header>
      <div className="sbo-table-wrap"><table className="sbo-table"><thead><tr><th>Time</th><th>State</th><th>Operation</th><th>Detail</th></tr></thead><tbody>
        {(events.items || []).map(row => <tr key={row.id}><td>{row.created_at}</td><td>{row.state}</td><td>{row.operation}</td><td>{row.detail}</td></tr>)}
        {!(events.items || []).length && <tr><td colSpan="4">No browser activity has been recorded.</td></tr>}
      </tbody></table></div>
    </section>}
    {selected && detail && <div className="sbo-drawer" onMouseDown={event => { if (event.target === event.currentTarget) setSelected(null); }}><section className="sbo-card sbo-dialog" role="dialog" aria-modal="true">
      <header><h3>{detail.observation.home} vs {detail.observation.away}</h3><button className="sp-secondary" onClick={() => setSelected(null)}>Close</button></header>
      <p className="sbo-note">{detail.observation.market_label} / {detail.observation.selection_label} · {detail.observation.decimal_odds ?? 'no price'} · {detail.observation.source_url}</p>
      <p className="sbo-note">Market mapping: {detail.market?.supported ? detail.market.canonical_market : (detail.market?.reason || 'Match a sport before mapping the market.')}</p>
      <div className="sbo-actions">
        <button className="sp-primary" onClick={() => act(`/sportybet/observations/${selected.id}/confirm`)}>Confirm quote</button>
        <button className="sp-secondary" onClick={() => act(`/sportybet/observations/${selected.id}/market`)}>Validate market</button>
        <button className="sp-secondary" onClick={() => act(`/sportybet/observations/${selected.id}/import`)}>Import approved odds</button>
      </div>
      <div className="sbo-table-wrap"><table className="sbo-table"><thead><tr><th>Stored fixture</th><th>Confidence</th><th></th></tr></thead><tbody>
        {(detail.candidates?.candidates || []).map(item => <tr key={item.fixture_id}><td>{item.home} vs {item.away}</td><td>{(item.confidence * 100).toFixed(0)}%{item.swapped ? ' reversed' : ''}</td><td><button onClick={() => act(`/sportybet/observations/${selected.id}/match`, {fixture_id: item.fixture_id, acknowledge_ambiguity: Boolean(detail.candidates?.ambiguous)})}>Use this fixture</button></td></tr>)}
        {!(detail.candidates?.candidates || []).length && <tr><td colSpan="3">No stored fixture clears the name check.</td></tr>}
      </tbody></table></div>
    </section></div>}
  </div>;
}
