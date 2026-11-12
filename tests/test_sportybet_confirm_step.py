"""SportyBet's in-betslip payment step ("About to pay NGN 100.00 · Cancel · Confirm").

Seen on 12 Nov 2026: after Place Bet, SportyBet showed this panel inside the betslip, not as a
dialog. The engine did not see it, never confirmed, and paused with an unclear submission.
POPUP_STATE is run here in real Chromium against a copy of that betslip.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import sportybet_account as acct

SLIP = '''<html><body>
<div class="m-betslip-wrapper" style="width:300px">
  <div class="m-betslip">
    <div>Betslip 1 Cashout 12</div>
    <div>Under 3.5 Thailand v Qatar Over/Under 1.30</div>
    <div>Total Stake NGN 100 Potential Win 130.00</div>
    <div class="pay-panel"><p>About to pay</p><p>NGN {amount}</p>
      <button>Cancel</button><button>Confirm</button></div>
  </div>
</div></body></html>'''


def run(html):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(html)
            state = page.evaluate(acct.POPUP_STATE)
            marked = page.locator('[data-sports-ai-confirm="1"]').count()
            return state, marked
        finally:
            browser.close()


class ConfirmStepTests(unittest.TestCase):
    def setUp(self):
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            self.skipTest('playwright is not installed')

    def test_in_slip_payment_step_for_100_is_found_and_confirmable(self):
        state, marked = run(SLIP.format(amount='100.00'))
        self.assertEqual(state['confirm_buttons'], 1)
        self.assertEqual(marked, 1)
        self.assertTrue(state['shows_stake'])
        self.assertFalse(state['success'])

    def test_payment_step_for_another_amount_is_never_confirmed(self):
        for amount in ('1,000.00', '100.50', '200.00'):
            state, _ = run(SLIP.format(amount=amount))
            self.assertEqual(state['confirm_buttons'], 1)
            self.assertFalse(state['shows_stake'], amount)

    def test_slip_without_payment_step_has_nothing_to_confirm(self):
        state, marked = run(SLIP.replace('<div class="pay-panel"><p>About to pay</p><p>NGN {amount}</p>\n      <button>Cancel</button><button>Confirm</button></div>', ''))
        self.assertEqual((state['confirm_buttons'], marked), (0, 0))


if __name__ == '__main__':
    unittest.main()
