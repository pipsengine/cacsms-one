"""Apply a provider's correction of its own final score.

A result_conflict is raised whenever a stored final score differs from an incoming one.
When the incoming score comes from the same provider that supplied the stored score, it is
that provider correcting itself (a stat correction, an overtime update), not two sources
disagreeing. Left open, the conflict keeps the Settle stage in Attention indefinitely.

The correction is applied automatically only when nothing has been graded on the fixture.
A disagreement between different providers, or any fixture with tickets, singles, rollover
days, draw picks or basketball bet selections, still waits for an operator.
"""
import json
import uuid
from datetime import datetime, timezone

GRADED_TABLES = (
    'sports_ai_accumulator_legs', 'sports_ai_ticket_legs', 'sports_ai_single_bets',
    'sports_ai_rollover_days', 'sports_ai_draw_fixtures', 'sports_ai_weekly_draw_watch',
    'sports_ai_basketball_bet_selections', 'sports_ai_single_bet_legs',
)


def _graded(connection, fixture_id):
    present = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in GRADED_TABLES:
        if table in present and connection.execute(
            f'SELECT 1 FROM {table} WHERE fixture_id=? LIMIT 1', (fixture_id,),
        ).fetchone():
            return table
    return None


def apply_self_corrections(connection, now=None):
    current = (now or datetime.now(timezone.utc)).isoformat()
    applied, held = [], []
    issues = connection.execute(
        """SELECT i.id, i.fixture_id, i.details_json, r.home_score, r.away_score, r.source
           FROM sports_ai_data_quality_issues i
           JOIN sports_ai_results r ON r.fixture_id=i.fixture_id
           WHERE i.issue_type='result_conflict' AND i.resolved_at IS NULL""",
    ).fetchall()
    for issue in issues:
        try:
            detail = json.loads(issue['details_json'] or '{}')
            stored, incoming = list(detail['stored']), list(detail['incoming'])
        except (KeyError, TypeError, ValueError):
            continue
        provider = str(detail.get('provider_key') or '')
        if not provider or provider != str(issue['source'] or ''):
            continue  # two different sources disagree: an operator decides
        if [issue['home_score'], issue['away_score']] != stored:
            continue  # the stored score changed since the issue was raised
        if len(incoming) != 2 or any(not isinstance(v, int) or v < 0 for v in incoming):
            continue
        blocker = _graded(connection, issue['fixture_id'])
        if blocker:
            held.append({'issue_id': issue['id'], 'graded_in': blocker})
            continue
        connection.execute(
            'UPDATE sports_ai_results SET home_score=?, away_score=? WHERE fixture_id=?',
            (incoming[0], incoming[1], issue['fixture_id']),
        )
        connection.execute(
            'UPDATE sports_ai_data_quality_issues SET resolved_at=? WHERE id=? AND resolved_at IS NULL',
            (current, issue['id']),
        )
        connection.execute(
            'INSERT INTO sports_ai_audit_events(id,actor,action,metadata_json,created_at) VALUES(?,?,?,?,?)',
            (str(uuid.uuid4()), 'sports-ai-worker', 'result.self_correction', json.dumps({
                'fixture_id': issue['fixture_id'], 'provider': provider, 'from': stored, 'to': incoming,
                'issue_id': issue['id'], 'reason': 'Same provider corrected its own final score; nothing graded on it.',
            }), current),
        )
        applied.append({'fixture_id': issue['fixture_id'], 'from': stored, 'to': incoming})
    return {'applied': applied, 'held': held}
