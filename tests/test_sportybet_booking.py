import asyncio
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import db
from sportybet_booking import create_job, prepare_slip, process_booking_queue, qualified_selections

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
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.previous = db.DB
        db.DB = Path(cls.temp.name) / 'sports.sqlite3'
        db.initialize()
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_address[1]}/'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        db.DB = cls.previous
        cls.temp.cleanup()

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

    def test_duplicate_qualified_set_is_not_queued_twice(self):
        with db.connect() as connection:
            first = create_job(connection, [self.selection()])
            second = create_job(connection, [self.selection()])
        self.assertFalse(first['duplicate'])
        self.assertTrue(second['duplicate'])
        self.assertEqual(first['id'], second['id'])

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

    def test_changed_price_does_not_request_a_code(self):
        from playwright.async_api import async_playwright
        Handler.html = PAGE.replace('__HOME__', '1.95')

        async def run():
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                page = await (await browser.new_context()).new_page()
                await page.goto(self.url)
                result = await prepare_slip(page, [self.selection()])
                booked = await page.locator('body').get_attribute('data-booked')
                placed = await page.locator('body').get_attribute('data-placed')
                await browser.close()
                return result, booked, placed

        try:
            result, booked, placed = asyncio.run(run())
        finally:
            Handler.html = PAGE.replace('__HOME__', '1.80')
        self.assertEqual(result['status'], 'ODDS_CHANGED')
        self.assertIsNone(result['code'])
        self.assertEqual(booked, 'no')
        self.assertEqual(placed, 'no')


if __name__ == '__main__':
    unittest.main()
