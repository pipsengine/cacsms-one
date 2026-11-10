"""Universal intelligence read models across every sport, league and market.

This module sits on top of the existing pipeline (collection, models, market
registry, qualification and booking). It adds four things the pipeline did not
expose in one place:

* a research-source registry for the named public sites, with availability,
  coverage, freshness, reliability and provenance per source;
* a capability catalogue that registers every sport seen in collected fixtures
  and states, per sport, whether a validated model and active markets exist;
* a cross-sport opportunity board ranked by conservative expected value, with
  one verdict per fixture ("NO QUALIFIED OPPORTUNITY" when nothing passes);
* an all-market analyzer for one fixture: every stored model probability and
  every stored bookmaker quote, with break-even probability and edge.

Everything here reads stored rows. It never fetches a page, invents a price or
probability, or registers a booking code. Source access stays gated by
``source_permissions`` (terms review + robots.txt); a site listed here is not
thereby approved.
"""
import json
import math
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

PREFERRED_ODDS = (1.70, 2.50)
CONFIDENCE_Z = 1.645  # one-sided 95% bound on the holdout hit rate
STATE_ORDER = {'QUALIFIED': 0, 'MONITORING': 1, 'AWAITING_DATA': 2, 'SUSPENDED': 3, 'REJECTED': 4, 'EXPIRED': 5}
STATES = tuple(STATE_ORDER)
NO_OPPORTUNITY = 'NO QUALIFIED OPPORTUNITY'
MAX_BOARD_ROWS = 5000

RESEARCH_SOURCES = (
    {
        'key': 'flashscore', 'name': 'Flashscore', 'url': 'https://www.flashscore.com/',
        'hosts': ('www.flashscore.com', 'flashscore.com'), 'provider_keys': (), 'sports': 'all',
        'responsibilities': ['Historical results', 'Fixtures', 'Standings', 'Match statistics',
                             'Set/quarter/period scores', 'Form', 'Head-to-head'],
    },
    {
        'key': 'sofascore', 'name': 'Sofascore', 'url': 'https://www.sofascore.com/',
        'hosts': ('www.sofascore.com', 'sofascore.com', 'api.sofascore.com'), 'provider_keys': (), 'sports': 'all',
        'responsibilities': ['Match statistics', 'Team/player performance', 'Standings', 'Schedules',
                             'Form', 'Event information'],
    },
    {
        'key': 'betexplorer', 'name': 'BetExplorer', 'url': 'https://www.betexplorer.com/',
        'hosts': ('www.betexplorer.com', 'betexplorer.com'), 'provider_keys': ('betexplorer',), 'sports': 'all',
        'responsibilities': ['Historical results', 'Head-to-head', 'Standings', 'Bookmaker odds',
                             'Market comparisons'],
    },
    {
        'key': 'oddsportal', 'name': 'OddsPortal', 'url': 'https://www.oddsportal.com/',
        'hosts': ('www.oddsportal.com', 'oddsportal.com'), 'provider_keys': (), 'sports': 'all',
        'responsibilities': ['Odds comparisons', 'Available markets', 'Handicaps', 'Totals',
                             'Historical prices'],
    },
    {
        'key': 'forebet', 'name': 'Forebet', 'url': 'https://www.forebet.com/',
        'hosts': ('www.forebet.com', 'forebet.com'), 'provider_keys': (), 'sports': 'all',
        'responsibilities': ['External statistical forecasts (comparison signal only, never a model input '
                             'without its own holdout)'],
    },
    {
        'key': 'asia_basket', 'name': 'Asia-Basket', 'url': 'https://www.asia-basket.com/',
        'hosts': ('www.asia-basket.com', 'asia-basket.com'), 'provider_keys': (), 'sports': 'basketball',
        'responsibilities': ['Basketball results', 'Team records', 'League standings', 'Competition information'],
    },
    {
        'key': 'sportybet', 'name': 'SportyBet', 'url': 'https://www.sportybet.com/',
        'hosts': ('www.sportybet.com', 'sportybet.com'), 'provider_keys': ('sportybet',), 'sports': 'all',
        'responsibilities': ['Target-bookmaker fixture matching', 'Available markets', 'Current odds',
                             'Genuine booking-code verification'],
    },
)


def _now(now=None):
    return now or datetime.now(timezone.utc)


