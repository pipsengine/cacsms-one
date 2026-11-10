"""Test-only helpers for suites that exercise booking and ticket mechanics.

Those suites cover other behaviour than capability promotion, the same way they
already call ``market_anchor.set_model_only``. ``eligible_everywhere`` resolves
every sport-league-market as ELIGIBLE with strong settled evidence, so the
probability record, integrity and conservative-EV gates still run unchanged.
``certify_qualified`` gives decisions a test inserted directly the immutable
probability record that revalidate() would have written.
Nothing here is imported by production code.
"""
import json
from unittest.mock import patch

import prediction_integrity as pi

ELIGIBLE = {
    'level': 'ELIGIBLE', 'scope': 'test', 'reason': 'Granted by the test suite.', 'evidence': {},
    'sport_evidence': {'observations': 100000, 'calibration_error': 0.0},
}


def eligible_everywhere(testcase):
    """Resolve every capability as ELIGIBLE; certify directly inserted decisions before the real booking gate."""
    original = pi.require_bookable

    def gate(connection, selections, now=None, record_booking=True):
        certify_qualified(connection)
        return original(connection, selections, now, record_booking)

    for patcher in (patch.object(pi, 'capability_for', lambda *args, **kwargs: dict(ELIGIBLE)),
                    patch.object(pi, 'require_bookable', gate)):
        patcher.start()
        testcase.addCleanup(patcher.stop)


def certify_qualified(connection):
    """Write a clean ELIGIBLE probability record for each QUALIFIED decision that lacks one."""
    rows = connection.execute(
        """SELECT d.id,d.prediction_id,d.fixture_id,d.sport,d.competition,d.market,d.evidence_json,d.opportunity_id,
                  p.probability,p.model_id,m.version AS model_version
           FROM sports_ai_qualification_decisions d
           LEFT JOIN sports_ai_engine_predictions p ON p.id=d.prediction_id
           LEFT JOIN sports_ai_model_registry m ON m.id=p.model_id
           WHERE d.state='QUALIFIED'""",
    ).fetchall()
    for row in rows:
        evidence = json.loads(row['evidence_json'] or '{}')
        if evidence.get('probability_record_id'):
            continue
        quote = pi._quote_for(connection, row['opportunity_id']) if row['opportunity_id'] else None
        if quote is None:
            implied = evidence.get('implied_probability')
            quote = {'id': None, 'bookmaker': 'SportyBet', 'decimal_odds': (1 / implied) if implied else 2.0,
                     'observed_at': None}
        probability = evidence.get('probability') if evidence.get('probability') is not None else row['probability']
        probability = 0.6 if probability is None else float(probability)
        odds = float(quote['decimal_odds'])
        evidence.update(probability=probability, model_probability=probability, break_even=1 / odds,
                        uncertainty_margin=0.0, conservative_probability=probability,
                        expected_value=probability * odds - 1, conservative_ev=probability * odds - 1)
        item = {'prediction_id': row['prediction_id'], 'fixture_id': row['fixture_id'], 'sport': row['sport'],
                'competition': row['competition'], 'market': row['market'], 'model_id': row['model_id'],
                'model_version': row['model_version']}
        record = pi.record_probability(connection, item, quote, evidence, 'ok', [], ELIGIBLE, pi.load_policy(connection))
        evidence['probability_record_id'] = record
        connection.execute('UPDATE sports_ai_qualification_decisions SET evidence_json=? WHERE id=?',
                           (json.dumps(evidence), row['id']))
