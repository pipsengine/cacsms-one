import asyncio
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from integrity_support import eligible_everywhere

import db
from sportybet_booking import create_job, prepare_slip, process_booking_queue, qualified_selections, select_booking

PAGE = """<!doctype html><html><body>
<article><span>Pistons</span><span>76ers</span>
<button class="price">__HOME__</button><button class="price">2.07</button></article>
<button id="place">Place bet</button>
<button id="book">Book bet</button>
<div data-booking-code></div>
<script>
document.body.dataset.placed='no';
document.body.dataset.booked='no';
document.querySelectorAll('.price').forEach(button => button.addEventListener('click', () => button.dataset.clicked='yes'));
document.getElementById('place').addEventListener('click', () => { document.body.dataset.placed='yes'; });
document.getElementById('book').addEventListener('click', () => {
  document.body.dataset.booked='yes';
  document.querySelector('[data-booking-code]').textContent='BK7REAL';
});
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    html = PAGE.replace('__HOME__', '1.80')

    def do_GET(self):
        body = self.html.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


class BookingTests(unittest.TestCase):
    def setUp(self):
        eligible_everywhere(self)

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.previous = db.DB
        db.DB = Path(cls.temp.name) / 'sports.sqlite3'
        db.initialize()
        with db.connect() as _c:
            __import__('booking_policy').save(_c, __import__('booking_policy').STRICT)  # these tests cover the strict gates
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_address[1]}/'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        db.DB = cls.previous
        cls.temp.cleanup()

    def scheduled_fixture(self, connection):
        from datetime import datetime, timedelta, timezone
        kickoff = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        connection.execute(
            """INSERT OR IGNORE INTO sports_ai_fixtures(id,external_id,source,sport,competition,home,away,starts_at,status,
                 statistics_json,odds_json,observed_at) VALUES('pistons','pistons','test','basketball','NBA','Pistons','76ers',?,
                 'scheduled','{}','{}',?)""", (kickoff, kickoff),
        )

    def selection(self, odds=1.80):
        return {
            'fixture_id': 'pistons', 'home': 'Pistons', 'away': '76ers', 'sport': 'basketball',
            'market': 'MONEYLINE_HOME', 'selection': 'Pistons', 'decimal_odds': odds,
        }

    def test_unqualified_queue_does_not_open_a_browser(self):
        with db.connect() as connection:
            connection.execute('DELETE FROM sports_ai_booking_jobs')
            self.assertEqual(qualified_selections(connection), [])
        result = process_booking_queue()
        self.assertEqual(result['status'], 'waiting')
        self.assertEqual(result['qualified'], 0)
        self.assertFalse(result['places_wager'])

    @patch('qualification_engine.require_current_qualification')
    def test_duplicate_qualified_set_is_not_queued_twice(self, qualification):
        with db.connect() as connection:
            connection.execute('DELETE FROM sports_ai_booking_jobs')
            moment = '2026-11-04T09:00:00+00:00'
            connection.execute(
                """INSERT INTO sports_ai_qualification_decisions
                   (id,prediction_id,fixture_id,sport,market,state,reason_code,reason,evidence_json,products_json,
                    policy_version,decision_version,fingerprint,created_at,updated_at)
                   VALUES('qual-pistons','pred-pistons','pistons','basketball','MONEYLINE_HOME','QUALIFIED',
                          'QUALIFIED_OK','Stored qualification','{}','["booking"]',1,1,'fp',?,?)""",
                (moment, moment),
            )
            self.scheduled_fixture(connection)
            first = create_job(connection, [self.selection()])
            second = create_job(connection, [self.selection()])
        self.assertFalse(first['duplicate'])
        self.assertTrue(second['duplicate'])
        self.assertEqual(first['id'], second['id'])

    @patch('qualification_engine.require_current_qualification')
    def test_closed_window_does_not_open_a_browser(self, qualification):
        from datetime import datetime, timezone
        with db.connect() as connection:
            connection.execute('DELETE FROM sports_ai_booking_jobs')
            moment = '2026-11-04T09:00:00+00:00'
            connection.execute(
                """INSERT INTO sports_ai_qualification_decisions
                   (id,prediction_id,fixture_id,sport,market,state,reason_code,reason,evidence_json,products_json,
                    policy_version,decision_version,fingerprint,created_at,updated_at)
                   VALUES('qual-window','pred-window','pistons','basketball','MONEYLINE_HOME','QUALIFIED',
                          'QUALIFIED_OK','Stored qualification','{}','["booking"]',1,1,'fp-window',?,?)""",
                (moment, moment),
            )
            self.scheduled_fixture(connection)
            create_job(connection, [self.selection()])
        result = process_booking_queue(datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc))
        self.assertEqual(result['status'], 'outside_window')
        self.assertEqual(result['phase'], 'event')
        self.assertFalse(result['places_wager'])
        with db.connect() as connection:
            status = connection.execute('SELECT status FROM sports_ai_booking_jobs').fetchone()['status']
        self.assertEqual(status, 'QUEUED')

    def test_click_adds_the_price_and_reads_the_page_code(self):
        from playwright.async_api import async_playwright

        async def run():
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                page = await (await browser.new_context()).new_page()
                await page.goto(self.url)
                result = await prepare_slip(page, [self.selection()])
                placed = await page.locator('body').get_attribute('data-placed')
                clicked = await page.locator('.price').first.get_attribute('data-clicked')
                await browser.close()
                return result, placed, clicked

        result, placed, clicked = asyncio.run(run())
        self.assertEqual(result['status'], 'CODE_GENERATED')
        self.assertEqual(result['code'], 'BK7REAL')
        self.assertEqual(placed, 'no')
        self.assertEqual(clicked, 'yes')

    def test_changed_price_uses_same_named_outcome_and_requests_a_code(self):
        from playwright.async_api import async_playwright
        Handler.html = PAGE.replace(
            '<button class="price">__HOME__</button><button class="price">2.07</button>',
            '<button class="price" data-selection="away">1.80</button>'
            '<button class="price" data-selection="home">1.95</button>',
        )

        async def run():
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                page = await (await browser.new_context()).new_page()
                await page.goto(self.url)
                result = await prepare_slip(page, [self.selection()])
                booked = await page.locator('body').get_attribute('data-booked')
                placed = await page.locator('body').get_attribute('data-placed')
                away_clicked = await page.locator('[data-selection="away"]').get_attribute('data-clicked')
                home_clicked = await page.locator('[data-selection="home"]').get_attribute('data-clicked')
                await browser.close()
                return result, booked, placed, away_clicked, home_clicked

        try:
            result, booked, placed, away_clicked, home_clicked = asyncio.run(run())
        finally:
            Handler.html = PAGE.replace('__HOME__', '1.80')
        self.assertEqual(result['status'], 'CODE_GENERATED')
        self.assertEqual(result['code'], 'BK7REAL')
        self.assertEqual(away_clicked, None)
        self.assertEqual(home_clicked, 'yes')
        self.assertEqual(result['seen'][0]['page_odds'], 1.95)
        self.assertEqual(booked, 'yes')
        self.assertEqual(placed, 'no')

    def test_changed_total_price_is_accepted_only_on_the_same_line(self):
        from playwright.async_api import async_playwright
        from sportybet_booking import FIND_LINE

        html = """<!doctype html><html><body>
          <div class="match-row">Pistons vs 76ers
            <div class="af-select"><span class="af-select-input">2.5</span></div>
            <button class="m-outcome">1.95</button><button class="m-outcome">1.85</button>
          </div>
          <button id="place">Place bet</button><button id="book">Book bet</button>
          <div data-booking-code></div>
          <script>
            document.body.dataset.placed='no';
            document.body.dataset.booked='no';
            document.querySelectorAll('.m-outcome').forEach(button => button.addEventListener('click', () => button.dataset.clicked='yes'));
            document.getElementById('place').addEventListener('click', () => { document.body.dataset.placed='yes'; });
            document.getElementById('book').addEventListener('click', () => {
              document.body.dataset.booked='yes';
              document.querySelector('[data-booking-code]').textContent='BK7REAL';
            });
          </script>
        </body></html>"""

        async def run():
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                page = await (await browser.new_context()).new_page()
                await page.set_content(html)
                selection = {
                    **self.selection(1.80),
                    'market': 'OVER_2.5',
                    'selection': 'Over 2.5',
                }
                result = await prepare_slip(page, [selection])
                clicked = await page.locator('.m-outcome').first.get_attribute('data-clicked')
                placed = await page.locator('body').get_attribute('data-placed')
                await browser.close()
                return result, clicked, placed

        result, clicked, placed = asyncio.run(run())
        self.assertEqual(result['status'], 'CODE_GENERATED')
        self.assertEqual(result['seen'][0]['page_odds'], 1.95)
        self.assertEqual(clicked, 'yes')
        self.assertEqual(placed, 'no')

    def test_reveal_fixture_drills_through_duplicate_country_into_exact_competition(self):
        from playwright.async_api import async_playwright
        from sportybet_booking import _reveal_fixture

        html = """<!doctype html><html><head><style>
          ul { width: 300px; margin: 0; padding: 0; }
          .category-list-item, .tournament-list-item { width: 280px; min-height: 20px; }
        </style></head><body>
          <ul>
            <li class="category-list-item"><div class="category-item"><span>Germany</span></div></li>
            <li class="category-list-item">
              <div class="category-item"><span>Germany</span></div>
              <ul id="competitions" hidden></ul>
            </li>
          </ul>
          <script>
            const country = document.querySelectorAll('.category-list-item')[1];
            country.addEventListener('click', () => {
              const competitions = document.getElementById('competitions');
              competitions.hidden = false;
              if (!competitions.childElementCount) {
                competitions.innerHTML = '<li class="tournament-list-item"><div class="tournament-name">2. Bundesliga</div><div id="matches" hidden></div></li>';
                competitions.firstElementChild.addEventListener('click', () => {
                  const matches = document.getElementById('matches');
                  matches.hidden = false;
                  matches.innerHTML = '<article class="match-row">1. FC Magdeburg FC St. Pauli</article>';
                });
              }
            });
          </script>
        </body></html>"""

        async def run():
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                page = await (await browser.new_context()).new_page()
                await page.set_content(html)
                result = await _reveal_fixture(page, {
                    'home': '1. FC Magdeburg',
                    'away': 'FC St. Pauli',
                    'competition': '2. Fußball-Bundesliga 2026/2027',
                })
                await browser.close()
                return result

        result = asyncio.run(run())
        self.assertTrue(result['visible'], result)

    def test_numbered_club_switches_total_line_and_books_current_price(self):
        from playwright.async_api import async_playwright

        html = """<!doctype html><html><body>
          <section>
            <button class="market-item">3 Way &amp; O/U</button>
            <div class="match-row">1. FC Magdeburg vs FC St. Pauli
              <div class="af-select"><div class="af-select-title">
                <span class="af-select-input">3</span>
              </div></div>
              <button class="m-outcome">1.90</button><button class="m-outcome">1.22</button>
            </div>
          </section>
          <div class="af-select-list" hidden><span class="af-select-item">4.5</span></div>
          <div class="af-select-list" id="lines" style="display:none">
            <span class="af-select-item">3</span><span class="af-select-item">4.5</span>
          </div>
          <button id="book">Book bet</button><button id="place">Place bet</button>
          <div data-booking-code></div>
          <script>
            document.body.dataset.placed='no';
            document.querySelector('.market-item').onclick = () => {
              document.querySelector('.af-select-input').textContent='3';
            };
            document.querySelector('.af-select-title').onclick = () => {
              document.getElementById('lines').style.display='block';
            };
            document.querySelectorAll('#lines .af-select-item').forEach(node => {
              node.onclick = () => {
                document.querySelector('.af-select-input').textContent=node.textContent;
                document.getElementById('lines').style.display='none';
              };
            });
            document.querySelectorAll('.m-outcome').forEach(node => {
              node.onclick = () => { node.dataset.clicked='yes'; };
            });
            document.getElementById('book').onclick = () => {
              document.querySelector('[data-booking-code]').textContent='BK7REAL';
            };
            document.getElementById('place').onclick = () => { document.body.dataset.placed='yes'; };
          </script>
        </body></html>"""

        async def run():
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.set_content(html)
                result = await prepare_slip(page, [{
                    **self.selection(1.20), 'home': '1. FC Magdeburg', 'away': 'FC St. Pauli',
                    'sport': 'football', 'market': 'UNDER_4.5', 'selection': 'UNDER',
                }])
                line = await page.locator('.af-select-input').inner_text()
                under = await page.locator('.m-outcome').last.get_attribute('data-clicked')
                over = await page.locator('.m-outcome').first.get_attribute('data-clicked')
                placed = await page.locator('body').get_attribute('data-placed')
                await browser.close()
                return result, line, under, over, placed

        result, line, under, over, placed = asyncio.run(run())
        self.assertEqual(result['status'], 'CODE_GENERATED')
        self.assertEqual(result['seen'][0]['page_odds'], 1.22)
        self.assertEqual(line, '4.5')
        self.assertEqual(under, 'yes')
        self.assertIsNone(over)
        self.assertEqual(placed, 'no')

    def test_competition_number_distinguishes_portugal_second_division(self):
        from playwright.async_api import async_playwright
        from sportybet_booking import REVEAL_FIXTURE

        async def run():
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.set_content('''<style>.tournament-list-item {width:280px;height:24px}</style>
                    <ul><li class="tournament-list-item"><div class="tournament-name">Liga Portugal</div></li>
                    <li class="tournament-list-item"><div class="tournament-name">Liga Portugal 2</div></li></ul>''')
                result = await page.evaluate(REVEAL_FIXTURE, {
                    'home': 'SC Farense', 'away': 'SL Benfica B', 'competition': 'Portugal Liga Portugal 2',
                })
                await browser.close()
                return result

        self.assertEqual(asyncio.run(run())['clicked'], 'liga portugal 2')

    def test_nested_country_tournament_is_opened_instead_of_collapsing_the_country(self):
        from playwright.async_api import async_playwright
        from sportybet_booking import _reveal_fixture

        html = """<!doctype html><html><head><style>
          ul { width: 300px; margin: 0; padding: 0; }
          .category-list-item { width: 148px; height: 24px; }
          .tournament-list { display: none; }
          .tournament-list.open { display: block; }
          .tournament-list-item { width: 0; height: 0; overflow: hidden; }
          .tournament-list.open .tournament-list-item { width: 148px; height: 24px; overflow: visible; }
        </style></head><body>
          <ul>
            <li class="category-list-item" id="spain">
              <div class="category-item"><span>Spain</span></div>
              <div class="tournament-list" id="spain-tournaments">
                <ul>
                  <li class="tournament-list-item" title="Primera FEB"><span class="tournament-name">Primera FEB</span></li>
                  <li class="tournament-list-item" title="Segunda FEB"><span class="tournament-name">Segunda FEB</span></li>
                </ul>
              </div>
            </li>
          </ul>
          <div id="board"></div>
          <script>
            const spain = document.getElementById('spain');
            const tournaments = document.getElementById('spain-tournaments');
            spain.addEventListener('click', (event) => {
              if (event.target.closest('.tournament-list-item')) return;
              tournaments.classList.toggle('open');
            });
            document.querySelector('[title="Segunda FEB"]').addEventListener('click', () => {
              document.getElementById('board').innerHTML =
                '<article class="match-row"><span>Cb Benicarlo</span> <span>Upb Gandia</span>'
                + '<button class="m-outcome">1.58</button><button class="m-outcome">2.20</button>'
                + '<button class="m-outcome">165.5</button><button class="m-outcome">1.85</button>'
                + '<button class="m-outcome">1.95</button></article>'
                + '<button id="book">Book bet</button><div data-booking-code></div>';
              document.getElementById('book').onclick = () => {
                document.querySelector('[data-booking-code]').textContent = 'BK7REAL';
              };
              document.querySelectorAll('.m-outcome').forEach(node => {
                node.onclick = () => { node.dataset.clicked = 'yes'; };
              });
            });
          </script>
        </body></html>"""

        async def run():
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                page = await (await browser.new_context()).new_page()
                await page.set_content(html)
                revealed = await _reveal_fixture(page, {
                    'home': 'Cb Benicarlo', 'away': 'Upb Gandia', 'competition': 'Spain Segunda FEB',
                })
                from sportybet_booking import prepare_slip
                result = await prepare_slip(page, [{
                    'fixture_id': 'benicarlo', 'home': 'Cb Benicarlo', 'away': 'Upb Gandia',
                    'sport': 'basketball', 'competition': 'Spain Segunda FEB',
                    'market': 'MONEYLINE_HOME', 'selection': 'home', 'decimal_odds': 1.58,
                }])
                home = await page.locator('.m-outcome').first.get_attribute('data-clicked')
                await browser.close()
                return revealed, result, home

        revealed, result, home = asyncio.run(run())
        self.assertTrue(revealed.get('visible'), revealed)
        self.assertEqual(result['status'], 'CODE_GENERATED')
        self.assertEqual(result['code'], 'BK7REAL')
        self.assertEqual(result['seen'][0]['page_odds'], 1.58)
        self.assertEqual(home, 'yes')

    def test_sure_odds_displayed_code_requires_qualification_after_price_drift(self):
        """A displayed code does not establish model validation or qualify a new price."""
        from datetime import datetime, timezone
        from sportybet_booking import _apply_displayed_prices, _store_displayed_code, _sure_odds_ticket
        moment = datetime(2026, 11, 8, 7, 53, tzinfo=timezone.utc).isoformat()
        holdout = '{"calibration_error":0.09,"sample_size":30}'
        with db.connect() as connection:
            connection.execute('DELETE FROM sports_ai_booking_codes')
            connection.execute('DELETE FROM sports_ai_accumulator_legs')
            connection.execute('DELETE FROM sports_ai_accumulator_tickets')
            connection.execute('DELETE FROM sports_ai_accumulator_runs')
            connection.execute("DELETE FROM sports_ai_market_registry WHERE sport='basketball' AND market_key='MONEYLINE_HOME'")
            connection.execute(
                """INSERT INTO sports_ai_market_registry
                   (id,sport,market_key,selection,period,line_value,settlement_rule,data_requirements,model_source,status,evidence_json,updated_at)
                   VALUES('holdout-ml-home','basketball','MONEYLINE_HOME','home','match',NULL,'moneyline','[]','test','active',?,?)""",
                (holdout, moment),
            )
            connection.execute(
                """INSERT INTO sports_ai_accumulator_runs
                   (id,config_id,sport_mode,status,trigger,idempotency_key,parameters_json,actor,started_at)
                   VALUES('sure-run','accumulator-default','basketball','complete','test','sure-run','{}','test',?)""",
                (moment,),
            )
            connection.execute(
                """INSERT INTO sports_ai_accumulator_tickets
                   (id,run_id,family_id,version,signature,tier,sport_mode,status,leg_count,probability_basis,
                    joint_probability,combined_odds,explanation,parameters_json,created_at)
                   VALUES('sure-ticket','sure-run','family',1,'sure-sig',2,'basketball','unverified_probability',2,'independent',
                          0.85,2.45,'test',?,?)""",
                (
                    '{"product":"sure_odds","band":"2-2.95","odds_band":"2.00–2.95","places_wager":false}',
                    moment,
                ),
            )
            for leg_id, opp, fixture, home, away, odds, prob in (
                ('leg-a', 'opp-a', 'fx-a', 'Home A', 'Away A', 1.40, 0.90),
                ('leg-b', 'opp-b', 'fx-b', 'Home B', 'Away B', 1.75, 0.88),
            ):
                connection.execute(
                    """INSERT INTO sports_ai_accumulator_legs
                       (id,ticket_id,opportunity_id,fixture_id,sport,competition,home,away,starts_at,market,selection,
                        decimal_odds,probability,bookmaker,odds_observed_at,quote_market,quote_outcome,model_id,
                        model_version,settlement_kind,result,qualification_json)
                       VALUES(?,?,?,?, 'basketball','Test',?,?,?,'MONEYLINE_HOME','home',?,?,'SportyBet',?,
                              'MONEYLINE_HOME','home','model','1','binary','pending','{}')""",
                    (leg_id, 'sure-ticket', opp, fixture, home, away, moment, odds, prob, moment),
                )
            self.assertTrue(_sure_odds_ticket(connection, 'sure-ticket'))
            _apply_displayed_prices(
                connection,
                'sure-ticket',
                [
                    {'fixture_id': 'fx-a', 'market': 'MONEYLINE_HOME', 'selection': 'home', 'page_odds': 1.55},
                    {'fixture_id': 'fx-b', 'market': 'MONEYLINE_HOME', 'selection': 'home', 'page_odds': 2.20},
                ],
            )
            self.assertFalse(_sure_odds_ticket(connection, 'sure-ticket'))
            with self.assertRaisesRegex(ValueError, 'active validated model'):
                _store_displayed_code(connection, 'sure-ticket', 'Q3XTSR', 'isolated sure-odds evidence')
            stored = connection.execute(
                "SELECT code, accumulator_ticket_id FROM sports_ai_booking_codes WHERE code='Q3XTSR'",
            ).fetchone()
            connection.execute("DELETE FROM sports_ai_booking_codes WHERE code='Q3XTSR'")
            connection.execute("DELETE FROM sports_ai_accumulator_legs WHERE ticket_id='sure-ticket'")
            connection.execute("DELETE FROM sports_ai_accumulator_tickets WHERE id='sure-ticket'")
            connection.execute("DELETE FROM sports_ai_accumulator_runs WHERE id='sure-run'")
            connection.execute("DELETE FROM sports_ai_market_registry WHERE sport='basketball' AND market_key='MONEYLINE_HOME'")
        self.assertIsNone(stored)

    def test_a_failed_job_does_not_take_the_current_ticket_slot(self):
        from datetime import datetime, timezone
        evening = datetime(2026, 11, 4, 16, 10, tzinfo=timezone.utc)
        with db.connect() as connection:
            connection.execute('DELETE FROM sports_ai_booking_jobs')
            connection.execute('DELETE FROM sports_ai_accumulator_tickets')
            connection.execute(
                """INSERT INTO sports_ai_booking_jobs
                   (id,created_at,updated_at,status,sport,selections_json,signature,places_wager,attempt,max_attempts,error)
                   VALUES('old-job',?,?, 'ODDS_CHANGED','football',?,'old-ticket|stale',0,1,3,'The displayed price no longer matches the qualified quote')""",
                (evening.isoformat(), evening.isoformat(), '[{"ticket_id":"old-ticket","home":"Old","away":"Side","market":"HOME","selection":"Old","decimal_odds":1.4}]'),
            )
            blocked = select_booking(connection, evening)
            connection.execute(
                """INSERT INTO sports_ai_accumulator_runs
                   (id,config_id,sport_mode,status,trigger,idempotency_key,parameters_json,actor,started_at)
                   VALUES('run','accumulator-default','football','complete','test','run','{}','test',?)""",
                (evening.isoformat(),),
            )
            connection.execute(
                """INSERT INTO sports_ai_accumulator_tickets
                   (id,run_id,family_id,version,signature,tier,sport_mode,status,leg_count,probability_basis,
                    joint_probability,explanation,parameters_json,created_at)
                   VALUES('current-ticket','run','family',1,'current-ticket',2,'football','qualified',1,'independent',
                          0.8,'test','{}',?)""",
                (evening.isoformat(),),
            )
            from category_support import register_category_card, unregister_category_card
            register_category_card(connection, 'current-ticket', '2-4.99', evening)
            chosen = select_booking(connection, evening)
            status = connection.execute("SELECT status FROM sports_ai_booking_jobs WHERE id='old-job'").fetchone()['status']
            unregister_category_card(connection, 'current-ticket')
            connection.execute("DELETE FROM sports_ai_accumulator_tickets WHERE id='current-ticket'")
            connection.execute("DELETE FROM sports_ai_accumulator_runs WHERE id='run'")
            connection.execute("DELETE FROM sports_ai_booking_jobs WHERE id='old-job'")
        self.assertEqual(blocked['action'], 'waiting')
        self.assertEqual(chosen['action'], 'ticket')
        self.assertEqual(chosen['ticket_id'], 'current-ticket')
        self.assertEqual(status, 'ODDS_CHANGED')



# These tests check booking, de-duplication and submission logic with Sure Odds data built for the
# earlier 2.00-2.95 range; that logic does not depend on the range, so the old range is pinned here.
# The current 2.50-2.95 / 2-4 selection rules are tested in test_three_objectives.py.
_OLD_RANGE = []


def setUpModule():
    from unittest.mock import patch as _patch
    import high_odds_board as _hb
    import single_betting as _sb
    for target, name, value in ((_hb, 'SURE_COMBINED_MIN', 2.0), (_hb, 'SURE_PACK_RANGE', (2.10, 2.80)),
                                (_sb, 'COMBINED_MIN_ODDS', 2.0)):
        pinned = _patch.object(target, name, value)
        pinned.start()
        _OLD_RANGE.append(pinned)


def tearDownModule():
    while _OLD_RANGE:
        _OLD_RANGE.pop().stop()

if __name__ == '__main__':
    unittest.main()