def _loads(value, fallback):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value) if value else fallback
    except (TypeError, ValueError):
        return fallback


def _float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _host(url):
    return (urlsplit(url or '').hostname or '').casefold().rstrip('.')


def _freshness(moment, now):
    if moment is None:
        return 'never'
    age = now - moment
    if age <= timedelta(hours=6):
        return 'fresh'
    if age <= timedelta(hours=48):
        return 'stale'
    return 'outdated'


def _table_exists(connection, name):
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


# --------------------------------------------------------------------------- sources

def _source_entry(connection, source, now, since):
    from source_permissions import terms_decision

    hosts = tuple(h.casefold() for h in source['hosts'])
    primary = hosts[0]
    approved, approved_by = False, None
    for host in hosts:
        approved, approved_by = terms_decision(connection, host)
        if approved:
            primary = host
            break
    marks = ','.join('?' * len(hosts))
    review = connection.execute(
        f"""SELECT hostname,terms_url,approved,approved_by,reviewed_at,notes FROM sports_ai_source_permissions
            WHERE lower(hostname) IN ({marks}) ORDER BY reviewed_at DESC LIMIT 1""", hosts,
    ).fetchone()
    robots = connection.execute(
        f'SELECT robots_status,checked_at FROM sports_ai_source_robots WHERE host IN ({marks}) ORDER BY checked_at DESC LIMIT 1',
        hosts,
    ).fetchone()
    checks = connection.execute(
        f"""SELECT COALESCE(SUM(allowed),0) allowed, COALESCE(SUM(1-allowed),0) denied,
                   MAX(CASE WHEN allowed=1 THEN created_at END) last_allowed
            FROM sports_ai_source_permission_checks WHERE host IN ({marks}) AND created_at>=?""",
        (*hosts, since),
    ).fetchone()
    jobs = {'complete': 0, 'failed': 0, 'saved': 0}
    last_success = None
    providers = []
    if source['provider_keys']:
        keys = source['provider_keys']
        pmarks = ','.join('?' * len(keys))
        for row in connection.execute(
            f"""SELECT id,provider_key,display_name,enabled,terms_reviewed,status,last_success_at,last_error
                FROM sports_ai_provider_registry WHERE provider_key IN ({pmarks})""", keys,
        ):
            providers.append(dict(row))
            moment = _parse(row['last_success_at'])
            if moment and (last_success is None or moment > last_success):
                last_success = moment
            stat = connection.execute(
                """SELECT COALESCE(SUM(status IN ('complete','partial')),0) ok, COALESCE(SUM(status='failed'),0) bad,
                          COALESCE(SUM(records_saved),0) saved
                   FROM sports_ai_acquisition_jobs WHERE provider_id=? AND started_at>=?""", (row['id'], since),
            ).fetchone()
            jobs['complete'] += stat['ok'] or 0
            jobs['failed'] += stat['bad'] or 0
            jobs['saved'] += stat['saved'] or 0
    last_allowed = _parse(checks['last_allowed'])
    latest = max((m for m in (last_allowed, last_success) if m), default=None)
    attempts = (checks['allowed'] or 0) + (checks['denied'] or 0) + jobs['complete'] + jobs['failed']
    successes = (checks['allowed'] or 0) + jobs['complete']
    reliability = round(successes / attempts, 4) if attempts else None
    if review is not None and not review['approved']:
        availability = 'declined_by_review'
    elif not approved:
        availability = 'awaiting_terms_review'
    elif robots is None:
        availability = 'approved_robots_unchecked'
    elif robots['robots_status'] == 'unreachable':
        availability = 'approved_robots_unreachable'
    else:
        availability = 'available'
    return {
        'key': source['key'], 'name': source['name'], 'url': source['url'], 'hosts': list(hosts),
        'sports': source['sports'], 'responsibilities': source['responsibilities'],
        'registered': 'catalogue',
        'availability': availability,
        'approved': approved,
        'coverage': {
            'pages_permitted_7d': checks['allowed'] or 0,
            'pages_denied_7d': checks['denied'] or 0,
            'collection_jobs_7d': jobs['complete'] + jobs['failed'],
            'records_saved_7d': jobs['saved'],
            'providers': providers,
        },
        'freshness': _freshness(latest, now),
        'last_activity_at': latest.isoformat() if latest else None,
        'reliability': reliability,
        'provenance': {
            'approved_by': approved_by,
            'terms_url': review['terms_url'] if review else None,
            'reviewed_at': review['reviewed_at'] if review else None,
            'review_notes': review['notes'] if review else None,
            'robots_status': robots['robots_status'] if robots else 'unchecked',
            'robots_checked_at': robots['checked_at'] if robots else None,
            'primary_host': primary,
        },
    }


