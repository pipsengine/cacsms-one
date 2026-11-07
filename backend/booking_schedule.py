"""West Africa Time windows for discovery, qualification and booking-code publication.

WAT is UTC+1 with no daylight-saving change. During the testing period a code
may be requested every 15 minutes. A code is stored only when SportyBet displays it.
"""

from datetime import datetime, timedelta, timezone

WAT = timezone(timedelta(hours=1))
WINDOWS = (
    {'id': 'discovery', 'label': 'Overnight discovery', 'start': '00:00', 'end': '04:00', 'publishes': False},
    {'id': 'qualification', 'label': 'Qualification', 'start': '05:00', 'end': '06:30', 'publishes': False},
    {'id': 'publication', 'label': 'Primary publication', 'start': '07:00', 'end': '08:00', 'publishes': True},
    {'id': 'midday', 'label': 'Midday reassessment', 'start': '12:00', 'end': '13:00', 'publishes': True},
    {'id': 'evening', 'label': 'Evening reassessment', 'start': '17:00', 'end': '18:00', 'publishes': True},
)


def _minute(value):
    hour, minute = value.split(':')
    return int(hour) * 60 + int(minute)


def opportunity_week(now=None):
    """Monday 00:00 WAT through the next Monday 00:00.

    The end is exclusive, so a kickoff on Sunday is inside the week and a
    kickoff on the following Monday is the next week.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local = current.astimezone(WAT)
    monday = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return monday, monday + timedelta(days=7)


def phase_at(now):
    """Return the active window. Minutes outside those ranges are event-driven."""
    current = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    local = current.astimezone(WAT)
    minute = local.hour * 60 + local.minute
    for window in WINDOWS:
        if _minute(window['start']) <= minute <= _minute(window['end']):
            return window['id']
    return 'event'


PUBLICATION_EVERY = timedelta(minutes=15)


def publication_open(phase):
    return phase in {window['id'] for window in WINDOWS if window['publishes']}


def last_publication_at(connection):
    """When the latest queued, preparing, or displayed-code request was stored."""
    row = connection.execute(
        """SELECT MAX(created_at) AS created_at FROM sports_ai_booking_jobs
           WHERE status IN ('QUEUED','PREPARING','CODE_GENERATED')""",
    ).fetchone()
    if not row or not row['created_at']:
        return None
    try:
        parsed = datetime.fromisoformat(str(row['created_at']).replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def publication_due(now, last_attempt=None):
    """Testing period: one code request every 15 minutes, including outside the named windows."""
    if last_attempt is None:
        return True
    current = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    previous = last_attempt if last_attempt.tzinfo else last_attempt.replace(tzinfo=timezone.utc)
    return current - previous >= PUBLICATION_EVERY


def next_publication(now, last_attempt=None):
    """The next Africa/Lagos minute when a booking code may be requested."""
    current = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    local = current.astimezone(WAT).replace(second=0, microsecond=0)
    if last_attempt is not None and last_attempt.tzinfo is None:
        last_attempt = last_attempt.replace(tzinfo=timezone.utc)
    if publication_due(current, last_attempt):
        return local.isoformat(timespec='minutes')
    due = (last_attempt.astimezone(WAT) + PUBLICATION_EVERY).replace(second=0, microsecond=0)
    return due.isoformat(timespec='minutes')


def describe(now, last_attempt=None):
    phase = phase_at(now)
    current = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    local = current.astimezone(WAT)
    return {
        'timezone': 'Africa/Lagos',
        'offset': '+01:00',
        'local_time': local.isoformat(timespec='minutes'),
        'phase': phase,
        'publishes': publication_due(current, last_attempt),
        'next_publication_at': next_publication(current, last_attempt),
        'places_wager': False,
        'windows': WINDOWS,
        'publication_every_minutes': int(PUBLICATION_EVERY.total_seconds() // 60),
        'note': (
            'Testing period: a booking code is requested every 15 minutes. '
            'A code is stored only when SportyBet displays it. No wager is placed.'
        ),
    }
