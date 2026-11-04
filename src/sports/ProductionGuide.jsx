import {useEffect, useState} from 'react';
import {get} from './api';

export default function ProductionGuide({tab = 0}) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => {
    get('/autonomous/readiness').then(setReport).catch(requestError => setError(requestError.message));
  }, []);
  if (error) return <p className="sp-ops-error" role="alert">{error}</p>;
  if (!report) return <p className="sp-muted">Loading the stored readiness report…</p>;
  if (tab === 1) return <div className="sp-grid two">
    <section className="sp-panel"><h3>Stored records</h3><p className="sp-muted">Fixtures, odds, predictions, qualification decisions, tickets, displayed booking codes, rollover days and learning runs stay in the sports_ai tables. A missing price or result stays missing.</p></section>
    <section className="sp-panel"><h3>Identity</h3><p className="sp-muted">Preview mode accepts loopback requests. Production should mount these routes behind the CACSMS ONE session. Administrator actions require SPORTS_ADMIN_API_KEY.</p></section>
  </div>;
  if (tab === 2) return <section className="sp-panel"><h3>Operations</h3>{report.procedures.map(item => <p key={item}>{item}</p>)}<h3>Rollback</h3>{report.rollback.map(item => <p key={item}>{item}</p>)}</section>;
  if (tab === 3) return <section className="sp-panel"><h3>Unresolved limitations</h3><div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>Check</th><th>State</th><th>Evidence</th></tr></thead><tbody>
    {report.checks.filter(item => !item.ok).map(item => <tr key={item.name}><td>{item.name}</td><td>Open</td><td>{item.detail}</td></tr>)}
    {!report.checks.some(item => !item.ok) && <tr><td className="sp-empty" colSpan="3">No open limitation is stored.</td></tr>}
  </tbody></table></div></section>;
  return <section className="sp-panel"><h3>{report.ready ? 'Production-ready' : 'Not production-ready'}</h3><p className="sp-muted">Paid wagering: disabled. The verdict uses the checks below.</p><div className="sp-table-scroll"><table className="sp-table"><thead><tr><th>Check</th><th>Result</th><th>Evidence</th></tr></thead><tbody>
    {report.checks.map(item => <tr key={item.name}><td>{item.name}</td><td>{item.ok ? 'Met' : 'Open'}</td><td>{item.detail}</td></tr>)}
  </tbody></table></div></section>;
}