def research_sources(connection, now=None):
    """The named sources plus every other host an operator has approved."""
    current = _now(now)
    since = (current - timedelta(days=7)).isoformat()
    catalogue = list(RESEARCH_SOURCES)
    known = {h for s in catalogue for h in s['hosts']}
    for row in connection.execute(
        'SELECT hostname,terms_url FROM sports_ai_source_permissions WHERE approved=1 ORDER BY hostname',
    ):
        host = row['hostname'].casefold().rstrip('.')
        if host in known or host.removeprefix('www.') in known or f'www.{host}' in known:
            continue
        known.add(host)
        catalogue.append({
            'key': host, 'name': host, 'url': f'https://{host}/', 'hosts': (host,), 'provider_keys': (),
            'sports': 'unspecified', 'responsibilities': ['Operator-approved research source'],
            '_extension': True,
        })
    items = []
    for source in catalogue:
        entry = _source_entry(connection, source, current, since)
        if source.get('_extension'):
            entry['registered'] = 'operator_approval'
        items.append(entry)
    summary = {
        'total': len(items),
        'available': sum(1 for i in items if i['availability'] == 'available'),
        'awaiting_review': sum(1 for i in items if i['availability'] == 'awaiting_terms_review'),
        'declined': sum(1 for i in items if i['availability'] == 'declined_by_review'),
    }
    return {
        'sources': items, 'summary': summary, 'generated_at': current.isoformat(),
        'policy': 'A source is opened only after an approved terms review and a robots.txt check. '
                  'CAPTCHA, login walls and access controls stop collection; nothing here bypasses them.',
    }


# --------------------------------------------------------------------------- capability catalogue

def register_discovered_sports(connection):
    """Register every collected sport code. A new code is evidence-only until a model validates."""
    from sport_support import canonical_sport, recognised

    known = {row['code'] for row in connection.execute('SELECT code FROM sports_ai_sports')}
    added = []
    for row in connection.execute('SELECT DISTINCT sport FROM sports_ai_fixtures WHERE sport<>""'):
        code = str(row['sport']).strip().lower()
        if not code or code in known:
            continue
        canonical = canonical_sport(code)
        note = ('Recognised sport family; a probability is produced only after its own model passes the holdout gates.'
                if recognised(canonical) else
                'Discovered from collected fixtures. No validated model exists, so it is unsupported for prediction; '
                'evidence is still collected.')
        connection.execute(
            """INSERT OR IGNORE INTO sports_ai_sports(code,name,prediction_status,acquisition_status,notes)
               VALUES(?,?,'not_modelled','source_catalog_only',?)""",
            (code, code.replace('_', ' ').title(), note),
        )
        known.add(code)
        added.append(code)
    return {'registered': added}


