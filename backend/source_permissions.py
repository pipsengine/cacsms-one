"""Source permissions: an approved terms decision plus robots.txt, per host.

A collector may open a URL only when its host is approved and the host's
robots.txt allows the path for our user agent. Every decision is written to
``sports_ai_source_permission_checks`` for the audit trail.

A host is approved by one of:
* an approved row in ``sports_ai_source_permissions`` (operator terms review),
* SPORTS_AI_AUTHORIZED_DOMAINS (operator configuration), or
* for SportyBet, the stored unattended collection authorization.

Nothing here discovers or approves sources by itself.
"""
import os
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib import robotparser
from urllib.parse import urlsplit

from db import connect

USER_AGENT = 'cacsms-sports-ai/1.0'
ROBOTS_MAX_AGE = timedelta(hours=24)
# Keep the last good robots.txt this long when the host cannot be reached.
ROBOTS_STALE_GRACE = timedelta(days=7)


class SourceNotPermitted(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc)


def _host(url):
    return (urlsplit(url).hostname or '').casefold().rstrip('.')


def _env_allowlist():
    return {item.strip().casefold() for item in os.environ.get('SPORTS_AI_AUTHORIZED_DOMAINS', '').split(',') if item.strip()}


def _fetch_robots(host, timeout=15):
    """Return (status, text). A 4xx means there is no robots.txt, so nothing is disallowed."""
    request = urllib.request.Request(f'https://{host}/robots.txt', headers={'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 'present', response.read(512_000).decode('utf-8', 'replace')
    except urllib.error.HTTPError as error:
        if 400 <= error.code < 500:
            return 'absent', ''
        return 'unreachable', None
    except (urllib.error.URLError, TimeoutError, OSError):
        return 'unreachable', None


def terms_decision(connection, host):
    """Return (approved, approved_by)."""
    row = connection.execute(
        'SELECT approved,approved_by FROM sports_ai_source_permissions WHERE lower(hostname)=?', (host,),
    ).fetchone()
    if row and row['approved']:
        return True, row['approved_by'] or 'terms_review'
    if row and not row['approved']:
        return False, None  # an explicit review that did not approve wins over configuration
    if host in _env_allowlist():
        return True, 'env:SPORTS_AI_AUTHORIZED_DOMAINS'
    if host == 'sportybet.com' or host.endswith('.sportybet.com'):
        grant = connection.execute(
            """SELECT actor FROM sports_ai_collection_authorizations
               WHERE bookmaker='SportyBet' AND status='authorized' ORDER BY created_at DESC LIMIT 1""",
        ).fetchone()
        if grant:
            return True, f"collection_authorization:{grant['actor']}"
    return False, None


def _robots(connection, host, now, fetcher):
    row = connection.execute('SELECT * FROM sports_ai_source_robots WHERE host=?', (host,)).fetchone()
    row = dict(row) if row else None
    if row and row['robots_status'] != 'unreachable' and datetime.fromisoformat(row['checked_at']) > now - ROBOTS_MAX_AGE:
        return row
    status, text = fetcher(host)
    if status == 'unreachable' and row and row['robots_status'] != 'unreachable' \
            and datetime.fromisoformat(row['checked_at']) > now - ROBOTS_STALE_GRACE:
        return row  # keep the last good copy through a short outage
    connection.execute(
        """INSERT INTO sports_ai_source_robots(host,robots_status,robots_txt,checked_at) VALUES(?,?,?,?)
           ON CONFLICT(host) DO UPDATE SET robots_status=excluded.robots_status,
             robots_txt=excluded.robots_txt,checked_at=excluded.checked_at""",
        (host, status, text, now.isoformat()),
    )
    return {'host': host, 'robots_status': status, 'robots_txt': text, 'checked_at': now.isoformat()}


def robots_allows(robots_txt, url, user_agent=USER_AGENT):
    parser = robotparser.RobotFileParser()
    parser.parse((robots_txt or '').splitlines())
    return parser.can_fetch(user_agent, url)


def evaluate(connection, url, purpose='collection', now=None, fetcher=_fetch_robots):
    """Return (allowed, reason) and record the decision."""
    current = now or _now()
    host = _host(url)
    if not host:
        allowed, reason = False, 'URL has no host'
    else:
        approved, _by = terms_decision(connection, host)
        if not approved:
            allowed, reason = False, 'Host has no approved terms review'
        else:
            robots = _robots(connection, host, current, fetcher)
            if robots['robots_status'] == 'unreachable':
                allowed, reason = False, 'robots.txt could not be read, so the path is not known to be allowed'
            elif robots['robots_status'] == 'absent':
                allowed, reason = True, 'Approved host without a robots.txt'
            elif robots_allows(robots['robots_txt'], url):
                allowed, reason = True, 'Approved host and robots.txt allows this path'
            else:
                allowed, reason = False, 'robots.txt disallows this path'
    connection.execute(
        """INSERT INTO sports_ai_source_permission_checks(id,host,url,purpose,allowed,reason,created_at)
           VALUES(?,?,?,?,?,?,?)""",
        (str(uuid.uuid4()), host, url[:500], purpose, int(allowed), reason, current.isoformat()),
    )
    return allowed, reason


def require(url, purpose='collection', connection=None, now=None, fetcher=None):
    fetcher = fetcher or _fetch_robots
    if connection is None:
        with connect() as owned:
            allowed, reason = evaluate(owned, url, purpose, now, fetcher)
    else:
        allowed, reason = evaluate(connection, url, purpose, now, fetcher)
    if not allowed:
        raise SourceNotPermitted(f'{reason}: {url}')
    return reason


def record_review(connection, host, approved, reviewer, terms_url, notes='', now=None):
    """Store an operator terms review in the existing permissions table."""
    if not reviewer:
        raise ValueError('A reviewer is required')
    if not terms_url or not terms_url.startswith('https://'):
        raise ValueError('An https terms URL is required')
    current = (now or _now()).isoformat()
    host = host.casefold().rstrip('.')
    connection.execute(
        """INSERT INTO sports_ai_source_permissions(id,hostname,terms_url,notes,approved,approved_by,reviewed_at)
           VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(hostname) DO UPDATE SET terms_url=excluded.terms_url,notes=excluded.notes,
             approved=excluded.approved,approved_by=excluded.approved_by,reviewed_at=excluded.reviewed_at""",
        (str(uuid.uuid4()), host, terms_url, notes or '', int(bool(approved)), reviewer, current),
    )
    return dict(connection.execute('SELECT * FROM sports_ai_source_permissions WHERE hostname=?', (host,)).fetchone())


def source_health(connection, now=None):
    """Every known host with its terms decision, robots state and the last day of decisions."""
    current = now or _now()
    since = (current - timedelta(days=1)).isoformat()
    hosts = {r['hostname'].casefold() for r in connection.execute('SELECT hostname FROM sports_ai_source_permissions')}
    hosts |= {r[0] for r in connection.execute('SELECT DISTINCT host FROM sports_ai_source_permission_checks WHERE host<>""')}
    hosts |= {r[0] for r in connection.execute('SELECT host FROM sports_ai_source_robots')}
    rows = []
    for host in sorted(hosts):
        approved, by = terms_decision(connection, host)
        robots = connection.execute('SELECT robots_status,checked_at FROM sports_ai_source_robots WHERE host=?', (host,)).fetchone()
        stats = connection.execute(
            """SELECT SUM(allowed) allowed, SUM(1-allowed) denied, MAX(created_at) last_check
               FROM sports_ai_source_permission_checks WHERE host=? AND created_at>=?""", (host, since),
        ).fetchone()
        rows.append({
            'host': host, 'approved': approved, 'approved_by': by,
            'robots_status': robots['robots_status'] if robots else 'unchecked',
            'robots_checked_at': robots['checked_at'] if robots else None,
            'checks_allowed': stats['allowed'] or 0, 'checks_denied': stats['denied'] or 0,
            'last_check': stats['last_check'],
        })
    denials = [dict(r) for r in connection.execute(
        """SELECT host,url,purpose,reason,created_at FROM sports_ai_source_permission_checks
           WHERE allowed=0 ORDER BY created_at DESC LIMIT 20""",
    )]
    return {'hosts': rows, 'recent_denials': denials, 'generated_at': current.isoformat()}
