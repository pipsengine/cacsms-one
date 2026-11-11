"""Authenticated SportyBet account: gates, rehearsal, submission and reconciliation.

Runs a real Chromium against a local page that mimics the signed-in SportyBet
header, match list and betslip. Nothing here contacts SportyBet.
"""
import asyncio
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import db
import single_betting as sb
import sportybet_account as acct

STATE = {'flow': 'success'}

PAGE = '''<!doctype html><html><body>
<div class="s-header">NGN 4,755.89 Deposit Bet History My Account</div>
<div class="match-row"><span>Braga</span><span>Estrela</span>
  <span class="m-outcome">1.72</span><span class="m-outcome">3.70</span><span class="m-outcome">4.80</span></div>
<div class="m-betslip-wrapper">
  <div id="count">Betslip0</div>
  <div class="mode-switch-button"><div class="switch-box">
    <div class="shadow-text left-side" data-op="switch-box-left" id="real">REAL</div>
    <div class="inside-btn right-side active" data-op="switch-box-right" id="sim">SIM</div>
  </div></div>
  <div id="pick"></div><i class="m-icon-delete" id="bin" style="display:inline-block;width:10px;height:10px;background:#999"></i>
  <input class="m-input" placeholder="min. 10" value="">
  <div id="totals">Total Stake NGN 0 Potential Win 0.00</div>
  <button class="af-button af-button--primary" id="place">Place Bet</button>
</div>
<div id="pop"></div>
<script>
const flow = "__FLOW__";
let odds = 0;
// SportyBet keeps the betslip between visits; "prefilled" starts with a leftover selection.
if (flow === 'prefilled') {
  document.getElementById('count').textContent = 'Betslip1';
  document.getElementById('pick').textContent = 'Home Other FC v Leftover United 1X2 2.10';
}
document.getElementById('bin').addEventListener('click', () => {
  odds = 0; document.getElementById('count').textContent = 'Betslip0'; document.getElementById('pick').textContent = '';
});
const stake = document.querySelector('.m-input');
const update = () => {
  const s = Number(stake.value || 0);
  document.getElementById('totals').textContent = `Total Stake NGN ${s} Potential Win ${(s*odds).toFixed(2)}`;
};
document.querySelectorAll('.m-outcome').forEach(node => node.addEventListener('click', () => {
  odds = Number(node.innerText); document.getElementById('count').textContent = 'Betslip1';
  document.getElementById('pick').textContent = 'Home Braga v Estrela 1X2 ' + node.innerText; update();
}));
stake.addEventListener('input', update); stake.addEventListener('change', update);
// Mirrors SportyBet: the selected label carries .inside-btn, and REAL keeps "inactive" even when selected.
document.getElementById('real').addEventListener('click', () => {
  document.getElementById('real').className = 'inside-btn left-side inactive';
  document.getElementById('sim').className = 'shadow-text right-side show-highlight';
});
document.getElementById('place').addEventListener('click', () => {
  const pop = document.getElementById('pop');
  if (flow === 'silent') return;
  if (flow === 'odds') { pop.innerHTML = '<div class="m-pop-main">Odds have changed. Accept changes?<button>Accept</button></div>'; return; }
  pop.innerHTML = '<div class="m-pop-main">Confirm bet. Total Stake NGN 100.00 <button id="ok">Confirm</button></div>';
  document.getElementById('ok').addEventListener('click', () => {
    pop.innerHTML = '<div class="m-pop-main">Submission Successful! Ticket ID: TK12345678</div>';
  });
});
</script></body></html>'''