def capability_catalogue(connection):
    from sport_support import BASKETBALL_FAMILY, family_of

    sports = [dict(r) for r in connection.execute(
        'SELECT code,name,prediction_status,acquisition_status,notes FROM sports_ai_sports ORDER BY code',
    )]
    markets = {}
    for row in connection.execute('SELECT sport,status,COUNT(*) n FROM sports_ai_market_registry GROUP BY sport,status'):
        markets.setdefault(row['sport'], {})[row['status']] = row['n']
    models = {}
    for row in connection.execute(
        """SELECT sport,model_name,version,lifecycle,calibration_status,metrics_json,activated_at
           FROM sports_ai_model_registry WHERE active=1""",
    ):
        models.setdefault(row['sport'], []).append({
            'model_name': row['model_name'], 'version': row['version'], 'lifecycle': row['lifecycle'],
            'calibration_status': row['calibration_status'], 'activated_at': row['activated_at'],
            'calibration_error': _loads(row['metrics_json'], {}).get('calibration_error'),
            'holdout_fixtures': _loads(row['metrics_json'], {}).get('holdout_fixtures'),
        })
    fixtures = {row['sport']: row['n'] for row in connection.execute(
        """SELECT sport,COUNT(*) n FROM sports_ai_fixtures
           WHERE status='scheduled' AND starts_at>? GROUP BY sport""", (_now().isoformat(),),
    )}
    items = []
    for sport in sports:
        code = sport['code']
        family = family_of(code)
        counts = markets.get(code, {})
        active_models = models.get(code, [])
        active_markets = counts.get('active', 0)
        if sport['prediction_status'] == 'modelled' and active_models and active_markets:
            status, reason = 'active', 'A validated active model and at least one active market exist.'
        elif family is None:
            status, reason = 'unsupported', 'No sport-specific model family exists; evidence is collected and no probability is produced.'
        elif not active_models:
            status, reason = 'research', 'The model family exists but no version has passed the holdout gates for this sport.'
        else:
            status, reason = 'research', 'A model is active but no market has passed its own holdout and quote requirements.'
        items.append({
            **sport, 'model_family': family, 'capability': status, 'reason': reason,
            'active_models': active_models, 'markets_by_status': counts,
            'upcoming_fixtures': fixtures.get(code, 0),
            'basketball_pipeline': family == BASKETBALL_FAMILY,
        })
    summary = {k: sum(1 for i in items if i['capability'] == k) for k in ('active', 'research', 'unsupported')}
    return {'sports': items, 'summary': summary}


# --------------------------------------------------------------------------- ranking

def conservative_margin(probability, calibration_error=None, holdout=None):
    """How far the win probability may plausibly sit below the estimate.

    The larger of the stored holdout calibration error and a one-sided 95%
    sampling bound on a hit rate measured over ``holdout`` fixtures. With no
    holdout the margin is the whole probability, so nothing is conservative-positive.
    """
    p = _float(probability)
    if p is None:
        return None
    n = int(_float(holdout) or 0)
    if n <= 0:
        return p
    sampling = CONFIDENCE_Z * math.sqrt(max(p * (1 - p), 0.0) / n)
    return max(_float(calibration_error) or 0.0, sampling)


def score_market(probability, odds, expected_value=None, calibration_error=None, holdout=None):
    """Break-even, edge and conservative EV for one priced selection.

    ``expected_value`` is the stored value when the pipeline computed it from the
    full settlement distribution (push, half-win, half-loss). Otherwise the
    binary formula p*odds-1 is used. The conservative EV removes the margin
    from the win probability: EV falls by margin*odds.
    """
    p, o = _float(probability), _float(odds)
    if p is None or o is None or o <= 1:
        return {'break_even': None, 'edge': None, 'expected_value': None, 'conservative_probability': None,
                'conservative_ev': None, 'robust': False, 'preferred_band': False}
    ev = _float(expected_value)
    if ev is None:
        ev = p * o - 1
    margin = conservative_margin(p, calibration_error, holdout)
    conservative = None if margin is None else ev - margin * o
    return {
        'break_even': round(1 / o, 6),
        'edge': round(p - 1 / o, 6),
        'expected_value': round(ev, 6),
        'conservative_probability': None if margin is None else round(max(0.0, p - margin), 6),
        'conservative_ev': None if conservative is None else round(conservative, 6),
        'robust': conservative is not None and conservative >= 0,
        'preferred_band': PREFERRED_ODDS[0] <= o <= PREFERRED_ODDS[1],
    }


def rank_key(item):
    """Spec order: conservative EV, probability, useful odds, completeness, model record,
    evidence, uncertainty, freshness. Missing values sort last."""
    def neg(value):
        number = _float(value)
        return float('inf') if number is None else -number

    def pos(value):
        number = _float(value)
        return float('inf') if number is None else number

    return (
        STATE_ORDER.get(item.get('state'), 9),
        neg(item.get('conservative_ev')),
        neg(item.get('probability')),
        0 if item.get('preferred_band') else 1,
        neg(item.get('completeness')),
        pos(item.get('calibration_error')),
        neg(item.get('holdout_fixtures')),
        pos(item.get('uncertainty')),
        pos(item.get('odds_age_minutes')),
    )


