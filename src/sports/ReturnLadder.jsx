import React, {useState} from 'react';
import {Check, Copy, Dribbble, Play, RefreshCw, ShieldCheck, Ticket} from 'lucide-react';
import {post} from './api';
import useLiveResource from './useLiveResource';
import './return-ladder.css';

const money = value => value == null ? '—' : `₦${Number(value).toLocaleString('en-NG', {maximumFractionDigits: 0})}`;
const odds = value => value == null ? '—' : Number(value).toLocaleString('en-GB', {maximumFractionDigits: 2});
const stamp = value => value ? new Date(value).toLocaleString('en-GB', {day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'Africa/Lagos'}) : '—';
const chance = value => value ? `1 in ${Math.max(1, Math.round(1 / Number(value))).toLocaleString('en-GB')}` : '—';

function statusOf(ticket, bookingId) {
  if (ticket.code) return 'Code ready';
  if (bookingId === ticket.ticket_id || ticket.status === 'PREPARING' || ticket.status === 'QUEUED') return 'Booking';
  if (ticket.status === 'FAILED') return 'Not booked';
  return 'Ready to book';
}

export default function ReturnLadder() {
  const {data, error, loading, refresh} = useLiveResource('/return-ladder', 8000);
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState('');
  const [open, setOpen] = useState(null);
  const tickets = data?.tickets || [];
  const selected = tickets.find(ticket => ticket.ticket_id === open) || null;

  async function generate() {
    setBusy('generate');
    setNotice('');
    try {
      const result = await post('/return-ladder/generate', {});
      const source = result.source === 'live' ? 'live SportyBet prices' : 'stored SportyBet prices';
      setNotice(result.created
        ? `Built ${result.created} basketball slips from ${result.pool} favourites (${source}). ${result.notice || ''} Booking has not started.`
        : (result.notice || 'The board did not have enough upcoming basketball favourites to build a slip.'));
      refresh();
    } catch (requestError) {
      setNotice(requestError.message);
    } finally {
      setBusy('');
    }
  }

  async function book() {
    setBusy('book');
    setNotice('');
    try {
      const result = await post('/return-ladder/book', {});
      setNotice(result.status === 'booking'
        ? 'SportyBet is opening. Each remaining slip is booked in turn, including matches on later days. No stake is placed.'
        : result.status === 'busy'
          ? 'A code is already being requested. The next slip follows when this one has a code.'
          : 'There is no slip waiting for a code.');
      refresh();
    } catch (requestError) {
      setNotice(requestError.message);
    } finally {
      setBusy('');
    }
  }

  async function copy(code) {
    try {
      await navigator.clipboard.writeText(code);
      setNotice(`Copied ${code}`);
    } catch {
      setNotice('Clipboard is unavailable. Select the code to copy it.');
    }
  }

  return <section className="rl-board" aria-label="Basketball return codes">
    <header className="rl-head">
      <div>
        <p className="rl-kicker"><Dribbble size={15}/> Basketball · SportyBet Nigeria · ₦100 stake</p>
        <h1>Return codes</h1>
        <p>Shortest winner prices, one pick per match. A slip can mix today with later days, and it stays here until its first match starts. The ₦200,000,000 row is three different codes, and that figure is SportyBet’s sports cap.</p>
      </div>
      <div className="rl-actions">
        <button type="button" className="rl-button" onClick={refresh}><RefreshCw size={15}/>Refresh</button>
        <button type="button" className="rl-button rl-primary" disabled={!!busy} onClick={generate}><Play size={15}/>{busy === 'generate' ? 'Reading the board…' : 'Build ladder'}</button>
        <button type="button" className="rl-button" disabled={!!busy || !tickets.some(ticket => !ticket.code)} onClick={book}><Ticket size={15}/>{busy === 'book' ? 'Opening SportyBet…' : 'Book codes'}</button>
      </div>
    </header>
    <p className="rl-note"><ShieldCheck size={16}/>A booking code does not place a stake. A fair price hits about once per the combined odds; the margin makes the true chance smaller. 18+. Play responsibly.</p>
    {(error || notice) && <p className="rl-banner" role="status">{error || notice}</p>}
    <div className="rl-meta">
      <span>{data?.source === 'live' ? 'Live SportyBet prices' : data?.source === 'stored' ? 'Stored SportyBet prices' : 'No board read yet'}</span>
      <span>{data?.pool == null ? '—' : `${data.pool} favourites`}</span>
      <span>Updated {stamp(data?.generated_at)} WAT</span>
      <span>{loading ? 'Refreshing…' : `${tickets.length} slips`}</span>
    </div>
    <div className="rl-table-wrap">
      <table>
        <thead>
          <tr>
            <th>Target</th>
            <th>Code</th>
            <th>Legs</th>
            <th>Odds</th>
            <th>Estimated payout</th>
            <th>Fair chance</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {tickets.map(ticket => <tr key={ticket.ticket_id} className={open === ticket.ticket_id ? 'is-open' : ''} onClick={() => setOpen(open === ticket.ticket_id ? null : ticket.ticket_id)}>
            <td><b>{ticket.label}</b>{ticket.span && <small>{ticket.span}</small>}</td>
            <td>{ticket.code
              ? <button type="button" className="rl-code" onClick={event => { event.stopPropagation(); copy(ticket.code); }}>{ticket.code}<Copy size={12}/></button>
              : <span className="rl-wait">Waiting</span>}
            </td>
            <td>{ticket.legs}</td>
            <td>{odds(ticket.combined_odds)}</td>
            <td>{money(ticket.displayed_payout || ticket.estimated_payout)}</td>
            <td>{chance(ticket.fair_chance)}</td>
            <td><span className={ticket.code ? 'rl-state is-ready' : 'rl-state'}>{ticket.code && <Check size={12}/>}{statusOf(ticket, data?.booking_ticket)}</span></td>
          </tr>)}
        </tbody>
      </table>
      {!tickets.length && <p className="rl-empty">{loading ? 'Loading the ladder…' : 'No ladder yet. Build ladder reads upcoming basketball, including later days, and stores the ten slips.'}</p>}
    </div>
    {selected && <article className="rl-detail">
      <header>
        <h2>{selected.label}</h2>
        <p>{selected.legs} selections · {odds(selected.combined_odds)} · {selected.code || 'No code yet'}{selected.job_error ? ` · ${selected.job_error}` : ''}</p>
      </header>
      <ol>
        {selected.selections.map((leg, index) => <li key={`${leg.home}-${index}`}>
          <span>{index + 1}</span>
          <div><b>{leg.selection}</b><small>{leg.home} vs {leg.away} · {leg.competition} · {stamp(leg.starts_at)}</small></div>
          <strong>{Number(leg.decimal_odds).toFixed(2)}</strong>
        </li>)}
      </ol>
    </article>}
  </section>;
}
