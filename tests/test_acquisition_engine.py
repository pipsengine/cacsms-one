import json
import os
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))

import acquisition_engine
import db
from acquisition import CollectionError, import_basketball_rows
from browser_operator import BrowserResearchError, validate_research_url


class AcquisitionEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_directory=tempfile.TemporaryDirectory()
        cls.database_path=Path(cls.temp_directory.name)/'acquisition.db'
        cls.previous_database=db.DB
        db.DB=cls.database_path
        db.initialize()

    @classmethod
    def tearDownClass(cls):
        db.DB=cls.previous_database
        cls.temp_directory.cleanup()

    def setUp(self):
        with db.connect() as connection:
            for table in (
                'sports_ai_acquisition_jobs','sports_ai_fixture_source_map',
                'sports_ai_match_statistics','sports_ai_standings_snapshots',
                'sports_ai_data_quality_issues','sports_ai_results',
                'sports_ai_fixtures',
            ):
                connection.execute(f'DELETE FROM {table}')
            connection.execute(
                """UPDATE sports_ai_provider_registry SET enabled=0,terms_reviewed=0,
                   status='untested',last_attempt_at=NULL,last_success_at=NULL,
                   last_error=NULL,next_run_at=NULL,config_json='{}'"""
            )
            connection.execute(
                'DELETE FROM sports_ai_acquisition_schedules'
            )

    def _provider(self,adapter='openligadb',provider_key=None,config=None,enabled=True):
        provider_id=str(uuid.uuid4())
        key=provider_key or f'test-{provider_id[:8]}'
        with db.connect() as connection:
            connection.execute(
                """INSERT INTO sports_ai_provider_registry
                   (id,provider_key,display_name,sport,adapter,enabled,terms_reviewed,
                    status,config_json)
                   VALUES(?,?,?,?,?,?,1,'untested',?)""",
                (provider_id,key,'Test source',
                 'basketball' if adapter in ('balldontlie','sportsdataverse_csv') else 'football',
                 adapter,int(enabled),json.dumps(config or {})),
            )
            connection.execute(
                """UPDATE sports_ai_provider_registry SET interval_minutes=5
                   WHERE id=?""",(provider_id,),
            )
        with db.connect() as connection:
            return dict(connection.execute(
                'SELECT * FROM sports_ai_provider_registry WHERE id=?',(provider_id,),
            ).fetchone())

    @staticmethod
    def _record(external_id='game-1',source='source-one'):
        return {
            'external_id':external_id,'sport':'football','competition':'Test League',
            'home':'São Paulo FC','away':'Côte d’Ivoire','starts_at':'2026-10-30T18:00:00Z',
            'observed_at':'2026-10-30T21:00:00Z','source_url':'https://example.org/fixtures',
            'status':'finished','home_score':2,'away_score':1,
            'settled_at':'2026-10-30T20:00:00Z','statistics':{'possession':{'home':55,'away':45}},
        }

    def test_cross_provider_fixture_mapping_deduplicates_and_preserves_results(self):
        first=self._provider(provider_key='source-one')
        second=self._provider(provider_key='source-two')
        record=self._record()
        with db.connect() as connection:
            acquisition_engine._persist(connection,first,{'fixtures':[record],'standings':[]})
            equivalent={**record,'external_id':'another-id','home':'Sao Paulo FC',
                        'away':'Cote d Ivoire','source_url':'https://example.org/other',
                        'starts_at':'2026-10-30T00:00:00+00:00','time_precision':'date'}
            acquisition_engine._persist(connection,second,{'fixtures':[equivalent],'standings':[]})
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM sports_ai_fixtures').fetchone()[0],1)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM sports_ai_fixture_source_map').fetchone()[0],2)
            self.assertEqual(
                connection.execute('SELECT starts_at FROM sports_ai_fixtures').fetchone()[0],
                '2026-10-30T18:00:00Z',
            )
            result=connection.execute('SELECT home_score,away_score FROM sports_ai_results').fetchone()
            self.assertEqual(tuple(result),(2,1))
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM sports_ai_match_statistics').fetchone()[0],2)

    def test_real_collection_only_marks_operational_after_persisted_records(self):
        provider=self._provider(config={'league':'bl1','season':2026})
        batch={'fixtures':[self._record()], 'standings':[],
               'source_url':'https://api.openligadb.de/getmatchdata/bl1/2026','pages':1}
        with patch.object(acquisition_engine,'_openligadb',return_value=batch):
            result=acquisition_engine.run_collection(provider['id'])
        self.assertEqual(result['records_saved'],1)
        with db.connect() as connection:
            row=connection.execute(
                'SELECT status,last_success_at FROM sports_ai_provider_registry WHERE id=?',
                (provider['id'],),
            ).fetchone()
        self.assertEqual(row['status'],'operational')
        self.assertTrue(row['last_success_at'])

    def test_empty_collection_does_not_mark_operational(self):
        provider=self._provider()
        batch={'fixtures':[],'standings':[],'source_url':'https://example.org/fixtures','pages':1}
        with patch.object(acquisition_engine,'_openligadb',return_value=batch):
            result=acquisition_engine.run_collection(provider['id'])
        self.assertEqual(result['records_saved'],0)
        with db.connect() as connection:
            status=connection.execute(
                'SELECT status FROM sports_ai_provider_registry WHERE id=?',
                (provider['id'],),
            ).fetchone()[0]
        self.assertEqual(status,'empty')

    def test_failed_collection_is_logged_and_retry_can_recover(self):
        provider=self._provider()
        batch={'fixtures':[self._record()], 'standings':[],
               'source_url':'https://api.openligadb.de/getmatchdata/bl1/2026','pages':1}
        with patch.object(acquisition_engine,'_openligadb',side_effect=CollectionError('temporary failure')):
            with self.assertRaises(CollectionError):
                acquisition_engine.run_collection(provider['id'])
        with db.connect() as connection:
            failed=connection.execute(
                """SELECT id,status FROM sports_ai_acquisition_jobs
                   ORDER BY started_at DESC LIMIT 1"""
            ).fetchone()
            connection.execute(
                """UPDATE sports_ai_provider_registry SET last_attempt_at=?
                   WHERE id=?""",
                ((datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat(),provider['id']),
            )
        self.assertEqual(failed['status'],'failed')
        with patch.object(acquisition_engine,'_openligadb',return_value=batch):
            recovered=acquisition_engine.run_collection(provider['id'],retry_of=failed['id'])
        self.assertEqual(recovered['records_saved'],1)
        with db.connect() as connection:
            retry=connection.execute(
                'SELECT retry_of,status,attempt FROM sports_ai_acquisition_jobs WHERE id=?',
                (recovered['job_id'],),
            ).fetchone()
        self.assertEqual(retry['retry_of'],failed['id'])
        self.assertEqual(retry['status'],'complete')
        self.assertEqual(retry['attempt'],2)
        with db.connect() as connection:
            open_failures = connection.execute(
                """SELECT COUNT(*) FROM sports_ai_data_quality_issues
                   WHERE issue_type='provider_collection_failed' AND resolved_at IS NULL""",
            ).fetchone()[0]
        self.assertEqual(open_failures, 0)

    def test_football_data_normalizes_provider_rows_and_standings(self):
        provider=self._provider(
            adapter='football_data',
            config={'competition':'PL','season':2026,'date_from':'2026-10-01','date_to':'2026-12-01'},
        )
        os.environ['SPORTS_FOOTBALL_DATA_API_KEY']='test-token'
        payloads=[
            ({'matches':[{
                'id':123,'competition':{'name':'Premier League'},
                'homeTeam':{'name':'Home FC'},'awayTeam':{'name':'Away FC'},
                'utcDate':'2026-11-02T18:00:00Z','status':'FINISHED',
                'score':{'fullTime':{'home':1,'away':0}},'lastUpdated':'2026-11-02T20:00:00Z',
            }],'resultSet':{'count':1}},{}),
            ({'competition':{'name':'Premier League'},'standings':[{
                'table':[{'position':1,'team':{'name':'Home FC'},'playedGames':10,
                          'won':8,'draw':1,'lost':1,'points':25,'goalDifference':12}]
            }]},{}),
        ]
        try:
            with patch.object(acquisition_engine,'_json_body',side_effect=payloads) as request:
                batch=acquisition_engine._football_data(provider,json.loads(provider['config_json']),limit=100)
            self.assertEqual(len(batch['fixtures']),1)
            self.assertEqual(batch['fixtures'][0]['home_score'],1)
            self.assertEqual(batch['standings'][0]['points'],25)
            self.assertEqual(request.call_args_list[0].args[1]['X-Auth-Token'],'test-token')
            self.assertNotIn('season=',batch['source_url'])
        finally:
            os.environ.pop('SPORTS_FOOTBALL_DATA_API_KEY',None)

    def test_openligadb_normalizes_final_scores(self):
        provider=self._provider(config={'league':'bl1','season':2026})
        payload=[{
            'matchID':20,'matchDateTimeUTC':'2026-10-30T19:30:00Z',
            'lastUpdateDateTime':'2026-10-30T22:00:00Z',
            'matchIsFinished':True,'leagueName':'Bundesliga',
            'team1':{'teamName':'Home FC'},'team2':{'teamName':'Away FC'},
            'matchResults':[{'resultName':'Endergebnis','pointsTeam1':3,
                             'pointsTeam2':1,'resultTypeKind':'After90Minutes'}],
        }]
        with patch.object(acquisition_engine,'_json_body',return_value=(payload,{})):
            batch=acquisition_engine._openligadb(provider,json.loads(provider['config_json']))
        self.assertEqual(batch['fixtures'][0]['home_score'],3)
        self.assertEqual(batch['fixtures'][0]['away_score'],1)
        self.assertEqual(batch['fixtures'][0]['status'],'finished')

    def test_openligadb_does_not_store_a_score_before_the_match_is_finished(self):
        provider=self._provider(config={'league':'DEL2','season':2026})
        payload=[{
            'matchID':84149,'matchDateTimeUTC':'2026-10-23T17:30:00Z',
            'lastUpdateDateTime':'2026-10-25T18:51:13.617',
            'matchIsFinished':False,'leagueName':'DEL2 Hauptrunde',
            'team1':{'teamName':'Dresdner Eislöwen'},'team2':{'teamName':'Starbulls Rosenheim'},
            'matchResults':[{'resultName':'3.Drittel','pointsTeam1':0,
                             'pointsTeam2':0,'resultTypeKind':'After90Minutes'}],
        }]
        with patch.object(acquisition_engine,'_json_body',return_value=(payload,{})):
            batch=acquisition_engine._openligadb(provider,json.loads(provider['config_json']))
        self.assertIsNone(batch['fixtures'][0].get('home_score'))
        self.assertIsNone(batch['fixtures'][0].get('away_score'))
        self.assertEqual(batch['fixtures'][0]['status'],'scheduled')

    def test_openfootball_normalizes_both_documented_score_shapes(self):
        provider=self._provider(provider_key='test-openfootball')
        payload={'name':'Test Competition','matches':[
            {'id':'json-1','date':'2026-10-30','team1':'Home FC','team2':'Away FC',
             'score':{'ft':[2,1]}},
            {'id':'json-2','date':'2026-10-31','team1':'North FC','team2':'South FC',
             'score':[1,0]},
        ]}
        with patch.object(acquisition_engine,'utc_now',return_value='2026-11-10T00:00:00+00:00'):
            with patch.object(acquisition_engine,'_json_body',return_value=(payload,{})):
                batch=acquisition_engine._openfootball(
                    provider,{'dataset_url':'https://raw.githubusercontent.com/openfootball/football.json/master/2026-27/en.1.json'},
                )
        self.assertEqual([row['home_score'] for row in batch['fixtures']],[2,1])
        self.assertEqual([row['time_precision'] for row in batch['fixtures']],['date','date'])
        self.assertEqual(
            [row['settled_at'] for row in batch['fixtures']],
            ['2026-11-10T00:00:00+00:00'] * 2,
        )

    def test_date_only_result_settlement_uses_observation_time(self):
        observed='2026-10-31T12:00:00+00:00'
        with patch.object(acquisition_engine,'utc_now',return_value=observed):
            record=acquisition_engine._record(
                'openfootball','game-1','football','Test League','Home FC','Away FC',
                '2026-10-30','2026-10-31','https://example.org/results',
                home_score=2,away_score=1,settled_at='2026-10-30',
            )
        self.assertEqual(record['settled_at'],observed)

    def test_same_score_refresh_preserves_or_repairs_settlement_time(self):
        provider=self._provider()
        record=self._record(source='openfootball')
        with db.connect() as connection:
            acquisition_engine._persist(connection,provider,{'fixtures':[record],'standings':[]})
            later={**record,'settled_at':'2026-10-30T21:00:00+00:00'}
            acquisition_engine._persist(connection,provider,{'fixtures':[later],'standings':[]})
            settled=connection.execute(
                'SELECT settled_at FROM sports_ai_results',
            ).fetchone()['settled_at']
            self.assertEqual(settled,record['settled_at'])
            connection.execute(
                "UPDATE sports_ai_results SET settled_at='2026-10-30T00:00:00+00:00'",
            )
            repair={**record,'settled_at':'2026-10-30T22:00:00+00:00'}
            acquisition_engine._persist(connection,provider,{'fixtures':[repair],'standings':[]})
            settled=connection.execute(
                'SELECT settled_at FROM sports_ai_results',
            ).fetchone()['settled_at']
        self.assertEqual(settled,repair['settled_at'])

    def test_persistence_rejects_results_settled_before_kickoff(self):
        provider=self._provider()
        record=self._record(source='openfootball')
        record['settled_at']='2026-10-30T18:00:00+00:00'
        with db.connect() as connection:
            with self.assertRaisesRegex(CollectionError,'must be after match kickoff'):
                acquisition_engine._persist(connection,provider,{'fixtures':[record],'standings':[]})
            self.assertEqual(
                connection.execute('SELECT COUNT(*) FROM sports_ai_results').fetchone()[0],
                0,
            )

    def test_football_txt_reads_league_and_nations_scores(self):
        provider={'provider_key': 'txt-openfootball'}
        text=(
            '= UEFA Champions League 2024/25\n'
            'Tue Sep 17 2024\n'
            '21:00  Real Madrid CF (ESP) v VfB Stuttgart (GER) 3-1 (0-0)\n'
            '21:00  Liverpool FC (ENG) v Paris Saint-Germain FC (FRA) 1-4 pen. 0-1 a.e.t. (0-1)\n'
            '= UEFA Nations League 2024\n'
            'Thu Sep 5\n'
            '  Portugal               2-1 Croatia                  @ Lisbon, Portugal\n'
            '     (Diogo Dalot 7\')\n'
        )
        rows=acquisition_engine._football_txt_fixtures(
            provider, 'https://raw.githubusercontent.com/openfootball/champions-league/master/2024-25/cl.txt', text,
        )
        self.assertEqual([(row['home'], row['away'], row['home_score'], row['away_score']) for row in rows], [
            ('Real Madrid CF', 'VfB Stuttgart', 3, 1),
            ('Portugal', 'Croatia', 2, 1),
        ])
        self.assertEqual(rows[0]['competition'], 'UEFA Champions League 2024/25')
        self.assertEqual(rows[1]['competition'], 'UEFA Nations League 2024')

    def test_balldontlie_requires_key_and_uses_authenticated_pagination(self):
        provider=self._provider(
            adapter='balldontlie',config={'season':2026,'per_page':100},
        )
        with self.assertRaisesRegex(CollectionError,'SPORTS_BALLDONTLIE_API_KEY'):
            acquisition_engine._balldontlie(provider,json.loads(provider['config_json']),limit=1)
        os.environ['SPORTS_BALLDONTLIE_API_KEY']='test-token'
        payload={'data':[{
            'id':44,'date':'2026-11-01','season':2026,
            'home_team':{'full_name':'Home NBA','id':1},
            'visitor_team':{'full_name':'Away NBA','id':2},
            'home_team_score':110,'visitor_team_score':104,
        }],'meta':{'next_cursor':None}}
        try:
            with patch.object(acquisition_engine,'_json_body',return_value=(payload,{})) as request:
                batch=acquisition_engine._balldontlie(provider,json.loads(provider['config_json']),limit=1)
            self.assertEqual(batch['fixtures'][0]['sport'],'basketball')
            self.assertEqual(batch['fixtures'][0]['away_score'],104)
            self.assertEqual(request.call_args.args[1]['Authorization'],'test-token')
        finally:
            os.environ.pop('SPORTS_BALLDONTLIE_API_KEY',None)

    def test_api_pagination_cursor_is_persistable(self):
        provider=self._provider(adapter='balldontlie',config={'season':2026})
        provider['cursor_json']=json.dumps({'next_cursor':'cursor-100'})
        os.environ['SPORTS_BALLDONTLIE_API_KEY']='test-token'
        payload={'data':[{
            'id':45,'date':'2026-11-02','season':2026,
            'home_team':{'full_name':'Home NBA','id':1},
            'visitor_team':{'full_name':'Away NBA','id':2},
            'home_team_score':0,'visitor_team_score':0,
        }],'meta':{'next_cursor':'cursor-200'}}
        try:
            with patch.object(acquisition_engine,'_json_body',return_value=(payload,{})) as request:
                batch=acquisition_engine._balldontlie(
                    provider,json.loads(provider['config_json']),limit=1,
                )
            self.assertIn('cursor=cursor-100',request.call_args.args[0])
            self.assertEqual(batch['next_cursor'],'cursor-200')
        finally:
            os.environ.pop('SPORTS_BALLDONTLIE_API_KEY',None)

    def test_sportsdataverse_unplayed_zero_scores_stay_scheduled(self):
        provider=self._provider(
            adapter='sportsdataverse_csv',
            config={'dataset_url':'https://raw.githubusercontent.com/sportsdataverse/example/main/games.csv'},
        )
        csv_body=(
            b'id,date,home_name,away_name,home_score,away_score,status_type_completed,status_type_state\n'
            b'1,2020-01-01T00:00Z,Old Home,Old Away,101,99,true,post\n'
            b'2,2099-01-01T00:00Z,Future Home,Future Away,0,0,false,pre\n'
        )
        with patch.object(acquisition_engine,'_request',return_value=(csv_body,{})):
            batch=acquisition_engine._sportsdataverse_csv(
                provider,json.loads(provider['config_json']),limit=1,
            )
        self.assertEqual(len(batch['fixtures']),1)
        self.assertEqual(batch['fixtures'][0]['status'],'scheduled')
        self.assertNotIn('home_score',batch['fixtures'][0])
        self.assertEqual(batch['fixtures'][0]['home'],'Future Home')

    def test_sportsdataverse_dataset_csv_normalizes_actual_rows(self):
        provider=self._provider(
            adapter='sportsdataverse_csv',
            config={'dataset_url':'https://raw.githubusercontent.com/sportsdataverse/example/main/games.csv'},
        )
        csv_body=b'id,date,home,away,home_score,away_score\n1,2026-11-01,Home,Away,99,97\n'
        with patch.object(acquisition_engine,'utc_now',return_value='2026-11-10T00:00:00+00:00'):
            with patch.object(acquisition_engine,'_request',return_value=(csv_body,{})):
                batch=acquisition_engine._sportsdataverse_csv(
                    provider,json.loads(provider['config_json']),
                )
        self.assertEqual(batch['fixtures'][0]['sport'],'basketball')
        self.assertEqual(batch['fixtures'][0]['home_score'],99)
        self.assertEqual(batch['fixtures'][0]['settled_at'],'2026-11-10T00:00:00+00:00')

    def test_sportsdataverse_missing_settlement_uses_observation_time(self):
        provider=self._provider(
            adapter='sportsdataverse_csv',
            config={'dataset_url':'https://raw.githubusercontent.com/sportsdataverse/example/main/games.csv'},
        )
        observed='2026-11-10T00:00:00+00:00'
        csv_body=(
            b'id,date,home,away,home_score,away_score\n'
            b'1,2026-11-01T22:00:00Z,Home,Away,99,97\n'
        )
        with patch.object(acquisition_engine,'utc_now',return_value=observed):
            with patch.object(acquisition_engine,'_request',return_value=(csv_body,{})):
                batch=acquisition_engine._sportsdataverse_csv(
                    provider,json.loads(provider['config_json']),
                )
        self.assertEqual(batch['fixtures'][0]['settled_at'],observed)

    def test_browser_permissions_reject_unapproved_and_private_hosts(self):
        with self.assertRaises(BrowserResearchError):
            validate_research_url('https://example.org/match',set())
        with self.assertRaises(BrowserResearchError):
            validate_research_url('https://127.0.0.1:8000/match',{'127.0.0.1'})
        with self.assertRaises(BrowserResearchError):
            validate_research_url('http://example.org/match',{'example.org'})

    def test_provider_config_cannot_persist_credentials(self):
        for config in (
            {'api_key':'secret'},
            {'headers':{'Authorization':'secret'}},
            {'dataset_url':'https://example.org/data.csv?access_token=secret'},
        ):
            with self.subTest(config=config):
                with self.assertRaises(CollectionError):
                    acquisition_engine.validate_public_config(config)
        acquisition_engine.validate_public_config({
            'dataset_url':'https://raw.githubusercontent.com/openfootball/football.json/main/2026-27/en.1.json'
        })

    def test_basketball_csv_keeps_source_settlement_timestamp(self):
        with db.connect() as connection:
            provider=dict(connection.execute(
                "SELECT * FROM sports_ai_provider_registry WHERE provider_key='basketball-csv'"
            ).fetchone())
            import_basketball_rows(connection,provider,[{
                'id':'source-csv-1','date':'2026-10-30','home':'Home','away':'Away',
                'home_score':90,'away_score':88,
                'settled_at':'2026-10-30T21:00:00Z',
            }])
            settled=connection.execute(
                """SELECT r.settled_at FROM sports_ai_results r
                   JOIN sports_ai_fixtures f ON f.id=r.fixture_id
                   WHERE f.external_id='source-csv-1'"""
            ).fetchone()[0]
        self.assertEqual(settled,'2026-10-30T21:00:00+00:00')

if __name__=='__main__':
    unittest.main()