def _board_query(connection, filters, now):
    where = ['1=1']
    params = []
    if not filters.get('include_started'):
        where.append('d.starts_at>?')
        params.append(now.isoformat())
    for key, column in (('sport', 'd.sport'), ('market', 'd.market')):
        if filters.get(key):
            where.append(f'{column}=?')
            params.append(filters[key])
    if filters.get('state'):
        where.append('d.state=?')
        params.append(filters['state'])
    if filters.get('competition'):
        where.append('d.competition LIKE ?')
        params.append(f"%{filters['competition']}%")
    if filters.get('country'):
        where.append('(c.name=? OR l.country_code=?)')
        params.extend([filters['country'], filters['country']])
    if filters.get('date_from'):
        where.append('d.starts_at>=?')
        params.append(filters['date_from'])
    if filters.get('date_to'):
        where.append('d.starts_at<=?')
        params.append(filters['date_to'] if len(filters['date_to']) > 10 else filters['date_to'] + 'T23:59:59')
    if filters.get('min_probability') is not None:
        where.append("CAST(json_extract(d.evidence_json,'$.probability') AS REAL)>=?")
        params.append(float(filters['min_probability']))
    if filters.get('odds_min') is not None:
        where.append("json_extract(d.evidence_json,'$.implied_probability')>0 AND 1.0/json_extract(d.evidence_json,'$.implied_probability')>=?")
        params.append(float(filters['odds_min']))
    if filters.get('odds_max') is not None:
        where.append("json_extract(d.evidence_json,'$.implied_probability')>0 AND 1.0/json_extract(d.evidence_json,'$.implied_probability')<=?")
        params.append(float(filters['odds_max']))
    sql = f"""SELECT d.id,d.prediction_id,d.fixture_id,d.sport,d.competition,d.market,d.state,d.reason_code,d.reason,
                     d.evidence_json,d.products_json,d.rank_score,d.starts_at,d.expires_at,d.updated_at,
                     f.home,f.away,f.status AS fixture_status,l.country_code,c.name AS country
              FROM sports_ai_qualification_decisions d
              JOIN sports_ai_fixtures f ON f.id=d.fixture_id
              LEFT JOIN sports_ai_leagues l ON l.id=f.league_id
              LEFT JOIN sports_ai_countries c ON c.code=l.country_code
              WHERE {' AND '.join(where)}
              ORDER BY CASE d.state WHEN 'QUALIFIED' THEN 0 WHEN 'MONITORING' THEN 1 ELSE 2 END, d.starts_at
              LIMIT {MAX_BOARD_ROWS}"""
    return connection.execute(sql, params).fetchall()


def _board_item(row):
    evidence = _loads(row['evidence_json'], {})
    implied = _float(evidence.get('implied_probability'))
    odds = round(1 / implied, 4) if implied and implied > 0 else None
    probability = _float(evidence.get('probability'))
    scored = score_market(probability, odds, evidence.get('expected_value'),
                          evidence.get('calibration_error'), evidence.get('holdout_fixtures'))
    return {
        'decision_id': row['id'], 'prediction_id': row['prediction_id'], 'fixture_id': row['fixture_id'],
        'sport': row['sport'], 'country': row['country'] or row['country_code'], 'competition': row['competition'],
        'home': row['home'], 'away': row['away'], 'starts_at': row['starts_at'], 'market': row['market'],
        'state': row['state'], 'reason_code': row['reason_code'], 'reason': row['reason'],
        'odds': odds, 'probability': probability,
        'model_probability': _float(evidence.get('model_probability')),
        'market_fair_probability': _float(evidence.get('market_fair_probability')),
        'calibration_error': _float(evidence.get('calibration_error')),
        'holdout_fixtures': evidence.get('holdout_fixtures'),
        'completeness': _float(evidence.get('completeness')),
        'uncertainty': _float(evidence.get('uncertainty')),
        'odds_age_minutes': _float(evidence.get('odds_age_minutes')),
        'model_version': evidence.get('model_version'),
        'products': _loads(row['products_json'], []),
        'expires_at': row['expires_at'], 'updated_at': row['updated_at'],
        **scored,
        **_integrity_fields(evidence),
    }


