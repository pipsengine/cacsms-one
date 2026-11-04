import {useEffect, useState} from 'react';
import {get, post} from './api';

const WARNING = 'This is an illustrative simulation. A 30-day rollover cannot guarantee 30 consecutive wins or a zero-loss result. One loss can end a full-rollover campaign. A day with no qualified selection is skipped. No real-money wager is placed.';

function money(value) {
  return value == null ? '—' : `₦${Number(value).toLocaleString('en-NG', {maximumFractionDigits: 2})}`;
}

function percent(value) {
  return value == null || value === '' ? '—' : `${(Number(value) * 100).toFixed(1)}%`;
}

function when(value) {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

export default function DailyRolloverCentre({tab = 0}) {
  const [campaigns, setCampaigns] = useState([]);
  const [selected, setSelected] = useState('');
  const [warning, setWarning] = useState(WARNING);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    get('/rollover/campaigns').then(value => {
      setCampaigns(value.items || []);
      setWarning(value.warning || WARNING);
      setSelected(current => current || value.items?.[0]?.id || '');
    }).catch(() => setCampaigns([]));
  }, [revision]);
  const current = campaigns.find(item => item.id === selected) || campaigns[0];
  return <div className="sp-ops-stack">
    <p className="sp-ops-error" role="note">{warning}</p>
    {tab === 0 && <Overview campaigns={campaigns} onCreated={id => { setSelected(id); setRevision(value => value + 1); }} />}
    {tab === 1 && <Qualification campaignId={current?.id} />}
    {tab === 2 && <Risk campaign={current} />}
    {tab === 3 && <Simulation campaignId={current?.id} onRan={() => setRevision(value => value + 1)} />}
    {tab === 4 && <History campaigns={campaigns} />}
  </div>;
}

function Overview({campaigns, onCreated}) {
  const [form, setForm] = useState({
    name: '30-day simulation', mode: 'CAPITAL_CONTROLLED_SIMULATION', length_days: 30,
    opening_balance: 1000, odds_target: 1.5, odds_low: 1.4, odds_high: 1.6,
    stake_mode: 'fixed', fixed_stake: 100, stake_fraction: 0.05, stop_loss_fraction: 0.2,
  });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  function update(key, value) { setForm(current => ({...current, [key]: value})); }
  async function create(event) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const created = await post('/rollover/campaigns', {
        ...form,
        length_days: Number(form.length_days),
        opening_balance: Number(form.opening_balance),
        odds_target: Number(form.odds_target),
        odds_low: Number(form.odds_low),
        odds_high: Number(form.odds_high),
        fixed_stake: Number(form.fixed_stake),
        stake_fraction: Number(form.stake_fraction),
        stop_loss_fraction: Number(form.stop_loss_fraction),
      });
      onCreated(created.id);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }
  return <>
    <p className="sp-muted">Campaigns run for a configurable number of calendar days in West Africa Time, starting at an illustrative ₦1,000. The daily price target is 1.50, inside 1.40–1.60 unless those bounds are changed. At most one qualified single is chosen each day.</p>
    {error && <p className="sp-ops-error" role="alert">{error}</p>}
    <div className="sp-kpi-grid">
      <article className="sp-kpi"><span>Campaigns</span><strong>{campaigns.length}</strong></article>
      <article className="sp-kpi"><span>Active</span><strong>{campaigns.filter(item => item.status === 'active').length}</strong></article>
      <article className="sp-kpi"><span>Places a wager</span><strong>No</strong></article>
    </div>
    <section className="sp-panel"><h3>Start a simulation</h3>
      <form className="sp-filter-bar" onSubmit={create}>
        <label>Name<input value={form.name} onChange={event => update('name', event.target.value)} /></label>
        <label>Mode<select value={form.mode} onChange={event => update('mode', event.target.value)}><option value="CAPITAL_CONTROLLED_SIMULATION">Capital controlled</option><option value="FULL_ROLLOVER_SIMULATION">Full rollover</option></select></label>
        <label>Days<input type="number" min="1" max="366" value={form.length_days} onChange={event => update('length_days', event.target.value)} /></label>
        <label>Opening ₦<input type="number" min="1" value={form.opening_balance} onChange={event => update('opening_balance', event.target.value)} /></label>
        <label>Odds low<input type="number" step="0.01" value={form.odds_low} onChange={event => update('odds_low', event.target.value)} /></label>
        <label>Target<input type="number" step="0.01" value={form.odds_target} onChange={event => update('odds_target', event.target.value)} /></label>
        <label>Odds high<input type="number" step="0.01" value={form.odds_high} onChange={event => update('odds_high', event.target.value)} /></label>
        <button className="sp-primary" type="submit" disabled={busy}>{busy ? 'Saving…' : 'Create campaign'}</button>
      </form>
    </section>
    <CampaignTable campaigns={campaigns} />
  </>;
}