# Two matches and a slip with Singles / Multiple tabs. On the Singles tab each selection
# gets its own stake input; on Multiple there is one stake over the product of the prices.
# "multi_singles" ignores the Multiple tab, so the slip stays unsafe for a combined bet.
MULTI_PAGE = '''<!doctype html><html><body>
<div class="s-header">NGN 4,755.89 Deposit Bet History My Account</div>
<div class="match-row"><span>Braga</span><span>Estrela</span>
  <span class="m-outcome">1.12</span><span class="m-outcome">7.50</span><span class="m-outcome">15.00</span></div>
<div class="match-row"><span>Porto</span><span>Benfica</span>
  <span class="m-outcome">1.14</span><span class="m-outcome">7.00</span><span class="m-outcome">13.00</span></div>
<div class="m-betslip-wrapper">
  <div id="count">Betslip0</div>
  <ul><li id="tab-single">Singles</li><li id="tab-multi">Multiple</li></ul>
  <div class="mode-switch-button"><div class="switch-box">
    <div class="shadow-text left-side" data-op="switch-box-left" id="real">REAL</div>
    <div class="inside-btn right-side active" data-op="switch-box-right" id="sim">SIM</div>
  </div></div>
  <div id="pick"></div><i class="m-icon-delete" id="bin" style="display:inline-block;width:10px;height:10px;background:#999"></i>
  <input class="m-input" id="s1" placeholder="min. 10" value="">
  <input class="m-input" id="s2" placeholder="min. 10" value="" style="display:none">
  <div id="totals">Total Stake NGN 0 Potential Win 0.00</div>
  <button class="af-button af-button--primary" id="place">Place Bet</button>
</div>
<div id="pop"></div>
<script>
const flow = "__FLOW__";
let picks = []; let tab = 'singles';
const s1 = document.getElementById('s1'), s2 = document.getElementById('s2');
const render = () => {
  document.getElementById('count').textContent = 'Betslip' + picks.length;
  document.getElementById('pick').textContent = picks.map(p => 'Home ' + p.name + ' 1X2 ' + p.odds).join(' | ');
  s2.style.display = (tab === 'singles' && picks.length > 1) ? '' : 'none';
  const a = Number(s1.value || 0), b = Number(s2.value || 0);
  let total, win;
  if (tab === 'multi') { total = a; win = a * picks.reduce((x, p) => x * p.odds, 1); }
  else { total = a + (picks.length > 1 ? b : 0); win = a * (picks[0] ? picks[0].odds : 0) + (picks[1] ? b * picks[1].odds : 0); }
  document.getElementById('totals').textContent = `Total Stake NGN ${total} Potential Win ${win.toFixed(2)}`;
};
document.querySelectorAll('.match-row').forEach(row => row.querySelectorAll('.m-outcome').forEach(node => node.addEventListener('click', () => {
  const name = [...row.querySelectorAll('span')].slice(0, 2).map(s => s.innerText).join(' v ');
  picks = picks.filter(p => p.name !== name).concat([{name, odds: Number(node.innerText)}]); render();
})));
document.getElementById('bin').addEventListener('click', () => { picks = []; render(); });
document.getElementById('tab-multi').addEventListener('click', () => { if (flow !== 'multi_singles') { tab = 'multi'; render(); } });
s1.addEventListener('input', render); s1.addEventListener('change', render);
document.getElementById('real').addEventListener('click', () => {
  document.getElementById('real').className = 'inside-btn left-side inactive';
  document.getElementById('sim').className = 'shadow-text right-side show-highlight';
});
document.getElementById('place').addEventListener('click', () => {
  const pop = document.getElementById('pop');
  pop.innerHTML = '<div class="m-pop-main">Confirm bet. Total Stake NGN 100.00 <button id="ok">Confirm</button></div>';
  document.getElementById('ok').addEventListener('click', () => {
    pop.innerHTML = '<div class="m-pop-main">Submission Successful! Ticket ID: TK87654321</div>';
  });
});
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        page = MULTI_PAGE if STATE['flow'].startswith('multi') else PAGE
        body = page.replace('__FLOW__', STATE['flow']).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class AccountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.previous_base = acct.BASE_URL
        acct.BASE_URL = f'http://127.0.0.1:{cls.server.server_address[1]}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        acct.BASE_URL = cls.previous_base

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = db.DB
        db.DB = Path(self.temp.name) / 'sports.sqlite3'
        db.initialize()
        acct.save_session({'cookies': [], 'origins': []})
        STATE['flow'] = 'success'

    def tearDown(self):
        acct.forget_session()
        db.DB = self.previous
        self.temp.cleanup()

    def _bet(self, connection, now):
        kickoff = (now + timedelta(hours=3)).isoformat()
        bet = {'id': 'bet-1', 'fixture_id': 'fx', 'sport': 'football', 'competition': 'Primeira Liga', 'home': 'Braga',
               'away': 'Estrela', 'market': 'HOME', 'selection': 'Home Win (1)', 'decimal_odds': 1.72,
               'probability': 0.645, 'expected_value': 0.1094, 'stake': 100.0, 'starts_at': kickoff,
               'odds_observed_at': now.isoformat(), 'local_date': sb.local_date(now)}
        connection.execute(
            """INSERT INTO sports_ai_single_bets(id,fixture_id,sport,competition,home,away,market,selection,decimal_odds,
               probability,expected_value,stake,mode,status,local_date,starts_at,odds_observed_at,created_at)
               VALUES(:id,:fixture_id,:sport,:competition,:home,:away,:market,:selection,:decimal_odds,:probability,
               :expected_value,:stake,'PAPER','QUEUED',:local_date,:starts_at,:odds_observed_at,:odds_observed_at)""", bet)
        return bet

    def _combo(self, connection, now):
        kickoff = (now + timedelta(hours=3)).isoformat()
        legs = [
            {'leg_no': 1, 'fixture_id': 'fa', 'sport': 'football', 'competition': 'Primeira Liga', 'home': 'Braga',
             'away': 'Estrela', 'market': 'HOME', 'selection': 'Home Win (1)', 'decimal_odds': 1.12, 'probability': 0.95,
             'starts_at': kickoff, 'odds_observed_at': now.isoformat()},
            {'leg_no': 2, 'fixture_id': 'fb', 'sport': 'football', 'competition': 'Primeira Liga', 'home': 'Porto',
             'away': 'Benfica', 'market': 'HOME', 'selection': 'Home Win (1)', 'decimal_odds': 1.14, 'probability': 0.93,
             'starts_at': kickoff, 'odds_observed_at': now.isoformat()},
        ]
        bet = {'id': 'combo-1', 'kind': 'COMBO', 'legs': legs, 'fixture_id': 'fa', 'sport': 'football',
               'competition': 'Primeira Liga', 'home': 'Braga', 'away': 'Estrela', 'market': 'COMBO',
               'selection': 'Home Win (1) + Home Win (1)', 'decimal_odds': 1.2768, 'probability': 0.95 * 0.93,
               'expected_value': 1.2768 * 0.95 * 0.93 - 1, 'stake': 100.0, 'starts_at': kickoff,
               'odds_observed_at': now.isoformat(), 'local_date': sb.local_date(now)}
        connection.execute(
            """INSERT INTO sports_ai_single_bets(id,fixture_id,sport,competition,home,away,market,selection,decimal_odds,
               probability,expected_value,stake,mode,status,local_date,starts_at,odds_observed_at,created_at,kind)
               VALUES(:id,:fixture_id,:sport,:competition,:home,:away,:market,:selection,:decimal_odds,:probability,
               :expected_value,:stake,'PAPER','QUEUED',:local_date,:starts_at,:odds_observed_at,:odds_observed_at,'COMBO')""",
            {k: v for k, v in bet.items() if k not in ('kind', 'legs')})
        for leg in legs:
            connection.execute(
                """INSERT INTO sports_ai_single_bet_legs(bet_id,leg_no,fixture_id,sport,competition,home,away,market,selection,
                   decimal_odds,probability,starts_at,odds_observed_at)
                   VALUES('combo-1',:leg_no,:fixture_id,:sport,:competition,:home,:away,:market,:selection,
                   :decimal_odds,:probability,:starts_at,:odds_observed_at)""", leg)
        return bet

    def _connected(self, connection, now):
        acct._set_account(connection, 'CONNECTED', 'test', now, last_ok_at=now.isoformat(), balance=4755.89)

    def _rehearsals(self, connection, now, n=3):
        for i in range(n):
            connection.execute(
                """INSERT INTO sports_ai_single_live_orders(id,bet_id,kind,status,local_date,stake,created_at,updated_at)
                   VALUES(?,?,'REHEARSAL','REHEARSED',?,100,?,?)""",
                (f'r{i}', f'old{i}', sb.local_date(now), now.isoformat(), now.isoformat()))

    def test_authorization_needs_phrase_session_and_rehearsals_never_a_balance(self):
        now = datetime.now(timezone.utc)
        with db.connect() as c:
            with self.assertRaises(ValueError):  # balance alone is not authorization
                c.execute("UPDATE sports_ai_sportybet_account SET balance=99999 WHERE id='primary'")
                acct.grant_authorization(c, 'admin', acct.ACKNOWLEDGEMENT, 24, now)
            self._connected(c, now)
            with self.assertRaisesRegex(ValueError, 'rehearsals'):
                acct.grant_authorization(c, 'admin', acct.ACKNOWLEDGEMENT, 24, now)
            self._rehearsals(c, now)
            with self.assertRaisesRegex(ValueError, 'acknowledgement'):
                acct.grant_authorization(c, 'admin', 'yes', 24, now)
            with self.assertRaisesRegex(ValueError, 'authorization'):
                acct.set_execution_mode(c, 'LIVE', 'admin', now)
            grant = acct.grant_authorization(c, 'admin', acct.ACKNOWLEDGEMENT, 24, now)
            acct.set_execution_mode(c, 'LIVE', 'admin', now)
            reasons, active = acct.submission_gate(c, now)
            self.assertEqual(reasons, [])
            self.assertEqual(active['id'], grant['id'])
            acct.revoke_authorization(c, 'admin', now)
            self.assertIn('No active real-money authorization', acct.submission_gate(c, now)[0])

    def test_daily_live_limits_are_enforced(self):
        now = datetime.now(timezone.utc)
        with db.connect() as c:
            self._connected(c, now)
            self._rehearsals(c, now)
            acct.grant_authorization(c, 'admin', acct.ACKNOWLEDGEMENT, 24, now)
            acct.set_execution_mode(c, 'LIVE', 'admin', now)
            for i in range(25):
                c.execute("""INSERT INTO sports_ai_single_live_orders(id,bet_id,kind,status,local_date,stake,created_at,updated_at)
                             VALUES(?,?,'SUBMISSION','PLACED',?,100,?,?)""", (f's{i}', f'b{i}', sb.local_date(now), now.isoformat(), now.isoformat()))
            reasons, _ = acct.submission_gate(c, now)
        self.assertTrue(any('Daily live limit' in r for r in reasons))

    def test_rehearsal_prepares_slip_without_clicking_place_bet(self):
        now = datetime.now(timezone.utc)
        with db.connect() as c:
            bet = self._bet(c, now)
        outcome, status, detail = asyncio.run(acct.execute_async(bet, 'REHEARSAL', now))
        self.assertEqual((outcome, status), ('PAPER_PLACED', 'REHEARSED'), detail)
        with db.connect() as c:
            order = c.execute('SELECT * FROM sports_ai_single_live_orders').fetchone()
        checks = json.loads(order['checks_json'])['checks']
        self.assertTrue(all(checks.values()), checks)
        self.assertIsNone(order['submitted_at'])

    def test_submission_confirms_once_and_records_ticket(self):
        now = datetime.now(timezone.utc)
        with db.connect() as c:
            self._connected(c, now)
            self._rehearsals(c, now)
            acct.grant_authorization(c, 'admin', acct.ACKNOWLEDGEMENT, 24, now)
            acct.set_execution_mode(c, 'LIVE', 'admin', now)
            bet = self._bet(c, now)
        outcome, status, detail = asyncio.run(acct.execute_async(bet, 'SUBMISSION', now))
        self.assertEqual((outcome, status), ('LIVE_PLACED', 'PLACED'), detail)
        with db.connect() as c:
            sb.apply_execution(c, bet, outcome, status, detail, 'SUBMISSION', now)
            order = c.execute("SELECT * FROM sports_ai_single_live_orders WHERE kind='SUBMISSION'").fetchone()
            row = c.execute("SELECT status, mode FROM sports_ai_single_bets WHERE id='bet-1'").fetchone()
            self.assertEqual(order['bookmaker_ref'], 'TK12345678')
            self.assertEqual((row['status'], row['mode']), ('PLACED', 'PAPER'))
            # The same bet can never be submitted twice.
            with self.assertRaises(Exception):
                acct._order(c, bet, 'SUBMISSION', None, now)
            placed = acct._now(now).astimezone(acct.WAT).strftime('%d/%m/%Y %H:%M')
            # The operator's own manual slip on another match, and a multiple on this match, are never claimed.
            history = f'''{placed}