def _integrity_fields(evidence):
    """Prefer the integrity engine's stored figures, so every page shows the same versioned values."""
    fields = {
        'probability_record_id': evidence.get('probability_record_id'),
        'capability_level': evidence.get('capability_level'),
        'capability_reason': evidence.get('capability_reason'),
        'consensus_probability': _float(evidence.get('consensus_probability')),
        'consensus_sources': evidence.get('consensus_independent_sources'),
        'sportybet_fair_probability': _float(evidence.get('sportybet_fair_probability')),
        'market_disagreement': _float(evidence.get('market_disagreement')),
        'integrity_issues': evidence.get('integrity_issues') or [],
        'paper_trade': bool(evidence.get('paper_trade')),
    }
    if evidence.get('conservative_ev') is not None:
        fields['conservative_ev'] = _float(evidence['conservative_ev'])
        fields['conservative_probability'] = _float(evidence.get('conservative_probability'))
        fields['robust'] = fields['conservative_ev'] is not None and fields['conservative_ev'] > _float(evidence.get('safety_margin') or 0)
        fields['ev_source'] = 'probability_record'
    else:
        fields['ev_source'] = 'estimated_from_holdout'
    return fields


def opportunity_board(connection, filters=None, page=1, page_size=50, now=None):
    filters = {k: v for k, v in (filters or {}).items() if v not in (None, '')}
    if filters.get('state') and filters['state'] not in STATES:
        raise ValueError(f"state must be one of {', '.join(STATES)}")
    current = _now(now)
    rows = [_board_item(r) for r in _board_query(connection, filters, current)]
    if filters.get('robust_only'):
        rows = [r for r in rows if r['robust']]
    rows.sort(key=rank_key)
    fixtures = {}
    for item in rows:
        group = fixtures.get(item['fixture_id'])
        if group is None:
            group = fixtures[item['fixture_id']] = {
                'fixture_id': item['fixture_id'], 'sport': item['sport'], 'country': item['country'],
                'competition': item['competition'], 'home': item['home'], 'away': item['away'],
                'starts_at': item['starts_at'], 'markets_evaluated': 0, 'qualified_markets': 0,
                'best': None, 'verdict': NO_OPPORTUNITY, 'leading_reason': item['reason'],
            }
        group['markets_evaluated'] += 1
        if item['state'] == 'QUALIFIED':
            group['qualified_markets'] += 1
            if group['best'] is None:
                group['best'] = item
                group['verdict'] = 'QUALIFIED'
                group['leading_reason'] = item['reason']
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 200))
    start = (page - 1) * page_size
    view = filters.get('view') or 'markets'
    listing = rows if view == 'markets' else sorted(
        fixtures.values(), key=lambda g: (g['best'] is None, rank_key(g['best']) if g['best'] else (), g['starts_at'] or ''),
    )
    summary = {
        'markets': len(rows),
        'fixtures': len(fixtures),
        'qualified_markets': sum(1 for r in rows if r['state'] == 'QUALIFIED'),
        'fixtures_with_opportunity': sum(1 for g in fixtures.values() if g['best']),
        'fixtures_without_opportunity': sum(1 for g in fixtures.values() if not g['best']),
        'robust_qualified': sum(1 for r in rows if r['state'] == 'QUALIFIED' and r['robust']),
        'preferred_band_qualified': sum(1 for r in rows if r['state'] == 'QUALIFIED' and r['preferred_band']),
        'by_state': {s: sum(1 for r in rows if r['state'] == s) for s in STATES},
        'truncated': len(rows) >= MAX_BOARD_ROWS,
    }
    return {
        'view': view, 'items': listing[start:start + page_size], 'total': len(listing),
        'page': page, 'page_size': page_size, 'summary': summary,
        'facets': facets(connection, current),
        'ranking': ['conservative expected value', 'calibrated probability', f'odds {PREFERRED_ODDS[0]:.2f}–{PREFERRED_ODDS[1]:.2f} preferred',
                    'data completeness', 'holdout calibration error', 'holdout size', 'uncertainty', 'odds freshness'],
        'generated_at': current.isoformat(),
    }


