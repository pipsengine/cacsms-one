"""One active ticket per match, shared by every ticket product and refresh."""

import json
import re
import unicodedata
from datetime import datetime, timezone

ACTIVE = ('qualified', 'unverified_probability', 'awaiting_odds', 'review_required')


class TicketExposureConflict(ValueError):
    pass


def event_keys(leg):
    from prediction_engine import parse_time, _CLUB_PREFIXES, _TOKEN_FOLDS
    from sport_support import canonical_sport
    leg = dict(leg)
    keys = set()
    if leg.get('fixture_id'):
        keys.add('fixture:' + str(leg['fixture_id']))
    if leg.get('home') and leg.get('away') and leg.get('starts_at'):
        def team(name):
            value = ''.join(c for c in unicodedata.normalize('NFKD', str(name).casefold()) if not unicodedata.combining(c))
            tokens = re.findall(r'[a-z0-9]+', value)
            # Initials, team numbers and reserve suffixes distinguish events.
            return ' '.join(_TOKEN_FOLDS.get(token, token) for token in tokens if token not in _CLUB_PREFIXES) or value
        try:
            day = parse_time(leg['starts_at']).astimezone(timezone.utc).date().isoformat()
        except (TypeError, ValueError):
            day = str(leg['starts_at'])[:10]
        names = sorted((team(leg['home']), team(leg['away'])))
        keys.add('|'.join(('match', canonical_sport(leg.get('sport'), leg.get('competition')), day, *names)))
    return keys


def keys_for(legs):
    return set().union(*(event_keys(leg) for leg in legs)) if legs else set()


def open_events(connection, now=None, ignore_family=None):
    current = now or datetime.now(timezone.utc)
    rows = connection.execute(
        """SELECT l.*,t.family_id FROM sports_ai_accumulator_legs l
           JOIN sports_ai_accumulator_tickets t ON t.id=l.ticket_id
           WHERE (t.status IN ('qualified','unverified_probability','awaiting_odds','review_required')
             OR (t.status='expired'
               AND COALESCE(json_extract(t.parameters_json,'$.diversity_retired'),0)=0
               AND EXISTS (SELECT 1 FROM sports_ai_booking_codes b WHERE b.accumulator_ticket_id=t.id
                 AND b.verification_method='displayed' AND TRIM(COALESCE(b.code,''))<>'')
               AND NOT EXISTS (SELECT 1 FROM sports_ai_ticket_settlements s WHERE s.ticket_id=t.id)))
             AND COALESCE(json_extract(t.parameters_json,'$.tier_mode'),'standard')!='simulation'
             AND julianday(l.starts_at)>julianday(?)
             AND NOT EXISTS (SELECT 1 FROM sports_ai_accumulator_tickets newer
                 WHERE newer.family_id=t.family_id AND newer.version>t.version)""", (current.isoformat(),),
    )
    used = {}
    for row in rows:
        if ignore_family and row['family_id'] == ignore_family:
            continue
        for key in event_keys(row):
            used.setdefault(key, row['ticket_id'])
    return used


def unallocated(connection, legs, now=None, ignore_family=None):
    used = open_events(connection, now, ignore_family)
    return [leg for leg in legs if not event_keys(leg).intersection(used)]


def assert_available(connection, legs, now=None, ignore_family=None):
    # Serialize the check and subsequent insert, including competing API/worker
    # writers. No independent products can both claim the same unallocated game.
    if not connection.in_transaction:
        connection.execute('BEGIN IMMEDIATE')
    used = open_events(connection, now, ignore_family)
    seen = set()
    conflicts = set()
    for leg in legs:
        keys = event_keys(leg)
        if keys & seen:
            raise TicketExposureConflict('The same match appears more than once on this ticket.')
        conflicts.update(used[key] for key in keys & used.keys())
        seen.update(keys)
    if conflicts:
        raise TicketExposureConflict('A match is already allocated to another active ticket: ' + ', '.join(sorted(conflicts)))


def reconcile(connection, now=None):
    """Retire whole conflicting recommendations; preserve original legs and codes."""
    current = now or datetime.now(timezone.utc)
    if not connection.in_transaction:
        connection.execute('BEGIN IMMEDIATE')
    tickets = connection.execute(
        """SELECT t.*,
             (SELECT MIN(b.verified_at) FROM sports_ai_booking_codes b WHERE b.accumulator_ticket_id=t.id
               AND b.verification_method='displayed' AND TRIM(COALESCE(b.code,''))<>'') AS coded_at
           FROM sports_ai_accumulator_tickets t
           WHERE t.status IN ('qualified','unverified_probability','awaiting_odds','review_required')
             AND COALESCE(json_extract(t.parameters_json,'$.tier_mode'),'standard')!='simulation'
             AND NOT EXISTS (SELECT 1 FROM sports_ai_accumulator_tickets newer
               WHERE newer.family_id=t.family_id AND newer.version>t.version)
           ORDER BY CASE WHEN coded_at IS NOT NULL THEN 0 WHEN t.status='qualified' THEN 1 ELSE 2 END,
                    COALESCE(coded_at,t.created_at),t.id""",
    ).fetchall()
    occupied, retired = {}, []
    for ticket in tickets:
        legs = connection.execute(
            'SELECT * FROM sports_ai_accumulator_legs WHERE ticket_id=? AND julianday(starts_at)>julianday(?)',
            (ticket['id'], current.isoformat()),
        ).fetchall()
        keys = keys_for(legs)
        conflicts = sorted({occupied[key] for key in keys & occupied.keys()})
        from booking_policy import load as booking_policy
        # A displayed code is fixed and already handed out; sharing matches is allowed by the policy.
        if conflicts and (ticket['coded_at'] or not booking_policy(connection)['enforce_match_exposure']):
            conflicts = []
        if conflicts:
            parameters = json.loads(ticket['parameters_json'] or '{}')
            parameters.update({'diversity_retired': True, 'conflicts_with': conflicts})
            reason = 'Retired from recommendations because a match is already on another active ticket. Original legs and booking-code evidence are preserved.'
            connection.execute(
                """UPDATE sports_ai_accumulator_tickets SET status='expired',publication_state='DIVERSITY_RETIRED',
                   parameters_json=?,explanation=? WHERE id=?""", (json.dumps(parameters), reason, ticket['id']),
            )
            connection.execute(
                """INSERT INTO sports_ai_ticket_monitoring_events(id,ticket_id,event_type,details_json,created_at)
                   VALUES(lower(hex(randomblob(16))),?,'diversity_retired',?,?)""",
                (ticket['id'], json.dumps({'conflicts_with': conflicts, 'reason': reason}), current.isoformat()),
            )
            connection.execute(
                """UPDATE sports_ai_booking_jobs SET status='FAILED',reason_code='FIXTURE_EXPOSURE',error=?,updated_at=?
                   WHERE ticket_id=? AND status='QUEUED'""", (reason, current.isoformat(), ticket['id']),
            )
            retired.append({'ticket_id': ticket['id'], 'conflicts_with': conflicts})
        else:
            occupied.update({key: ticket['id'] for key in keys})
    return {'retired': retired, 'retired_count': len(retired), 'policy': 'one_active_ticket_per_match'}
