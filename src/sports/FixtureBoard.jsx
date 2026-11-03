import React, {useEffect, useState} from 'react';
import {get} from './api';

const fmt = value => Number(value || 0).toLocaleString('en-US');
const when = value => {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
};

export default function FixtureBoard({sport, tab, revision}) {
  const mode = tab === 1 ? 'results' : 'upcoming';
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [team, setTeam] = useState('');
  const [competition, setCompetition] = useState('');
  const [provider, setProvider] = useState('');
  const [query, setQuery] = useState({team: '', competition: '', provider: ''});
  const [payload, setPayload] = useState({items: [], total: 0, page: 1, page_size: 25});
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  useEffect(() => { setPage(1); }, [sport, mode, pageSize, query, revision]);
  useEffect(() => {
    if (tab > 1) return;
    let cancel = false;
    setLoading(true);
    const params = new URLSearchParams({window: mode, page: String(page), page_size: String(pageSize)});
    if (sport) params.set('sport', sport);
    if (query.team) params.set('team', query.team);
    if (query.competition) params.set('competition', query.competition);
    if (query.provider) params.set('provider', query.provider);
    get('/fixtures?' + params.toString()).then(value => { if (!cancel) setPayload(value); }).catch(reason => { if (!cancel) setError(reason.message); }).finally(() => { if (!cancel) setLoading(false); });
    return () => { cancel = true; };
  }, [sport, mode, page, pageSize, query, revision, tab]);
  if (tab === 2) return <p className="sp-muted">Team statistics appear on Match Analysis. The schedule feeds do not include a separate standings file for every competition. Bundesliga table rows are stored when OpenLigaDB returns them during analysis.</p>;
  if (tab === 3) return <p className="sp-muted">Collection runs from the scheduler and from Data Sources. This tab does not import a second copy of the feeds.</p>;
  const rows = payload.items || [];
  const total = payload.total || 0;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return <div>
    <form className="sp-filter-bar" onSubmit={event => { event.preventDefault(); setQuery({team, competition, provider}); }}>
      <label>Team<input value={team} onChange={event => setTeam(event.target.value)} placeholder="Search team"/></label>
      <label>Competition<input value={competition} onChange={event => setCompetition(event.target.value)} placeholder="Competition"/></label>
      <label>Provider<input value={provider} onChange={event => setProvider(event.target.value)} placeholder="openligadb"/></label>
      <label>Rows<select value={pageSize} onChange={event => setPageSize(Number(event.target.value))}><option value="25">25</option><option value="50">50</option><option value="100">100</option></select></label>
      <button className="sp-secondary" type="submit">Apply</button>
    </form>
    {error && <p className="sp-ops-error">{error}</p>}
    {loading && <p className="sp-muted">Loading stored fixtures…</p>}
    <div className="sp-table-scroll sp-table-limit"><table className="sp-table"><thead><tr><th>Fixture</th><th>Competition</th><th>Kickoff</th><th>Status</th><th>Provider</th><th></th></tr></thead><tbody>
      {rows.map(row => <tr key={row.id}><td>{row.home} vs {row.away}</td><td>{row.competition}</td><td>{when(row.kickoff_at)}</td><td><span className="sp-status">{row.status}</span></td><td>{row.provider_name || row.source}</td><td><button className="sp-text-action" onClick={() => setSelected(row)}>Details</button></td></tr>)}
      {!loading && !rows.length && <tr><td className="sp-empty" colSpan="6">Awaiting Real Data for this filter.</td></tr>}
    </tbody></table></div>
    <div className="sp-pager"><span>{total ? (page - 1) * pageSize + 1 : 0}–{Math.min(total, page * pageSize)} of {fmt(total)}</span><button className="sp-secondary" disabled={page <= 1 || loading} onClick={() => setPage(page - 1)}>Previous</button><span>Page {Math.min(page, pages)} of {pages}</span><button className="sp-secondary" disabled={page >= pages || loading} onClick={() => setPage(page + 1)}>Next</button></div>
    {selected && <div className="sp-intel-overlay" onMouseDown={event => { if (event.target === event.currentTarget) setSelected(null); }}><section className="sp-panel sp-intel-dialog" role="dialog" aria-modal="true"><div className="sp-panel-head"><div><h2>{selected.home} vs {selected.away}</h2><p>{selected.competition}</p></div><button className="sp-secondary" onClick={() => setSelected(null)}>Close</button></div><p>Kickoff {when(selected.kickoff_at)}. Status {selected.status}. Collected {when(selected.observed_at)}.</p>{selected.source_url ? <a href={selected.source_url} target="_blank" rel="noreferrer">Open source record</a> : <p>No source URL is stored for this fixture.</p>}</section></div>}
  </div>;
}