def facets(connection, now=None):
    current = _now(now).isoformat()
    sports = [r[0] for r in connection.execute(
        'SELECT DISTINCT sport FROM sports_ai_qualification_decisions WHERE starts_at>? ORDER BY sport', (current,))]
    markets = [r[0] for r in connection.execute(
        'SELECT DISTINCT market FROM sports_ai_qualification_decisions WHERE starts_at>? ORDER BY market LIMIT 400', (current,))]
    countries = [r[0] for r in connection.execute(
        """SELECT DISTINCT COALESCE(c.name,l.country_code) FROM sports_ai_qualification_decisions d
           JOIN sports_ai_fixtures f ON f.id=d.fixture_id JOIN sports_ai_leagues l ON l.id=f.league_id
           LEFT JOIN sports_ai_countries c ON c.code=l.country_code
           WHERE d.starts_at>? AND l.country_code IS NOT NULL ORDER BY 1 LIMIT 400""", (current,)) if r[0]]
    return {'sports': sports, 'markets': markets, 'countries': countries, 'states': list(STATES)}


# --------------------------------------------------------------------------- all-market analyzer

def fixture_markets(connection, fixture_id, now=None):
    current = _now(now)
    fixture = connection.execute(
        """SELECT f.id,f.sport,f.competition,f.home,f.away,f.starts_at,f.status,f.source,
                  l.name AS league,l.country_code,c.name AS country
           FROM sports_ai_fixtures f LEFT JOIN sports_ai_leagues l ON l.id=f.league_id
           LEFT JOIN sports_ai_countries c ON c.code=l.country_code WHERE f.id=?""", (fixture_id,),
    ).fetchone()
    if fixture is None:
        raise LookupError('Fixture not found')
    fixture = dict(fixture)
    registry = {row['market_key']: dict(row) for row in connection.execute(
        'SELECT market_key,status,settlement_rule,model_source,model_version FROM sports_ai_market_registry WHERE sport=?',
        (fixture['sport'],),
    )}
    decisions = {(row['prediction_id'], row['market']): dict(row) for row in connection.execute(
        """SELECT prediction_id,market,state,reason_code,reason,evidence_json FROM sports_ai_qualification_decisions
           WHERE fixture_id=?""", (fixture_id,),
    )}
    predictions = connection.execute(
        """SELECT p.id,p.market,p.outcome,p.probability,p.created_at,p.explanation_json,
                  m.model_name,m.version AS model_version,m.metrics_json,m.lifecycle,m.active
           FROM sports_ai_engine_predictions p JOIN sports_ai_model_registry m ON m.id=p.model_id
           WHERE p.fixture_id=? AND p.created_at=(
             SELECT MAX(p2.created_at) FROM sports_ai_engine_predictions p2
             WHERE p2.fixture_id=p.fixture_id AND p2.market=p.market)""", (fixture_id,),
    ).fetchall()
    rows = []
    priced_odds_ids = set()
    for p in predictions:
        quote = connection.execute(
            """SELECT o.expected_edge,o.status AS value_status,o.reason AS value_reason,o.data_completeness,
                      q.id AS odds_id,q.decimal_odds,q.bookmaker,q.observed_at,q.source_url,q.line
               FROM sports_ai_value_opportunities o JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id
               WHERE o.prediction_id=? AND q.decimal_odds>1 ORDER BY q.observed_at DESC LIMIT 1""", (p['id'],),
        ).fetchone()
        metrics = _loads(p['metrics_json'], {})
        decision = decisions.get((p['id'], p['market']))
        evidence = _loads(decision['evidence_json'], {}) if decision else {}
        probability = _float(evidence.get('probability')) if decision and evidence.get('probability') is not None else _float(p['probability'])
        odds = _float(quote['decimal_odds']) if quote else None
        scored = score_market(probability, odds, quote['expected_edge'] if quote and probability == _float(p['probability']) else None,
                              metrics.get('calibration_error'), metrics.get('holdout_fixtures'))
        if quote:
            priced_odds_ids.add(quote['odds_id'])
        observed = _parse(quote['observed_at']) if quote else None
        reg = registry.get(p['market'], {})
        rows.append({
            'market': p['market'], 'outcome': p['outcome'], 'probability': probability,
            'model_probability': _float(p['probability']),
            'model': f"{p['model_name']} {p['model_version']}", 'model_lifecycle': p['lifecycle'],
            'calibration_error': _float(metrics.get('calibration_error')),
            'holdout_fixtures': metrics.get('holdout_fixtures'),
            'odds': odds, 'bookmaker': quote['bookmaker'] if quote else None,
            'odds_observed_at': quote['observed_at'] if quote else None,
            'odds_age_minutes': round((current - observed).total_seconds() / 60, 1) if observed else None,
            'source_url': quote['source_url'] if quote else None,
            'completeness': _float(quote['data_completeness']) if quote else None,
            'registry_status': reg.get('status', 'unregistered'),
            'settlement_rule': reg.get('settlement_rule'),
            'state': decision['state'] if decision else ('AWAITING_DATA' if odds is None else 'MONITORING'),
            'reason_code': decision['reason_code'] if decision else ('MARKET_UNAVAILABLE' if odds is None else 'NOT_ASSESSED'),
            'reason': decision['reason'] if decision else (
                'No bookmaker price is stored for this market.' if odds is None
                else 'Priced and modelled, but this market is outside the qualification scope (no active registry entry).'),
            'uncertainty': _float(evidence.get('uncertainty')),
            **scored,
            **(_integrity_fields(evidence) if decision else {}),
        })
    # Quotes with no model probability: listed so every offered market is visible, never scored.
    unmodelled = []
    seen = set()
    for q in connection.execute(
        """SELECT id,bookmaker,market,outcome,line,decimal_odds,observed_at,source_url,original_label
           FROM sports_ai_odds_snapshots WHERE fixture_id=? ORDER BY observed_at DESC LIMIT 2000""", (fixture_id,),
    ):
        key = (q['bookmaker'], q['market'], q['outcome'], q['line'])
        if key in seen:
            continue
        seen.add(key)
        if q['id'] in priced_odds_ids:
            continue
        unmodelled.append({
            'bookmaker': q['bookmaker'], 'market': q['market'], 'outcome': q['outcome'], 'line': q['line'],
            'label': q['original_label'], 'odds': q['decimal_odds'], 'break_even': round(1 / q['decimal_odds'], 6),
            'observed_at': q['observed_at'], 'source_url': q['source_url'],
            'status': 'No model probability for this market; it is not evaluated.',
        })
    rows.sort(key=rank_key)
    best = next((r for r in rows if r['state'] == 'QUALIFIED'), None)
    return {
        'fixture': fixture,
        'verdict': 'QUALIFIED' if best else NO_OPPORTUNITY,
        'best_market': best,
        'markets': rows,
        'unmodelled_quotes': unmodelled[:300],
        'counts': {'modelled': len(rows), 'priced': sum(1 for r in rows if r['odds']), 'unmodelled_quotes': len(unmodelled),
                   'qualified': sum(1 for r in rows if r['state'] == 'QUALIFIED')},
        'generated_at': current.isoformat(),
    }