function Qualification({campaignId}) {
  const [payload, setPayload] = useState({eligible: [], excluded: []});
  const [error, setError] = useState('');
  useEffect(() => {
    if (!campaignId) return;
    get(`/rollover/campaigns/${campaignId}/candidates`).then(setPayload).catch(requestError => setError(requestError.message));
  }, [campaignId]);
  if (!campaignId) return <p className="sp-muted">Create a campaign to see today’s qualified singles.</p>;
  return <>
    <p className="sp-muted">Candidates are qualified by the central service, priced inside this campaign’s odds range, and limited to one selection for the West Africa Time date. Other sports and markets stay out until they have a calibrated model.</p>
    {error && <p className="sp-ops-error" role="alert">{error}</p>}
    <section className="sp-panel"><div className="sp-table-scroll"><table className="sp-table"><thead><tr>{['Fixture', 'Market', 'Odds', 'Probability', 'Edge', 'Data quality', 'Uncertainty'].map(heading => <th key={heading}>{heading}</th>)}</tr></thead><tbody>
      {(payload.eligible || []).map(row => <tr key={`${row.fixture_id}-${row.market}`}><td>{row.home} vs {row.away}</td><td>{row.market}</td><td>{Number(row.decimal_odds).toFixed(2)}</td><td>{percent(row.probability)}</td><td>{percent(row.edge)}</td><td>{row.completeness == null ? '—' : percent(row.completeness)}</td><td>{row.uncertainty == null ? '—' : Number(row.uncertainty).toFixed(3)}</td></tr>)}
      {!payload.eligible?.length && <tr><td className="sp-empty" colSpan="7">No qualified single is inside the odds range. The day can be recorded as SKIPPED_DAY.</td></tr>}
    </tbody></table></div></section>
    {!!payload.excluded?.length && <p className="sp-muted">{payload.excluded.length} stored decisions are outside the odds range or have no fresh price.</p>}
  </>;
}

function Risk({campaign}) {
  if (!campaign) return <p className="sp-muted">Create a campaign to view its stake rules.</p>;
  return <>
    <p className="sp-muted">Capital control uses a fixed or fractional stake, an exposure cap, protected profit and a stop-loss. A losing day never raises the next stake. Full rollover reinvests the simulated return only after a win.</p>
    <div className="sp-kpi-grid">
      <article className="sp-kpi"><span>Mode</span><strong>{campaign.mode === 'FULL_ROLLOVER_SIMULATION' ? 'Full rollover' : 'Capital controlled'}</strong></article>
      <article className="sp-kpi"><span>Bankroll</span><strong>{money(campaign.bankroll)}</strong></article>
      <article className="sp-kpi"><span>Protected</span><strong>{money(campaign.protected_capital)}</strong></article>
      <article className="sp-kpi"><span>Max drawdown</span><strong>{percent(campaign.max_drawdown)}</strong></article>
      <article className="sp-kpi"><span>Stop-loss</span><strong>{percent(campaign.stop_loss_fraction)}</strong></article>
      <article className="sp-kpi"><span>Status</span><strong>{campaign.status}</strong></article>
    </div>
    <section className="sp-panel"><p>Stake mode {campaign.stake_mode}. Fixed stake {money(campaign.fixed_stake)}. Fraction {percent(campaign.stake_fraction)}. Exposure cap {percent(campaign.max_stake_fraction)}. Profit lock at {Number(campaign.profit_lock_multiple).toFixed(2)}×, protecting {percent(campaign.protected_profit_fraction)} of profit above the opening balance. Odds target {Number(campaign.odds_target).toFixed(2)} inside {Number(campaign.odds_low).toFixed(2)}–{Number(campaign.odds_high).toFixed(2)}.</p></section>
  </>;
}

