"""Keep tests away from production artifacts.

The booking tests drive a local test page ("Pistons76ers ... PLCY42") through the
real booking flow. Without this, every run wrote that page's screenshot into
backend/booking_evidence, where it appeared as SportyBet evidence.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

_EVIDENCE = Path(tempfile.mkdtemp(prefix='sports-test-evidence-'))
# Set before any test module imports sportybet_booking, so the module default is safe too.
os.environ['SPORTS_BOOKING_EVIDENCE_DIR'] = str(_EVIDENCE)
# Account session files and screenshots never touch the real .secrets or account_evidence.
os.environ['SPORTS_SECRETS_DIR'] = str(_EVIDENCE / 'secrets')
os.environ['SPORTS_ACCOUNT_EVIDENCE_DIR'] = str(_EVIDENCE / 'account')


@pytest.fixture(autouse=True)
def _isolated_booking_evidence(tmp_path, monkeypatch):
    import sportybet_booking
    monkeypatch.setattr(sportybet_booking, 'EVIDENCE', tmp_path / 'booking_evidence')
    yield