# --------------------------------------------------------------------------- API

def build_universal_router(auth):
    from fastapi import APIRouter, Depends, HTTPException, Query
    from db import connect

    router = APIRouter(prefix='/api/sports-ai/universal', tags=['Universal intelligence'])

    @router.get('/sources')
    def sources(user=Depends(auth)):
        with connect() as connection:
            return research_sources(connection)

    @router.get('/capabilities')
    def capabilities(user=Depends(auth)):
        with connect() as connection:
            return capability_catalogue(connection)

    @router.get('/opportunities')
    def opportunities(sport: str | None = None, country: str | None = None, competition: str | None = None,
                      market: str | None = None, state: str | None = None,
                      odds_min: float | None = Query(default=None, gt=1), odds_max: float | None = Query(default=None, gt=1),
                      min_probability: float | None = Query(default=None, ge=0, le=1),
                      date_from: str | None = None, date_to: str | None = None,
                      robust_only: bool = False, include_started: bool = False,
                      view: str = Query(default='markets', pattern='^(markets|fixtures)$'),
                      page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=200),
                      user=Depends(auth)):
        filters = dict(sport=sport, country=country, competition=competition, market=market, state=state,
                       odds_min=odds_min, odds_max=odds_max, min_probability=min_probability,
                       date_from=date_from, date_to=date_to, robust_only=robust_only,
                       include_started=include_started, view=view)
        try:
            with connect() as connection:
                return opportunity_board(connection, filters, page, page_size)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.get('/fixtures/{fixture_id}/markets')
    def markets(fixture_id: str, user=Depends(auth)):
        try:
            with connect() as connection:
                return fixture_markets(connection, fixture_id)
        except LookupError as error:
            raise HTTPException(404, str(error)) from error

    return router