Singles
Running
Sitra v Al Najma
Total Stake
100.00
Total Return
--
{placed}
Multiple
Won
Braga v Estrela
Porto v Benfica
Total Stake
100.00
Total Return
400.00
{placed}
Singles
Won
SC Braga v Estrela da Amadora
Total Stake
100.00
Total Return
172.00'''
            updated = acct.reconcile(c, history, now)
            self.assertEqual(updated, 1)
            order = c.execute("SELECT status, payout FROM sports_ai_single_live_orders WHERE kind='SUBMISSION'").fetchone()
            self.assertEqual((order['status'], order['payout']), ('SETTLED_WON', 172.0))

    def test_combined_bet_is_submitted_as_one_multiple_and_reconciled(self):
        now = datetime.now(timezone.utc)
        STATE['flow'] = 'multi'
        with db.connect() as c:
            self._connected(c, now)
            self._rehearsals(c, now)
            acct.grant_authorization(c, 'admin', acct.ACKNOWLEDGEMENT, 24, now)
            acct.set_execution_mode(c, 'LIVE', 'admin', now)
            bet = self._combo(c, now)
        outcome, status, detail = asyncio.run(acct.execute_async(bet, 'SUBMISSION', now))
        self.assertEqual((outcome, status), ('LIVE_PLACED', 'PLACED'), detail)
        self.assertIn('combined bet', detail)
        with db.connect() as c:
            order = c.execute("SELECT * FROM sports_ai_single_live_orders WHERE kind='SUBMISSION'").fetchone()
            checks = json.loads(order['checks_json'])
            self.assertTrue(all(checks['checks'].values()), checks['checks'])
            self.assertAlmostEqual(order['page_odds'], 1.2768)
            self.assertEqual(order['bookmaker_ref'], 'TK87654321')
            sb.apply_execution(c, bet, outcome, status, detail, 'SUBMISSION', now)
            self.assertEqual(c.execute("SELECT status FROM sports_ai_single_bets WHERE id='combo-1'").fetchone()[0], 'PLACED')
            placed = acct._now(now).astimezone(acct.WAT).strftime('%d/%m/%Y %H:%M')
            # A single on one leg is never claimed; the Multiple with exactly both legs is.
            history = f'''{placed}
