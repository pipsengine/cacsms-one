import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from integrity_support import eligible_everywhere

from pipeline_engine import (
    build_autonomous_tickets,
    classify_observations,
    enqueue_odds_refresh,
    measure_pipeline,
    process_odds_refresh,
    sync_ticket_lifecycle,
)
from qualification_engine import revalidate
import db

NOW = datetime.now(timezone.utc).replace(microsecond=0)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        eligible_everywhere(self)  # these tests cover pipeline mechanics, not capability promotion
        self.previous = db.DB
        self.temp = __import__('tempfile').TemporaryDirectory()
        db.DB = Path(self.temp.name) / 'sports.sqlite3'
        db.initialize()
        with db.connect() as _c:
            __import__('market_anchor').set_model_only(_c)  # these tests cover other behaviour than anchoring

    def tearDown(self):
        db.DB = self.previous
        self.temp.cleanup()

    def test_created_at_before_kickoff_is_not_enough_to_call_a_forecast_live(self):
        kickoff = NOW + timedelta(days=3)
        created = NOW - timedelta(hours=2)
        with db.connect() as connection:
            historical = self._prediction(connection, kickoff, created + timedelta(days=4), cutoff=kickoff, generated=created)
            eligible = self._prediction(connection, kickoff, created, cutoff=kickoff - timedelta(hours=2), generated=created)
            live = self._prediction(connection, kickoff, created, cutoff=kickoff, generated=created)
            original = {
                row['id']: row['created_at']
                for row in connection.execute('SELECT id, created_at FROM sports_ai_engine_predictions')
            }
            summary = classify_observations(connection)
            labels = {
                row['id']: row['observation_class']
                for row in connection.execute('SELECT id, observation_class, created_at FROM sports_ai_engine_predictions')
            }
            unchanged = {
                row['id']: row['created_at']
                for row in connection.execute('SELECT id, created_at FROM sports_ai_engine_predictions')
            }
        self.assertEqual(labels[historical], 'historical')
        self.assertEqual(labels[eligible], 'timestamp_eligible')
        self.assertEqual(labels[live], 'live_observed')
        self.assertEqual(unchanged, original)
        self.assertEqual(summary['live_observed'], 1)
        self.assertEqual(summary['live_settled'], 0)

    def test_refresh_queue_does_not_invent_a_price_and_does_not_duplicate(self):
        with db.connect() as connection:
            self._prediction(connection, NOW + timedelta(days=1), NOW - timedelta(hours=1), cutoff=NOW + timedelta(days=1), generated=NOW - timedelta(hours=1))
            first = enqueue_odds_refresh(connection, NOW)
            second = enqueue_odds_refresh(connection, NOW)
            queued = connection.execute('SELECT COUNT(*) FROM sports_ai_odds_refresh_queue').fetchone()[0]
            odds = connection.execute('SELECT COUNT(*) FROM sports_ai_odds_snapshots').fetchone()[0]
        self.assertEqual(first['odds_invented'], 0)
        self.assertEqual(second['odds_invented'], 0)
        self.assertEqual(queued, 1)
        self.assertEqual(odds, 0)

    def test_past_high_priority_requests_do_not_starve_upcoming_prices(self):
        with db.connect() as connection:
            past, _ = self._ids(connection, NOW - timedelta(hours=1))
            upcoming, _ = self._ids(connection, NOW + timedelta(hours=1))
            self._quote(connection, upcoming, NOW - timedelta(minutes=1))
            for fixture, priority in [(past, 10000), (upcoming, 10)]:
                connection.execute(
                    "INSERT INTO sports_ai_odds_refresh_queue(fixture_id,priority,status,updated_at) VALUES(?,?,'queued',?)",
                    (fixture, priority, NOW.isoformat()),
                )
            result = process_odds_refresh(connection, NOW, limit=1)
            self.assertEqual(result['expired'], 1)
            self.assertEqual(result['fixture_ids'], [upcoming])
            self.assertEqual(connection.execute(
                'SELECT status FROM sports_ai_odds_refresh_queue WHERE fixture_id=?', (past,),
            ).fetchone()[0], 'expired')

    def test_missing_market_yields_to_other_fixtures_and_enqueue_preserves_retry(self):
        with db.connect() as connection:
            missing, _ = self._ids(connection, NOW + timedelta(hours=1))
            upcoming, _ = self._ids(connection, NOW + timedelta(hours=2))
            # A quote for a different outcome must not complete the missing HOME refresh.
            quote = self._quote(connection, missing, NOW - timedelta(minutes=1))
            connection.execute("UPDATE sports_ai_odds_snapshots SET outcome='away' WHERE id=?", (quote,))
            self._quote(connection, upcoming, NOW - timedelta(minutes=1))
            enqueue_odds_refresh(connection, NOW)
            first = process_odds_refresh(connection, NOW, limit=1)
            self.assertEqual(first['waiting'], 1)
            self.assertEqual(first['refreshed'], 0)
            retry = connection.execute(
                'SELECT next_attempt_at FROM sports_ai_odds_refresh_queue WHERE fixture_id=?', (missing,),
            ).fetchone()[0]
            enqueue_odds_refresh(connection, NOW + timedelta(seconds=1))
            second = process_odds_refresh(connection, NOW + timedelta(seconds=1), limit=1)
            self.assertEqual(second['fixture_ids'], [upcoming])
            self.assertEqual(connection.execute(
                'SELECT next_attempt_at FROM sports_ai_odds_refresh_queue WHERE fixture_id=?', (missing,),
            ).fetchone()[0], retry)

    def test_refresh_links_active_forecast_ahead_of_newer_inactive_forecast(self):
        with db.connect() as connection:
            fixture, prediction = self._ids(connection, NOW + timedelta(hours=1))
            inactive = self._model(connection, version='inactive-test', active=False)
            run = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO sports_ai_analysis_runs(id,fixture_count,selection_count,ticket_count,status) VALUES(?,1,1,0,'complete')",
                (run,),
            )
            connection.execute(
                """INSERT INTO sports_ai_engine_predictions
                   (id,run_id,fixture_id,model_id,feature_id,market,outcome,probability,settlement_json,explanation_json,created_at)
                   SELECT ?,?,fixture_id,?,feature_id,market,outcome,probability,settlement_json,explanation_json,?
                   FROM sports_ai_engine_predictions WHERE id=?""",
                (str(uuid.uuid4()), run, inactive, NOW.isoformat(), prediction),
            )
            self._quote(connection, fixture, NOW - timedelta(minutes=1))
            enqueue_odds_refresh(connection, NOW)
            result = process_odds_refresh(connection, NOW)
            linked = connection.execute('SELECT prediction_id FROM sports_ai_value_opportunities').fetchall()
            self.assertEqual(result['fixture_ids'], [fixture])
            self.assertEqual([row[0] for row in linked], [prediction])
            from qualification_engine import candidate_rows, rows_for_fixtures
            self.assertIn(prediction, [row['prediction_id'] for row in candidate_rows(connection, NOW)])
            self.assertIn(prediction, [row['prediction_id'] for row in rows_for_fixtures(connection, NOW, [fixture])])

    def test_sure_odds_status_excludes_past_and_other_day_decisions(self):
        from command_view import _sure_odds_status
        # Use midday so the current fixture remains inside today's WAT window.
        now = NOW.replace(hour=11)
        with db.connect() as connection:
            for kickoff in [now - timedelta(hours=1), now + timedelta(hours=1), now + timedelta(days=1)]:
                fixture, prediction = self._ids(connection, kickoff)
                connection.execute(
                    """INSERT INTO sports_ai_qualification_decisions
                       (id,prediction_id,fixture_id,sport,market,state,reason_code,reason,evidence_json,
                        products_json,policy_version,decision_version,fingerprint,starts_at,created_at,updated_at)
                       VALUES(?,?,?,'football','HOME','EXPIRED','ODDS_STALE','Stale','{}','[]',1,1,'old',?,?,?)""",
                    (str(uuid.uuid4()), prediction, fixture, kickoff.isoformat(), now.isoformat(), now.isoformat()),
                )
            status = _sure_odds_status(connection, now)
            self.assertEqual(status['qualified_selections'], 0)
            self.assertEqual(status['blockers'], [{'code': 'ODDS_STALE', 'label': 'Bookmaker price needs refreshing', 'count': 1}])
            self.assertIn('No selection currently qualifies', status['message'])

    def test_a_stored_fresh_price_refreshes_and_a_challenge_does_not_create_one(self):
        kickoff = NOW + timedelta(days=1)
        with db.connect() as connection:
            fixture_id, prediction_id = self._ids(connection, kickoff)
            self._quote(connection, fixture_id, NOW - timedelta(minutes=2))
            connection.execute(
                """INSERT INTO sports_ai_odds_refresh_queue
                   (fixture_id,priority,status,attempt,reason,updated_at) VALUES(?,10,'queued',0,'price_stale',?)""",
                (fixture_id, NOW.isoformat()),
            )
            before = connection.execute('SELECT COUNT(*) FROM sports_ai_odds_snapshots').fetchone()[0]
            refreshed = process_odds_refresh(connection, NOW)
            after = connection.execute('SELECT COUNT(*) FROM sports_ai_odds_snapshots').fetchone()[0]
            status = connection.execute(
                'SELECT status FROM sports_ai_odds_refresh_queue WHERE fixture_id=?', (fixture_id,),
            ).fetchone()['status']
            self.assertEqual(refreshed['odds_invented'], 0)
            self.assertEqual(after, before)
            self.assertEqual(status, 'refreshed')
            blocked_fixture, _blocked_prediction = self._ids(
                connection, NOW + timedelta(days=2), created=NOW, cutoff=NOW + timedelta(days=2), generated=NOW,
            )
            connection.execute(
                """INSERT INTO sports_ai_browser_jobs
                   (id,url,status,created_at,finished_at,error) VALUES(?,?,?,?,?,?)""",
                (str(uuid.uuid4()), 'https://www.sportybet.com/ng/sport/basketball', 'failed', NOW.isoformat(), NOW.isoformat(), '{"blocked":["football"],"challenge":true}'),
            )
            connection.execute(
                """INSERT INTO sports_ai_odds_refresh_queue
                   (fixture_id,priority,status,attempt,reason,updated_at)
                   SELECT id, 5, 'queued', 0, 'price_missing', ? FROM sports_ai_fixtures WHERE id!=?""",
                (NOW.isoformat(), fixture_id),
            )
            blocked = process_odds_refresh(connection, NOW)
            attempt = connection.execute(
                'SELECT attempt, status FROM sports_ai_odds_refresh_queue WHERE fixture_id=?',
                (blocked_fixture,),
            ).fetchone()
            odds = connection.execute('SELECT COUNT(*) FROM sports_ai_odds_snapshots').fetchone()[0]
        self.assertTrue(blocked['feed_blocked'])
        self.assertEqual(blocked['odds_invented'], 0)
        self.assertEqual(attempt['attempt'], 1)
        self.assertEqual(attempt['status'], 'blocked')
        self.assertEqual(odds, before)
        self.assertTrue(prediction_id)

    def test_qualified_rows_are_reviewed_outside_the_kickoff_batch(self):
        kickoff = NOW + timedelta(days=1)
        with db.connect() as connection:
            fixture_id, prediction_id = self._ids(connection, kickoff, edge=0.18, probability=0.62, odds=1.9)
            stale = self._quote(connection, fixture_id, NOW - timedelta(minutes=40))
            opportunity_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO sports_ai_value_opportunities
                   (id,prediction_id,odds_id,expected_edge,status,reason,evaluated_at,data_completeness)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (opportunity_id, prediction_id, stale, 0.18, 'qualified', 'stored', (NOW - timedelta(minutes=40)).isoformat(), 0.8),
            )
            connection.execute(
                """INSERT INTO sports_ai_qualification_decisions
                   (id,prediction_id,opportunity_id,fixture_id,sport,market,state,reason_code,reason,evidence_json,
                    products_json,policy_version,decision_version,fingerprint,starts_at,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(uuid.uuid4()), prediction_id, opportunity_id, fixture_id, 'football', 'HOME', 'QUALIFIED',
                    'QUALIFIED_OK', 'stored', '{}', '["booking"]', 1, 1, 'old', kickoff.isoformat(),
                    NOW.isoformat(), NOW.isoformat(),
                ),
            )
            expired = revalidate(connection, 'test', NOW, limit=0)
            state = connection.execute(
                "SELECT state, reason_code FROM sports_ai_qualification_decisions WHERE prediction_id=?",
                (prediction_id,),
            ).fetchone()
            self.assertEqual(state['state'], 'EXPIRED')
            self.assertEqual(state['reason_code'], 'ODDS_STALE')
            self.assertGreaterEqual(expired['checked'], 1)
            fresh = self._quote(connection, fixture_id, NOW - timedelta(minutes=2))
            connection.execute(
                """INSERT INTO sports_ai_odds_refresh_queue
                   (fixture_id,priority,status,attempt,reason,updated_at) VALUES(?,10,'queued',0,'price_stale',?)""",
                (fixture_id, NOW.isoformat()),
            )
            process_odds_refresh(connection, NOW)
            self.assertNotEqual(fresh, stale)
            restored = revalidate(connection, 'test', NOW, limit=0, fixture_ids=[fixture_id])
            again = connection.execute(
                "SELECT state, reason_code, opportunity_id FROM sports_ai_qualification_decisions WHERE prediction_id=?",
                (prediction_id,),
            ).fetchone()
        self.assertEqual(again['state'], 'QUALIFIED')
        self.assertEqual(again['reason_code'], 'QUALIFIED_OK')
        self.assertNotEqual(again['opportunity_id'], opportunity_id)
        self.assertGreaterEqual(restored['counts']['QUALIFIED'], 1)

    def test_autonomous_builder_stores_nothing_without_a_qualified_selection(self):
        with db.connect() as connection:
            result = build_autonomous_tickets(connection, 'test', lambda *args: None, NOW)
            tickets = connection.execute('SELECT COUNT(*) FROM sports_ai_accumulator_tickets').fetchone()[0]
        self.assertEqual(result['status'], 'insufficient')
        self.assertEqual(result['tickets'], 0)
        self.assertFalse(result['places_wager'])
        self.assertEqual(tickets, 0)

    def test_lapsed_qualification_removes_a_ready_ticket(self):
        with db.connect() as _c:
            __import__('booking_policy').save(_c, __import__('booking_policy').STRICT)  # these tests cover the strict gates
        kickoff = NOW + timedelta(days=1)
        with db.connect() as connection:
            fixture_id, _prediction = self._ids(connection, kickoff)
            run_id, ticket_id = str(uuid.uuid4()), str(uuid.uuid4())
            connection.execute(
                """INSERT INTO sports_ai_accumulator_runs
                   (id,config_id,sport_mode,status,trigger,idempotency_key,parameters_json,actor,started_at)
                   VALUES(?,'accumulator-default','football','complete','test',?,'{}','test',?)""",
                (run_id, run_id, NOW.isoformat()),
            )
            connection.execute(
                """INSERT INTO sports_ai_accumulator_tickets
                   (id,run_id,family_id,version,signature,tier,sport_mode,status,leg_count,probability_basis,
                    explanation,parameters_json,created_at)
                   VALUES(?,?,?,1,'sig',200,'football','qualified',1,'independent','test','{}',?)""",
                (ticket_id, run_id, ticket_id, NOW.isoformat()),
            )
            connection.execute(
                """INSERT INTO sports_ai_accumulator_legs
                   (id,ticket_id,opportunity_id,fixture_id,sport,competition,home,away,starts_at,market,selection,
                    decimal_odds,bookmaker,odds_observed_at,quote_market,quote_outcome,model_id,model_version,settlement_kind,qualification_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(uuid.uuid4()), ticket_id, str(uuid.uuid4()), fixture_id, 'football', 'League', 'Home', 'Away',
                    kickoff.isoformat(), 'HOME', 'Home', 1.9, 'SportyBet', NOW.isoformat(), 'h2h', 'home', 'model', 'v2', 'binary', '{}',
                ),
            )
            connection.execute(
                """INSERT INTO sports_ai_qualification_decisions
                   (id,prediction_id,fixture_id,sport,market,state,reason_code,reason,evidence_json,products_json,
                    policy_version,decision_version,fingerprint,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(uuid.uuid4()), str(uuid.uuid4()), fixture_id, 'football', 'HOME', 'QUALIFIED', 'QUALIFIED_OK',
                    'stored', '{}', '["booking"]', 1, 1, 'fp', NOW.isoformat(), NOW.isoformat(),
                ),
            )
            ready = sync_ticket_lifecycle(connection, NOW)
            ready_state = connection.execute(
                'SELECT status, publication_state FROM sports_ai_accumulator_tickets WHERE id=?', (ticket_id,),
            ).fetchone()
            connection.execute("UPDATE sports_ai_qualification_decisions SET state='EXPIRED'")
            lapsed = sync_ticket_lifecycle(connection, NOW)
            lapsed_state = connection.execute(
                'SELECT status, publication_state FROM sports_ai_accumulator_tickets WHERE id=?', (ticket_id,),
            ).fetchone()
            measured = measure_pipeline(connection, NOW)
        self.assertEqual(ready_state['publication_state'], 'READY_FOR_WINDOW')
        self.assertFalse(ready['places_wager'])
        self.assertEqual(lapsed_state['status'], 'review_required')
        self.assertIsNone(lapsed_state['publication_state'])
        self.assertEqual(lapsed['demoted'], 1)
        self.assertFalse(measured['places_wager'])
        self.assertIn('QUALIFIED', {item['stage'] for item in measured['stages']})

    def _prediction(self, connection, kickoff, created, cutoff, generated):
        fixture_id, prediction_id = self._ids(connection, kickoff, created=created, cutoff=cutoff, generated=generated)
        return prediction_id if created >= kickoff else prediction_id if cutoff != kickoff else prediction_id

    def _ids(self, connection, kickoff, created=None, cutoff=None, generated=None, edge=0.18, probability=0.62, odds=1.9):
        created = created or (NOW - timedelta(hours=1))
        cutoff = cutoff or kickoff
        generated = generated or created
        fixture_id, prediction_id = str(uuid.uuid4()), str(uuid.uuid4())
        model_id = connection.execute("SELECT id FROM sports_ai_model_registry WHERE active=1").fetchone()
        if not model_id:
            model_id = self._model(connection)
        else:
            model_id = model_id['id']
        run = connection.execute('SELECT id FROM sports_ai_analysis_runs LIMIT 1').fetchone()
        if not run:
            run_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO sports_ai_analysis_runs (id,fixture_count,selection_count,ticket_count,status) VALUES(?,0,0,0,'complete')",
                (run_id,),
            )
        else:
            run_id = run['id']
        feature_id = str(uuid.uuid4())
        connection.execute(
            """INSERT INTO sports_ai_fixtures
               (id,external_id,source,sport,competition,home,away,starts_at,status,statistics_json,odds_json,observed_at,canonical_key)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (fixture_id, fixture_id, 'test', 'football', 'League', 'Home', 'Away', kickoff.isoformat(), 'scheduled', '{}', '{}', created.isoformat(), fixture_id),
        )
        connection.execute(
            """INSERT INTO sports_ai_feature_snapshots
               (id,fixture_id,sport,cutoff_at,features_json,source_refs_json,generated_at,completeness)
               VALUES(?,?,?,?,?,?,?,?)""",
            (feature_id, fixture_id, 'football', cutoff.isoformat(), '{}', '[]', generated.isoformat(), 0.8),
        )
        settlement = {'p_win': probability, 'p_half_win': 0, 'p_push': 0, 'p_half_loss': 0, 'p_loss': round(1 - probability, 6)}
        connection.execute(
            """INSERT INTO sports_ai_engine_predictions
               (id,run_id,fixture_id,model_id,feature_id,market,outcome,probability,settlement_json,explanation_json,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                prediction_id, run_id, fixture_id, model_id, feature_id, 'HOME', 'home', probability,
                __import__('json').dumps(settlement), '{}', created.isoformat(),
            ),
        )
        self.edge = edge
        self.odds = odds
        return fixture_id, prediction_id

    def _model(self, connection, version='v-test', active=True):
        run_id, model_id = str(uuid.uuid4()), str(uuid.uuid4())
        connection.execute(
            """INSERT INTO sports_ai_training_runs
               (id,sport,model_name,version,status,training_window_json,parameters_json,metrics_json,sample_size)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (run_id, 'football', 'sports-ai-football-ensemble', version, 'validated', '{}', '{}', '{}', 40),
        )
        connection.execute(
            """INSERT INTO sports_ai_model_registry
               (id,training_run_id,sport,model_name,version,status,active,parameters_json,metrics_json,lifecycle,calibration_status)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                model_id, run_id, 'football', 'sports-ai-football-ensemble', version, 'active', int(active), '{}',
                '{"calibration_error":0.04,"holdout_fixtures":40,"brier_score":0.2}', 'active', 'validated',
            ),
        )
        return model_id

    def _quote(self, connection, fixture_id, observed):
        provider = connection.execute("SELECT id FROM sports_ai_provider_registry LIMIT 1").fetchone()
        if not provider:
            provider_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO sports_ai_provider_registry
                   (id,provider_key,display_name,sport,adapter,enabled,terms_reviewed,status)
                   VALUES(?,?,?,?,?,1,1,'operational')""",
                (provider_id, 'sportybet-test', 'SportyBet', 'football', 'authorized_json'),
            )
        else:
            provider_id = provider['id']
        odds_id = str(uuid.uuid4())
        connection.execute(
            """INSERT INTO sports_ai_odds_snapshots
               (id,fixture_id,provider_id,bookmaker,market,outcome,decimal_odds,source_url,observed_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (odds_id, fixture_id, provider_id, 'SportyBet', 'h2h', 'home', 1.9, 'https://www.sportybet.com/ng/sport/football', observed.isoformat()),
        )
        return odds_id
