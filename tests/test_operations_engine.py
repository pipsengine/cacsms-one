import json
import os
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import db
from operations_engine import (
    acquire_leader,
    browser_research_status,
    claim_job,
    command_centre,
    complete_job,
    cron_is_due,
    enqueue_job,
    evaluate_models,
    keep_leader,
    evaluate_retraining,
    fail_job,
    orchestrate_once,
    recover_stuck_jobs,
    redact,
    refresh_provider_health,
    run_due_schedules,
    sync_workflows,
    transition,
    ensure_workflow,
)


def _audit(connection, actor, action, details):
    connection.execute(
        'INSERT INTO sports_ai_audit_events(id,actor,action,metadata_json) VALUES(?,?,?,?)',
        (str(uuid.uuid4()), actor, action, json.dumps(details)),
    )


class OperationsEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_directory = tempfile.TemporaryDirectory()
        cls.database_path = Path(cls.temp_directory.name) / 'sports-ai.db'
        cls.previous_database = db.DB
        db.DB = cls.database_path
        db.initialize()

    @classmethod
    def tearDownClass(cls):
        db.DB = cls.previous_database
        cls.temp_directory.cleanup()

    def setUp(self):
        self.now = datetime(2026, 11, 3, 8, tzinfo=timezone.utc)
        with db.connect() as connection:
            for table in (
                'sports_ai_notification_deliveries', 'sports_ai_recovery_cases', 'sports_ai_job_attempts',
                'sports_ai_jobs', 'sports_ai_workflow_events', 'sports_ai_workflows',
                'sports_ai_provider_health', 'sports_ai_data_quality_incidents', 'sports_ai_model_drift_events',
                'sports_ai_model_evaluations', 'sports_ai_model_promotions', 'sports_ai_model_training_runs',
                'sports_ai_ticket_settlements', 'sports_ai_ticket_monitoring_events', 'sports_ai_accumulator_legs',
                'sports_ai_accumulator_tickets', 'sports_ai_value_opportunities', 'sports_ai_prediction_explanations',
                'sports_ai_engine_predictions', 'sports_ai_feature_snapshots', 'sports_ai_calibration_results',
                'sports_ai_validation_results', 'sports_ai_training_datasets', 'sports_ai_model_versions',
                'sports_ai_model_registry', 'sports_ai_training_runs', 'sports_ai_prediction_jobs',
                'sports_ai_odds_snapshots', 'sports_ai_analysis_runs', 'sports_ai_results', 'sports_ai_fixtures',
                'sports_ai_acquisition_jobs', 'sports_ai_acquisition_schedules', 'sports_ai_provider_registry',
                'sports_ai_source_permissions', 'sports_ai_job_history',
            ):
                connection.execute(f'DELETE FROM {table}')
            connection.execute(
                """UPDATE sports_ai_worker_leases
                   SET owner='', expires_at='1970-01-01T00:00:00+00:00', heartbeat_at='1970-01-01T00:00:00+00:00'
                   WHERE id='scheduler-leader'""",
            )
            connection.execute('UPDATE sports_ai_schedules SET enabled=0, last_run_at=NULL')
            connection.execute('UPDATE sports_ai_notification_rules SET enabled=0')

    def _fixture(self, connection, fixture_id, kickoff):
        connection.execute(
            """INSERT INTO sports_ai_fixtures
               (id,external_id,source,sport,competition,home,away,starts_at,status,statistics_json,odds_json,observed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (fixture_id, fixture_id, 'test-source', 'football', 'League', 'Home', 'Away',
             kickoff, 'scheduled', '{}', '{}', self.now.isoformat()),
        )

    def _model(self, connection):
        run_id, model_id = str(uuid.uuid4()), str(uuid.uuid4())
        connection.execute(
            """INSERT INTO sports_ai_training_runs
               (id,sport,model_name,version,status,training_window_json,parameters_json,metrics_json,sample_size)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (run_id, 'football', 'sports-ai-football-ensemble', 'ops-1', 'insufficient_data', '{}', '{}', '{}', 0),
        )
        connection.execute(
            """INSERT INTO sports_ai_model_registry
               (id,training_run_id,sport,model_name,version,status,active,parameters_json,metrics_json)
               VALUES(?,?,?,?,?,?,0,?,?)""",
            (model_id, run_id, 'football', 'sports-ai-football-ensemble', 'ops-1', 'unvalidated', '{}', '{}'),
        )
        analysis_id = str(uuid.uuid4())
        connection.execute(
            """INSERT INTO sports_ai_analysis_runs (id,fixture_count,selection_count,ticket_count,status)
               VALUES(?,0,0,0,'complete')""",
            (analysis_id,),
        )
        return model_id, analysis_id

    def _prediction(self, connection, model_id, analysis_id, created, kickoff, settled, probability, home, away):
        fixture_id = str(uuid.uuid4())
        feature_id = str(uuid.uuid4())
        self._fixture(connection, fixture_id, kickoff.isoformat())
        connection.execute(
            """INSERT INTO sports_ai_results
               (id,fixture_id,home_score,away_score,source,status,settled_at)
               VALUES(?,?,?,?,?,'final',?)""",
            (str(uuid.uuid4()), fixture_id, home, away, 'verified', settled.isoformat()),
        )
        connection.execute(
            """INSERT INTO sports_ai_feature_snapshots
               (id,fixture_id,sport,cutoff_at,features_json,source_refs_json,generated_at)
               VALUES(?,?,?,?,?,?,?)""",
            (feature_id, fixture_id, 'football', (kickoff - timedelta(hours=1)).isoformat(), '{}', '[]', created.isoformat()),
        )
        connection.execute(
            """INSERT INTO sports_ai_engine_predictions
               (id,run_id,fixture_id,model_id,feature_id,market,outcome,probability,settlement_json,explanation_json,created_at)
               VALUES(?,?,?,?,?,'HOME','HOME',?,?,?,?)""",
            (str(uuid.uuid4()), analysis_id, fixture_id, model_id, feature_id, probability,
             json.dumps({'p_win': probability, 'p_loss': 1 - probability}), '{}', created.isoformat()),
        )

    def test_invalid_transition_is_rejected_and_legal_path_is_recorded(self):
        with db.connect() as connection:
            self._fixture(connection, 'fixture-1', (self.now + timedelta(days=2)).isoformat())
            workflow = ensure_workflow(connection, 'fixture-1', 'test-source', self.now)
            with self.assertRaises(ValueError):
                transition(connection, workflow['id'], 'SETTLED', 'not a legal step', self.now)
            sync_workflows(connection, self.now)
            state = connection.execute('SELECT state FROM sports_ai_workflows WHERE fixture_id=?', ('fixture-1',)).fetchone()['state']
            events = connection.execute(
                'SELECT COUNT(*) FROM sports_ai_workflow_events WHERE workflow_id=?', (workflow['id'],),
            ).fetchone()[0]
        self.assertEqual(state, 'DATA_COLLECTION_PENDING')
        self.assertGreaterEqual(events, 2)

    def test_one_scheduler_leader_and_idempotent_jobs(self):
        with db.connect() as connection:
            self.assertTrue(acquire_leader(connection, 'worker-a', self.now))
            self.assertFalse(acquire_leader(connection, 'worker-b', self.now))
            self.assertTrue(keep_leader(connection, 'worker-a', self.now + timedelta(seconds=10)))
            self.assertFalse(keep_leader(connection, 'worker-b', self.now + timedelta(seconds=10)))
            self.assertTrue(keep_leader(connection, 'worker-c', self.now + timedelta(seconds=120)))
            first = enqueue_job(connection, 'model_evaluation', 'same-key', now=self.now, max_attempts=3)
            second = enqueue_job(connection, 'model_evaluation', 'same-key', now=self.now, max_attempts=3)
            self.assertFalse(second['created'])
            self.assertEqual(first['id'], second['id'])
            claimed = claim_job(connection, 'worker-a', self.now)
            self.assertEqual(fail_job(connection, claimed['id'], 'UNHANDLED', 'temporary', self.now), 'retry_pending')
            available = connection.execute('SELECT available_at,status FROM sports_ai_jobs WHERE id=?', (claimed['id'],)).fetchone()
            self.assertEqual(available['status'], 'retry_pending')
            self.assertGreater(available['available_at'], self.now.isoformat())
            final = enqueue_job(connection, 'model_evaluation', 'final-key', now=self.now, max_attempts=1)
            claim_job(connection, 'worker-a', self.now)
            self.assertEqual(fail_job(connection, final['id'], 'UNHANDLED', 'exhausted', self.now), 'dead_letter')
            case = connection.execute('SELECT * FROM sports_ai_recovery_cases WHERE job_id=?', (final['id'],)).fetchone()
            self.assertEqual(case['error_code'], 'UNHANDLED')
            self.assertGreaterEqual(case['attempt_count'], 1)
            stuck = enqueue_job(connection, 'model_evaluation', 'stuck-key', now=self.now, max_attempts=3)
            connection.execute(
                """UPDATE sports_ai_jobs
                   SET status='running', attempt=1, lease_expires_at=?
                   WHERE id=?""",
                ((self.now - timedelta(minutes=5)).isoformat(), stuck['id']),
            )
            self.assertEqual(recover_stuck_jobs(connection, self.now), 1)
            recovered = connection.execute('SELECT status,error_code FROM sports_ai_jobs WHERE id=?', (stuck['id'],)).fetchone()
            self.assertEqual(recovered['status'], 'retry_pending')
            self.assertEqual(recovered['error_code'], 'LEASE_EXPIRED')

    def test_cron_window_and_duplicate_schedule_bucket(self):
        slot = self.now.replace(minute=0)
        self.assertTrue(cron_is_due('0 * * * *', None, slot))
        self.assertFalse(cron_is_due('0 * * * *', None, slot.replace(minute=15)))
        with self.assertRaises(ValueError):
            cron_is_due('0 6 1 * *', None, slot)
        schedule_id = 'schedule-ops-test'
        with db.connect() as connection:
            connection.execute(
                """INSERT INTO sports_ai_schedules
                   (id,name,interval_minutes,enabled,job_kind,timezone,last_run_at)
                   VALUES(?,?,60,1,'model_evaluation','UTC',?)""",
                (schedule_id, 'Ops evaluation', (self.now - timedelta(hours=2)).isoformat()),
            )
            bucket = int(self.now.timestamp() // (60 * 60))
            job = enqueue_job(connection, 'model_evaluation', f'schedule:{schedule_id}:{bucket}', now=self.now)
            complete_job(connection, job['id'], {'status': 'complete'}, 'complete', self.now)
            first = run_due_schedules(connection, _audit, self.now)
        self.assertEqual(first[0]['status'], 'duplicate')

    def test_provider_failures_pause_the_source(self):
        with db.connect() as connection:
            connection.execute(
                """INSERT INTO sports_ai_provider_registry
                   (id,provider_key,display_name,sport,adapter,enabled,terms_reviewed,status)
                   VALUES('provider-ops','ops','Ops Feed','football','manual',1,1,'ready')""",
            )
            connection.execute(
                """INSERT INTO sports_ai_acquisition_schedules
                   (provider_id,enabled,interval_minutes,paused,updated_by,updated_at)
                   VALUES('provider-ops',1,360,0,'test',?)""",
                (self.now.isoformat(),),
            )
            for index in range(3):
                connection.execute(
                    """INSERT INTO sports_ai_acquisition_jobs
                       (id,provider_id,kind,status,started_at,error)
                       VALUES(?,?,?,?,?,?)""",
                    (str(uuid.uuid4()), 'provider-ops', 'collection', 'failed',
                     (self.now - timedelta(minutes=index)).isoformat(), 'source unavailable'),
                )
            refresh_provider_health(connection, self.now)
            paused = connection.execute(
                'SELECT paused FROM sports_ai_acquisition_schedules WHERE provider_id=?', ('provider-ops',),
            ).fetchone()['paused']
        self.assertEqual(paused, 1)

    def test_evaluation_excludes_future_predictions_and_does_not_promote(self):
        kickoff = self.now - timedelta(days=1)
        with db.connect() as connection:
            model_id, analysis_id = self._model(connection)
            self._prediction(connection, model_id, analysis_id, kickoff - timedelta(hours=2), kickoff, kickoff + timedelta(hours=2), 0.8, 2, 0)
            self._prediction(connection, model_id, analysis_id, kickoff + timedelta(minutes=10), kickoff, kickoff + timedelta(hours=2), 0.8, 2, 0)
            report = evaluate_models(connection, self.now)
            active = connection.execute('SELECT active FROM sports_ai_model_registry WHERE id=?', (model_id,)).fetchone()['active']
        self.assertEqual(report['excluded'], 1)
        self.assertEqual(report['evaluations'][0]['sample_size'], 1)
        self.assertAlmostEqual(report['evaluations'][0]['brier_score'], 0.04)
        self.assertFalse(report['promoted'])
        self.assertEqual(active, 0)
        self.assertIn('not an expected-return', report['evaluations'][0]['note'])

    def test_drift_is_recorded_without_changing_the_active_model(self):
        kickoff = self.now - timedelta(days=2)
        with db.connect() as connection:
            model_id, analysis_id = self._model(connection)
            for _ in range(20):
                self._prediction(
                    connection, model_id, analysis_id, kickoff - timedelta(hours=3), kickoff,
                    kickoff + timedelta(hours=2), 0.9, 0, 1,
                )
            evaluate_models(connection, self.now)
            drift = connection.execute('SELECT COUNT(*) FROM sports_ai_model_drift_events').fetchone()[0]
            active = connection.execute('SELECT SUM(active) FROM sports_ai_model_registry').fetchone()[0]
        self.assertEqual(drift, 1)
        self.assertEqual(active, 0)

    def test_retraining_stays_unpromoted_and_notifications_need_acceptance(self):
        with db.connect() as connection:
            result = evaluate_retraining(connection, 'football', 'tester', _audit)
            stored = connection.execute(
                'SELECT promoted,training_run_id FROM sports_ai_model_training_runs',
            ).fetchone()
            self.assertFalse(result['promoted'])
            self.assertEqual(result['active'], False)
            self.assertEqual(stored['promoted'], 0)
            self.assertEqual(stored['training_run_id'], result['training_run_id'])
            connection.execute("UPDATE sports_ai_notification_rules SET enabled=1 WHERE id='rule-qualified-dashboard'")
            rule = dict(connection.execute("SELECT * FROM sports_ai_notification_rules WHERE id='rule-qualified-dashboard'").fetchone())
            from operations_engine import deliver_notification
            self.assertEqual(deliver_notification(connection, rule, 'opp-1', {'opportunity_id': 'opp-1'}, self.now), 'sent')
            self.assertEqual(deliver_notification(connection, rule, 'opp-1', {'opportunity_id': 'opp-1'}, self.now), 'duplicate')
            email = dict(connection.execute("SELECT * FROM sports_ai_notification_rules WHERE id='rule-summary-email'").fetchone())
            saved = {name: os.environ.pop(name, None) for name in ('SPORTS_SMTP_HOST', 'SPORTS_SMTP_FROM', 'SPORTS_ALERT_EMAIL')}
            try:
                status = deliver_notification(connection, email, 'summary-1', {'summary': True}, self.now)
            finally:
                for name, value in saved.items():
                    if value is not None:
                        os.environ[name] = value
            self.assertEqual(status, 'not_configured')
            self.assertNotEqual(status, 'sent')

    def test_second_worker_is_skipped_and_daily_evaluation_is_not_duplicated(self):
        first = orchestrate_once(_audit, owner='worker-a', now=self.now)
        second = orchestrate_once(_audit, owner='worker-b', now=self.now)
        orchestrate_once(_audit, owner='worker-a', now=self.now + timedelta(seconds=1))
        with db.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM sports_ai_jobs WHERE idempotency_key='model-evaluation:2026-11-03'",
            ).fetchone()[0]
            centre = command_centre(connection)
            mode = connection.execute('PRAGMA journal_mode').fetchone()[0]
        self.assertEqual(first['status'], 'complete')
        self.assertEqual(second['status'], 'skipped')
        self.assertEqual(count, 1)
        self.assertEqual(centre['fixtures'], 0)
        self.assertIn('not evidence of positive expected return', centre['hit_rate_note'])
        self.assertEqual(str(mode).lower(), 'wal')

    def test_browser_research_stays_manual_and_secrets_are_redacted(self):
        with db.connect() as connection:
            connection.execute(
                """INSERT INTO sports_ai_source_permissions
                   (id,hostname,terms_url,approved,reviewed_at)
                   VALUES('perm-bet','www.betexplorer.com','https://www.betexplorer.com',1,?)""",
                (self.now.isoformat(),),
            )
            blocked = browser_research_status(connection)
            connection.execute(
                """INSERT INTO sports_ai_source_permissions
                   (id,hostname,terms_url,approved,reviewed_at)
                   VALUES('perm-ok','example.com','https://example.com/terms',1,?)""",
                (self.now.isoformat(),),
            )
            allowed = browser_research_status(connection)
        self.assertEqual(blocked['approved_sources'], 0)
        self.assertEqual(blocked['status'], 'skipped')
        self.assertIn('BetExplorer', blocked['reason'])
        self.assertEqual(allowed['approved_sources'], 1)
        self.assertEqual(allowed['status'], 'skipped')
        self.assertEqual(redact({'smtp_password': 'secret', 'note': 'visible'})['smtp_password'], '[redacted]')
        self.assertEqual(redact({'smtp_password': 'secret', 'note': 'visible'})['note'], 'visible')

    def test_migration_does_not_remove_an_existing_traders_table(self):
        migration = (Path(__file__).resolve().parents[1] / 'migrations' / '007_sports_ai_operations_engine.sql').read_text(encoding='utf-8')
        self.assertNotIn('DROP TABLE', migration.upper())
        self.assertNotIn('traders', migration.lower())
        with db.connect() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS cacsms_traders_positions (id TEXT PRIMARY KEY, qty INTEGER)')
            connection.execute("INSERT INTO cacsms_traders_positions (id,qty) VALUES('kept',4)")
        db.initialize()
        with db.connect() as connection:
            qty = connection.execute("SELECT qty FROM cacsms_traders_positions WHERE id='kept'").fetchone()['qty']
            workflows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='sports_ai_workflows'",
            ).fetchone()
        self.assertEqual(qty, 4)
        self.assertIsNotNone(workflows)

    def test_web_process_does_not_start_the_scheduler(self):
        source = (Path(__file__).resolve().parents[1] / 'backend' / 'main.py').read_text(encoding='utf-8')
        self.assertNotIn('orchestrate_once', source)
        self.assertNotIn('worker.tick', source)