Singles
Won
Braga v Estrela
Total Stake
100.00
Total Return
112.00
{placed}
Multiple
Won
Porto v Benfica
SC Braga v Estrela da Amadora
Total Stake
100.00
Total Return
127.68'''
            self.assertEqual(acct.reconcile(c, history, now), 1)
            order = c.execute("SELECT status, payout FROM sports_ai_single_live_orders WHERE kind='SUBMISSION'").fetchone()
            self.assertEqual((order['status'], order['payout']), ('SETTLED_WON', 127.68))

    def test_no_edge_ticket_is_submitted_in_live_mode(self):
        now = datetime.now(timezone.utc)
        STATE['flow'] = 'multi'
        with db.connect() as c:
            self._connected(c, now)
            self._rehearsals(c, now)
            acct.grant_authorization(c, 'admin', acct.ACKNOWLEDGEMENT, 24, now)
            acct.set_execution_mode(c, 'LIVE', 'admin', now)
            bet = self._combo(c, now)
            for item in bet['legs']:
                item['probability'] = 0.75          # 56% joint, negative expected value
            bet.update(kind='TICKET', probability=0.5625, expected_value=1.2768 * 0.5625 - 1)
            c.execute("UPDATE sports_ai_single_bets SET kind='TICKET', probability=0.5625 WHERE id='combo-1'")
        outcome, status, detail = asyncio.run(acct.execute_async(bet, 'SUBMISSION', now))
        self.assertEqual((outcome, status), ('LIVE_PLACED', 'PLACED'), detail)
        with db.connect() as c:
            order = c.execute("SELECT checks_json FROM sports_ai_single_live_orders WHERE kind='SUBMISSION'").fetchone()
            self.assertTrue(all(json.loads(order['checks_json'])['checks'].values()))
            self.assertEqual(acct.grant_authorization.__module__, 'sportybet_account')
            reasons, grant = acct.submission_gate(c, now)
            self.assertEqual(grant['daily_count'], 10)

    def test_combined_bet_on_singles_tab_is_never_placed(self):
        now = datetime.now(timezone.utc)
        STATE['flow'] = 'multi_singles'
        with db.connect() as c:
            bet = self._combo(c, now)
        outcome, status, detail = asyncio.run(acct.execute_async(bet, 'REHEARSAL', now))
        self.assertEqual((outcome, status), ('CANCEL', 'CHECKS'), detail)
        self.assertIn('one_stake_input', detail)

    def test_unclear_result_pauses_engine_and_blocks_further_submissions(self):
        now = datetime.now(timezone.utc)
        STATE['flow'] = 'silent'
        with db.connect() as c:
            self._connected(c, now)
            self._rehearsals(c, now)
            acct.grant_authorization(c, 'admin', acct.ACKNOWLEDGEMENT, 24, now)
            acct.set_execution_mode(c, 'LIVE', 'admin', now)
            bet = self._bet(c, now)
        outcome, status, detail = asyncio.run(acct.execute_async(bet, 'SUBMISSION', now))
        self.assertEqual((outcome, status), ('UNKNOWN', 'UNKNOWN'))
        with db.connect() as c:
            sb.apply_execution(c, bet, outcome, status, detail, 'SUBMISSION', now)
            self.assertTrue(sb.runtime(c)['paused'])
            reasons, _ = acct.submission_gate(c, now)
        self.assertTrue(any('unclear' in r for r in reasons))

    def test_changed_odds_are_never_accepted(self):
        now = datetime.now(timezone.utc)
        STATE['flow'] = 'odds'
        with db.connect() as c:
            self._connected(c, now)
            self._rehearsals(c, now)
            acct.grant_authorization(c, 'admin', acct.ACKNOWLEDGEMENT, 24, now)
            acct.set_execution_mode(c, 'LIVE', 'admin', now)
            bet = self._bet(c, now)
        outcome, status, _detail = asyncio.run(acct.execute_async(bet, 'SUBMISSION', now))
        self.assertEqual((outcome, status), ('CANCEL', 'REJECTED'))

    def test_session_check_reads_balance_from_signed_in_header(self):
        result = acct.check_session(capture_history=True)
        self.assertEqual(result['status'], 'CONNECTED')
        self.assertAlmostEqual(result['balance'], 4755.89)
        with db.connect() as c:
            view = acct.status_view(c)
        self.assertEqual(view['balance']['status'], 'SYNCED')
        self.assertIsNotNone(view['reconciliation']['last_history_capture'])

    def test_test_rehearsal_ignores_edge_and_never_counts_toward_authorization(self):
        now = datetime.now(timezone.utc)
        bet = {'id': 'test:fx', 'fixture_id': 'fx', 'sport': 'football', 'competition': 'Primeira Liga',
               'home': 'Braga', 'away': 'Estrela', 'market': 'HOME', 'selection': 'Home Win (1)',
               'decimal_odds': 1.72, 'probability': 0.40, 'stake': 100.0}  # negative EV: not qualified
        outcome, status, detail = asyncio.run(acct.execute_async(bet, 'REHEARSAL', now, test=True))
        self.assertEqual(status, 'REHEARSED', detail)
        with db.connect() as c:
            self.assertEqual(acct.rehearsals_passed(c, now), 0)
            order = acct.status_view(c, now)['orders'][0]
            self.assertTrue(order['test'])
            self.assertEqual((order['home'], order['away']), ('Braga', 'Estrela'))
            self.assertIsNone(c.execute("SELECT submitted_at FROM sports_ai_single_live_orders").fetchone()[0])
        with self.assertRaises(ValueError):
            asyncio.run(acct.execute_async(bet, 'SUBMISSION', now, test=True))

    def test_leftover_selection_is_cleared_and_slip_is_left_empty(self):
        now = datetime.now(timezone.utc)
        STATE['flow'] = 'prefilled'
        with db.connect() as c:
            bet = self._bet(c, now)
        outcome, status, detail = asyncio.run(acct.execute_async(bet, 'REHEARSAL', now))
        self.assertEqual(status, 'REHEARSED', detail)
        with db.connect() as c:
            checks = json.loads(c.execute('SELECT checks_json FROM sports_ai_single_live_orders').fetchone()[0])
        self.assertTrue(checks['checks']['one_selection'])
        self.assertTrue(checks['slip_cleared'])
        self.assertNotIn('Leftover', checks['slip']['text'])

    def test_history_capture_drops_header_and_account_details(self):
        page = '''NGN 4,755.89
My Account
JANE DOE
08000000000
Balance
Sport Bets
10/11/2026 05:24
Singles
Running
A v B
Total Stake
100.00
Total Return
--'''
        kept = acct.bet_list_text(page)
        self.assertTrue(kept.startswith('10/11/2026 05:24'))
        self.assertNotIn('JANE DOE', kept)
        self.assertNotIn('08000000000', kept)
        rows = acct.parse_history(kept)
        self.assertEqual((rows[0]['type'], rows[0]['status'], rows[0]['stake'], rows[0]['events']), ('single', 'running', 100.0, ['A v B']))


if __name__ == '__main__':
    unittest.main()
