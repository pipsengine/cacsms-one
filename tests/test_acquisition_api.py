import os
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))

import acquisition_api
import db
from fastapi import HTTPException


class AcquisitionApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_directory=tempfile.TemporaryDirectory()
        cls.previous_database=db.DB
        db.DB=Path(cls.temp_directory.name)/'acquisition-api.db'
        db.initialize()
        cls.router=acquisition_api.build_acquisition_router(lambda: 'test-user')

    @classmethod
    def tearDownClass(cls):
        db.DB=cls.previous_database
        cls.temp_directory.cleanup()

    def setUp(self):
        with db.connect() as connection:
            connection.execute('DELETE FROM sports_ai_predictions')
            connection.execute('DELETE FROM sports_ai_analysis_runs')
            connection.execute('DELETE FROM sports_ai_browser_sessions')
            connection.execute('DELETE FROM sports_ai_fixtures')

    def _endpoint(self,path):
        return next(route.endpoint for route in self.router.routes if route.path==path)

    def test_provider_listing_reports_environment_credential_state(self):
        os.environ.pop('SPORTS_FOOTBALL_DATA_API_KEY',None)
        sources=self._endpoint('/api/sports-ai/acquisition/providers')(user='test-user')
        football_data=next(source for source in sources if source['adapter']=='football_data')
        self.assertTrue(football_data['requires_api_key'])
        self.assertFalse(football_data['credential_configured'])

    def test_match_research_reads_legacy_predictions_with_run_timestamp(self):
        fixture_id,run_id=str(uuid.uuid4()),str(uuid.uuid4())
        with db.connect() as connection:
            connection.execute(
                """INSERT INTO sports_ai_fixtures
                   (id,external_id,source,sport,competition,home,away,starts_at,status,
                    statistics_json,odds_json,observed_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (fixture_id,'match-1','test','football','Test League','Home','Away',
                 '2027-01-02T15:00:00+00:00','scheduled','{}','{}',
                 datetime.now(timezone.utc).isoformat()),
            )
            connection.execute(
                'INSERT INTO sports_ai_analysis_runs(id,status) VALUES(?,?)',
                (run_id,'complete'),
            )
            connection.execute(
                """INSERT INTO sports_ai_predictions
                   (id,run_id,fixture_id,sport,fixture,market,odds,probability,edge,model_name)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()),run_id,fixture_id,'football','Home vs Away',
                 'home_win',2.0,0.6,0.2,'test-model'),
            )
        research=self._endpoint('/api/sports-ai/research/matches/{fixture_id}')(
            fixture_id=fixture_id,user='test-user',
        )
        self.assertEqual(len(research['analysis']),1)
        self.assertEqual(research['analysis'][0]['model_name'],'test-model')
        self.assertIsNotNone(research['analysis'][0]['analyzed_at'])

    def test_running_browser_session_cannot_be_retried(self):
        session_id=str(uuid.uuid4())
        with db.connect() as connection:
            connection.execute(
                """INSERT INTO sports_ai_browser_sessions
                   (id,query,status,started_at,requested_urls_json,actor)
                   VALUES(?,?,'running',?,?,?)""",
                (session_id,'research',datetime.now(timezone.utc).isoformat(),
                 '["https://example.com/"]','test-user'),
            )
        retry=self._endpoint(
            '/api/sports-ai/acquisition/browser/sessions/{session_id}/retry',
        )
        with self.assertRaises(HTTPException) as raised:
            retry(session_id=session_id,user='test-user')
        self.assertEqual(raised.exception.status_code,409)


if __name__=='__main__':
    unittest.main()
