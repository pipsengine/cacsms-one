import React, {useEffect, useState} from 'react';
import {useLiveReload} from './live';
import {AlertCircle, RefreshCw} from 'lucide-react';
import {get, post} from './api';

function localTime(value) {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
}

export default function Diagnostics({tab = 0}) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(true);
  const [adminKey, setAdminKey] = useState('');

  async function load(silent = false) {
    if (!silent) setLoading(true);
    setError('');
    try {
      setReport(await get('/operations/diagnostics'));
    } catch (loadError) {
      setReport(null);
      setError(loadError.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);
  useLiveReload(() => load(true));

  async function collect(providerId) {
    setMessage('');
    setError('');
    try {
      const result = await post(`/acquisition/sources/${providerId}/collect`, {}, adminKey ? {'X-Sports-Admin-Key': adminKey} : {});
      setMessage(`${result.records_saved} record(s) saved from ${result.source_url || 'the provider'}.`);
      await load();
    } catch (collectError) {
      setError(collectError.message);
    }
  }

  async function testConnection(providerId) {
    setMessage('');
    setError('');
    try {
      const result = await post(`/acquisition/sources/${providerId}/test`, {});
      setMessage(result.message || 'Connection succeeded.');
      await load();
    } catch (testError) {
      setError(testError.message);
    }
  }

  if (loading && !report) return <div className="sp-emptybox">Checking backend, database and providers…</div>;
  if (!report) return <p className="sp-ops-error"><AlertCircle size={16} />{error || 'The diagnostics service did not respond.'}</p>;

  const providers = report.providers || [];
  const football = providers.filter(row => row.sport === 'football' || row.sport === 'both');
  const basketball = providers.filter(row => row.sport === 'basketball' || row.sport === 'both');

  return (
    <div className="sp-intelligence-stack">
      <section className="sp-panel">
        <div className="sp-panel-head">
          <div>
            <h3>Live data mode</h3>
            <p>{report.prediction_note}</p>
          </div>
          <button className="sp-secondary" onClick={() => load()}><RefreshCw size={16} />{loading ? 'Refreshing…' : 'Refresh Data'}</button>
        </div>
        {error && <p className="sp-ops-error"><AlertCircle size={16} />{error}</p>}
        {message && <p role="status">{message}</p>}
        <div className="sp-intel-detail-grid">
          {[
            ['Backend', report.backend],
            ['SQLite', `${report.sqlite} (${report.journal_mode})`],
            ['Data mode', report.data_mode],
            ['Football fixtures', report.football_fixtures],
            ['Basketball fixtures', report.basketball_fixtures],
            ['Odds', report.odds_status],
            ['Browser', report.browser?.status],
            ['SportyBet', report.sportybet?.availability],
          ].map(([label, value]) => <div key={label}><small>{label}</small><strong>{value ?? '—'}</strong></div>)}
        </div>
        <label className="sp-intel-block">Admin key for collection<input type="password" value={adminKey} onChange={event => setAdminKey(event.target.value)} autoComplete="off" /></label>
      </section>
      {tab === 0 && <ProviderPanel title="Football and basketball providers" rows={[...football, ...basketball]} onTest={testConnection} onCollect={collect} />}
      {tab === 1 && (
        <section className="sp-panel">
          <h3>Browser research</h3>
          <p>{report.browser?.detail || report.browser?.status}</p>
          <p className="sp-muted">Approved non-BetExplorer hosts: {report.approved_browser_hosts}. BetExplorer remains a manual research reference.</p>
          <h3>SportyBet</h3>
          <p>{report.sportybet?.reason}</p>
          <p className="sp-muted">Automatic booking code: {String(report.sportybet?.automatic_booking_code)}. Wager placement: {String(report.sportybet?.automatic_wager_placement)}.</p>
          <h3>Scheduler</h3>
          <p>{report.scheduler?.detail}</p>
          <p className="sp-muted">Enabled provider schedules: {report.scheduler?.enabled_provider_schedules}. Leader heartbeat {localTime(report.scheduler?.leader?.heartbeat_at)}.</p>
        </section>
      )}
      {tab === 2 && (
        <section className="sp-panel">
          <h3>Collection log</h3>
          <div className="sp-table-scroll">
            <table className="sp-table">
              <thead><tr><th>Status</th><th>Seen</th><th>Saved</th><th>Started</th><th>Error</th></tr></thead>
              <tbody>
                {(report.recent_collections || []).length ? report.recent_collections.map(row => (
                  <tr key={row.id}><td>{row.status}</td><td>{row.records_seen}</td><td>{row.records_saved}</td><td>{localTime(row.started_at)}</td><td>{row.error || '—'}</td></tr>
                )) : <tr><td colSpan="5" className="sp-empty">Awaiting Real Data. No collection job has completed.</td></tr>}
              </tbody>
            </table>
          </div>
          <h3>Stored fixture sources</h3>
          {(report.fixtures_by_source || []).length ? report.fixtures_by_source.map(row => (
            <p key={`${row.sport}-${row.source}`}>{row.sport} / {row.source}: {row.fixtures}</p>
          )) : <p>Awaiting Real Data.</p>}
        </section>
      )}
    </div>
  );
}

function ProviderPanel({title, rows, onTest, onCollect}) {
  return (
    <section className="sp-panel">
      <h3>{title}</h3>
      <div className="sp-table-scroll">
        <table className="sp-table">
          <thead><tr><th>Provider</th><th>Status</th><th>Last success</th><th>Credential</th><th>Actions</th></tr></thead>
          <tbody>
            {rows.map(row => (
              <tr key={row.id}>
                <td>{row.display_name}<small className="sp-intel-fine">{row.terms_url}</small></td>
                <td>{row.availability}</td>
                <td>{localTime(row.last_success_at)}</td>
                <td>{row.credential_configured ? 'Configured or not required' : 'Provider Not Configured'}</td>
                <td>
                  <span className="sp-intel-actions">
                    <button className="sp-secondary" onClick={() => onTest(row.id)}>Test Connection</button>
                    {row.enabled ? <button className="sp-secondary" onClick={() => onCollect(row.id)}>Collect Now</button> : null}
                    {row.terms_url ? <a className="sp-secondary" href={row.terms_url} target="_blank" rel="noreferrer">View Source</a> : null}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
