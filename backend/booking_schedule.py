"""West Africa Time windows for discovery, qualification and booking-code publication.

WAT is UTC+1 with no daylight-saving change. A code is requested only inside a
publication window. Other hours stay event-driven and do not open a betslip.
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


def publication_open(phase):
    return phase in {window['id'] for window in WINDOWS if window['publishes']}


def next_publication(now):
    """The next Africa/Lagos minute when a booking code may be requested."""
    current = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    local = current.astimezone(WAT).replace(second=0, microsecond=0)
    if publication_open(phase_at(current)):
        return local.isoformat(timespec='minutes')
    start = local.replace(hour=0, minute=0)
    for day in (0, 1):
        for window in WINDOWS:
            if not window['publishes']:
                continue
            hour, minute = (int(part) for part in window['start'].split(':'))
            moment = start + timedelta(days=day, hours=hour, minutes=minute)
            if moment > local:
                return moment.isoformat(timespec='minutes')
    return None


def describe(now):
    phase = phase_at(now)
    current = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    local = current.astimezone(WAT)
    return {
        'timezone': 'Africa/Lagos',
        'offset': '+01:00',
        'local_time': local.isoformat(timespec='minutes'),
        'phase': phase,
        'publishes': publication_open(phase),
        'next_publication_at': next_publication(current),
        'places_wager': False,
        'windows': WINDOWS,
        'note': (
            'Booking codes are requested only from 07:00–08:00, 12:00–13:00 and 17:00–18:00 WAT, '
            'and only for tickets that are already qualified. A code is stored only when SportyBet displays it. '
            'Hours outside the named windows update qualification and expiry without opening a betslip.'
        ),
    }
