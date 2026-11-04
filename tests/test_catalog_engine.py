import json
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import acquisition_engine
import catalog_engine
import db
from acquisition import CollectionError


class CatalogEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_directory = tempfile.TemporaryDirectory()
        cls.database_path = Path(cls.temp_directory.name) / 'catalog.db'
        cls.previous_database = db.DB
        cls.previous_evidence = catalog_engine.EVIDENCE_DIR
        db.DB = cls.database_path
        catalog_engine.EVIDENCE_DIR = Path(cls.temp_directory.name) / 'evidence'
        db.initialize()

    @classmethod
    def tearDownClass(cls):
        db.DB = cls.previous_database
        catalog_engine.EVIDENCE_DIR = cls.previous_evidence
        cls.temp_directory.cleanup()

    def setUp(self):
        with db.connect() as connection:
            for table in (
                'sports_ai_fixture_reconciliations', 'sports_ai_backfill_tasks',
                'sports_ai_catalog_evidence', 'sports_ai_catalog_runs',
                'sports_ai_team_aliases', 'sports_ai_teams', 'sports_ai_league_sources',
                'sports_ai_leagues', 'sports_ai_countries', 'sports_ai_odds_snapshots',
                'sports_ai_data_quality_issues', 'sports_ai_results',
                'sports_ai_fixture_source_map', 'sports_ai_fixtures',
            ):
                connection.execute(f'DELETE FROM {table}')

    def test_seed_registers_sports_without_inventing_fixtures(self):
        with db.connect() as connection:
            codes = {row['code'] for row in connection.execute('SELECT code FROM sports_ai_sports')}
            fixtures = connection.execute('SELECT COUNT(*) FROM sports_ai_fixtures').fetchone()[0]
            tennis = connection.execute("SELECT COUNT(*) FROM sports_ai_fixtures WHERE sport='tennis'").fetchone()[0]
        self.assertTrue({'football', 'basketball', 'tennis', 'volleyball', 'baseball', 'ice_hockey'} <= codes)
        self.assertEqual(fixtures, 0)
        self.assertEqual(tennis, 0)

    def test_openligadb_discovery_keeps_only_complete_provider_rows(self):
        payload = json.dumps([
            {
                'leagueId': 1,
                'leagueName': '1. Fußball-Bundesliga 2024/2025',
                'leagueShortcut': 'bl1',
                'leagueSeason': '2024',
                'sport': {'sportName': 'Fußball'},
            },
            {'leagueShortcut': 'missing', 'leagueSeason': '2024', 'sport': {'sportName': 'Tennis'}},
        ]).encode()

        def fake_request(url, headers=None):
            self.assertIn('getavailableleagues', url)
            return payload, {}

        with patch('catalog_engine._request', fake_request):
            result = catalog_engine.run_catalog_discovery(['openligadb'])
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['saved'], 1)
        with db.connect() as connection:
            league = connection.execute('SELECT sport_code, name, season, fixture_count FROM sports_ai_leagues').fetchone()
            self.assertEqual(league['sport_code'], 'football')
            self.assertEqual(league['season'], '2024')
            self.assertEqual(league['fixture_count'], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sports_ai_leagues WHERE sport_code='tennis'").fetchone()[0], 0)
            task = connection.execute('SELECT status FROM sports_ai_backfill_tasks').fetchone()
            self.assertEqual(task['status'], 'held')
        with patch('catalog_engine._request', fake_request):
            again = catalog_engine.run_catalog_discovery(['openligadb'])
        self.assertEqual(again['saved'], 1)
        with db.connect() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM sports_ai_leagues').fetchone()[0], 1)

    def test_missing_odds_key_does_not_create_markets(self):
        with patch.dict('os.environ', {}, clear=False):
            import os
            os.environ.pop('SPORTS_ODDS_API_KEY', None)
            with self.assertRaises(CollectionError):
                catalog_engine.discover_odds_api({'provider_key': 'the_odds_api', 'id': 'provider-odds'})
        with db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sports_ai_leagues WHERE league_kind='odds_market'").fetchone()[0], 0)

    def test_persisted_fixture_creates_canonical_teams_and_blocks_conflicting_scores(self):
        provider = self._provider()
        record = {
            'external_id': 'game-1', 'sport': 'football', 'competition': 'Test League',
            'home': 'São Paulo FC', 'away': 'Côte d’Ivoire', 'starts_at': '2026-10-30T18:00:00+00:00',
            'observed_at': '2026-10-30T21:00:00+00:00', 'source_url': 'https://example.org/fixtures',
            'status': 'finished', 'home_score': 2, 'away_score': 1,
            'settled_at': '2026-10-30T20:00:00+00:00', 'statistics': {},
        }
        with db.connect() as connection:
            acquisition_engine._persist(connection, provider, {'fixtures': [record], 'standings': []})
            teams = connection.execute('SELECT canonical_key FROM sports_ai_teams ORDER BY canonical_key').fetchall()
            self.assertEqual([row['canonical_key'] for row in teams], ['cotedivoire', 'saopaulofc'])
            fixture = connection.execute('SELECT canonical_key, league_id FROM sports_ai_fixtures').fetchone()
            self.assertTrue(fixture['league_id'])
            self.assertIn('saopaulofc', fixture['canonical_key'])
        changed = {**record, 'external_id': 'game-2', 'home_score': 0, 'away_score': 0}
        other = self._provider(provider_key='other-source')
        with db.connect() as connection:
            acquisition_engine._persist(connection, other, {'fixtures': [changed], 'standings': []})
            score = connection.execute('SELECT home_score, away_score FROM sports_ai_results').fetchone()
            issues = connection.execute("SELECT COUNT(*) FROM sports_ai_data_quality_issues WHERE issue_type='result_conflict'").fetchone()[0]
        self.assertEqual(tuple(score), (2, 1))
        self.assertEqual(issues, 1)

    def test_disabled_provider_cannot_queue_backfill_and_cap_stays_idle(self):
        payload = json.dumps([{
            'leagueName': 'Bundesliga', 'leagueShortcut': 'bl1', 'leagueSeason': '2025',
            'sport': {'sportName': 'Football'},
        }]).encode()
        with patch('catalog_engine._request', lambda url, headers=None: (payload, {})):
            catalog_engine.run_catalog_discovery(['openligadb'])
        with db.connect() as connection:
            connection.execute(
                "UPDATE sports_ai_provider_registry SET enabled=0, terms_reviewed=0 WHERE provider_key='openligadb'",
            )
            source_id = connection.execute('SELECT id FROM sports_ai_league_sources').fetchone()['id']
            with self.assertRaises(CollectionError):
                catalog_engine.queue_backfill(connection, [source_id])
        backfill = catalog_engine.release_and_run_backfill()
        self.assertEqual(backfill['status'], 'idle')
        with db.connect() as connection:
            self.assertEqual(connection.execute('SELECT status FROM sports_ai_backfill_tasks').fetchone()['status'], 'held')

    def test_authorized_feed_stores_only_returned_matches(self):
        provider = self._provider(adapter='authorized_json', sport='both', provider_key='volley-feed', config={
            'catalog_sport': 'volleyball',
            'feed_url': 'https://example.com/matches.json',
        })
        payload = {'matches': [{
            'id': 'v1', 'competition': 'Nations League', 'home': 'Home Side', 'away': 'Away Side',
            'starts_at': '2026-12-01T18:00:00Z', 'status': 'SCHEDULED',
            'odds': [{'bookmaker': 'LicensedBook', 'market': 'moneyline', 'outcome': 'home', 'decimal_odds': 1.91}],
        }]}
        with patch('browser_operator.validate_research_url', return_value='example.com'), \
             patch.object(acquisition_engine, '_json_body', return_value=(payload, {})):
            batch = acquisition_engine._authorized_json(provider, json.loads(provider['config_json']))
        with db.connect() as connection:
            acquisition_engine._persist(connection, provider, batch)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sports_ai_fixtures WHERE sport='volleyball'").fetchone()[0], 1)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM sports_ai_odds_snapshots').fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sports_ai_fixtures WHERE sport='tennis'").fetchone()[0], 0)

    def test_unmodelled_sport_is_skipped_by_prediction(self):
        from prediction_engine import run_analysis
        starts = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        with db.connect() as connection:
            connection.execute(
                """INSERT INTO sports_ai_fixtures
                   (id,external_id,source,sport,competition,home,away,starts_at,status,
                    statistics_json,odds_json,observed_at)
                   VALUES('tennis-1','ext','manual','tennis','ATP','Player A','Player B',?,'scheduled','{}','{}',?)""",
                (starts, datetime.now(timezone.utc).isoformat()),
            )
            with patch('prediction_engine.train_model', return_value=None):
                result = run_analysis(connection, 'tester', lambda *args: None)
        self.assertEqual(result['predictions'], 0)
        self.assertEqual(result['skipped'].get('sport_not_modelled'), 1)

    def _provider(self, adapter='openligadb', provider_key=None, sport='football', config=None):
        provider_id = str(uuid.uuid4())
        key = provider_key or f'test-{provider_id[:8]}'
        with db.connect() as connection:
            connection.execute(
                """INSERT INTO sports_ai_provider_registry
                   (id,provider_key,display_name,sport,adapter,enabled,terms_reviewed,status,config_json)
                   VALUES(?,?,?,?,?,0,0,'untested',?)""",
                (provider_id, key, 'Test source', sport, adapter, json.dumps(config or {})),
            )
            return dict(connection.execute(
                'SELECT * FROM sports_ai_provider_registry WHERE id=?', (provider_id,),
            ).fetchone())