function Simulation({campaignId, onRan}) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  function load() {
    if (!campaignId) return Promise.resolve();
    return get(`/rollover/campaigns/${campaignId}`).then(setDetail);
  }
  useEffect(() => { load().catch(requestError => setError(requestError.message)); }, [campaignId]);
  async function run() {
    setBusy(true);
    setError('');
    try {
      await post(`/rollover/campaigns/${campaignId}/run`, {});
      await load();
      onRan();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusy(false);
    }
  }
  if (!campaignId) return <p className="sp-muted">Create a campaign before running a simulated day.</p>;
  return <>
    <p className="sp-muted">The path probability multiplies stored win probabilities for selections already made. It is not a forecast that the remaining days will be won.</p>
    {error && <p className="sp-ops-error" role="alert">{error}</p>}
    <div className="sp-filter-bar">
      <button className="sp-primary" type="button" onClick={run} disabled={busy || detail?.status !== 'active'}>{busy ? 'Running…' : 'Select today’s simulation'}</button>
      <span>Path probability {percent(detail?.path_probability)}</span>
    </div>
    <section className="sp-panel"><div className="sp-table-scroll"><table className="sp-table"><thead><tr>{['Date', 'Status', 'Selection', 'Odds', 'Stake', 'Bankroll after', 'Reason'].map(heading => <th key={heading}>{heading}</th>)}</tr></thead><tbody>
      {(detail?.days || []).map(day => <tr key={day.id}><td>{day.campaign_date}</td><td>{day.status}</td><td>{day.home ? `${day.home} vs ${day.away} · ${day.market}` : '—'}</td><td>{day.decimal_odds == null ? '—' : Number(day.decimal_odds).toFixed(2)}</td><td>{money(day.stake)}</td><td>{money(day.bankroll_after)}</td><td>{day.reason}</td></tr>)}
      {!detail?.days?.length && <tr><td className="sp-empty" colSpan="7">No simulated day is stored yet.</td></tr>}
    </tbody></table></div></section>
  </>;
}

function History({campaigns}) {
  return <>
    <p className="sp-muted">Each campaign keeps its own bankroll, wins, losses, skipped days, consecutive wins and drawdown. Strategies are compared from stored results only.</p>
    <CampaignTable campaigns={campaigns} />
  </>;
}

function CampaignTable({campaigns}) {
  return <section className="sp-panel"><div className="sp-table-scroll"><table className="sp-table"><thead><tr>{['Campaign', 'Mode', 'Status', 'Equity', 'Wins', 'Losses', 'Skipped', 'Best streak', 'Drawdown', 'Path probability'].map(heading => <th key={heading}>{heading}</th>)}</tr></thead><tbody>
    {campaigns.map(row => <tr key={row.id}><td>{row.name}<br/><small>{when(row.created_at)}</small></td><td>{row.mode === 'FULL_ROLLOVER_SIMULATION' ? 'Full rollover' : 'Capital controlled'}</td><td>{row.status}</td><td>{money(row.equity)}</td><td>{row.wins}</td><td>{row.losses}</td><td>{row.skipped}</td><td>{row.max_consecutive_wins}</td><td>{percent(row.max_drawdown)}</td><td>{percent(row.path_probability)}</td></tr>)}
    {!campaigns.length && <tr><td className="sp-empty" colSpan="10">No rollover campaign is stored.</td></tr>}
  </tbody></table></div></section>;
}
