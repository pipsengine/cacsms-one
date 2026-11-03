ALTER TABLE sports_ai_schedules ADD COLUMN job_kind TEXT NOT NULL DEFAULT 'prediction';
ALTER TABLE sports_ai_schedules ADD COLUMN cron_expression TEXT;
ALTER TABLE sports_ai_schedules ADD COLUMN timezone TEXT NOT NULL DEFAULT 'UTC';
ALTER TABLE sports_ai_schedules ADD COLUMN next_run_at TEXT;

CREATE TABLE IF NOT EXISTS sports_ai_workflows (
 id TEXT PRIMARY KEY,
 fixture_id TEXT NOT NULL UNIQUE,
 state TEXT NOT NULL,
 source_ref TEXT NOT NULL DEFAULT '',
 model_id TEXT,
 job_id TEXT,
 reason TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sports_ai_workflow_events (
 id TEXT PRIMARY KEY,
 workflow_id TEXT NOT NULL REFERENCES sports_ai_workflows(id),
 fixture_id TEXT NOT NULL,
 from_state TEXT,
 to_state TEXT NOT NULL,
 reason TEXT NOT NULL,
 job_id TEXT,
 source_ref TEXT NOT NULL DEFAULT '',
 model_id TEXT,
 created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sports_ai_jobs (
 id TEXT PRIMARY KEY,
 idempotency_key TEXT NOT NULL UNIQUE,
 job_kind TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('queued','running','complete','failed','retry_pending','dead_letter','cancelled','skipped')),
 attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt>=0),
 max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 1 AND 8),
 available_at TEXT NOT NULL,
 timeout_seconds INTEGER NOT NULL DEFAULT 120 CHECK(timeout_seconds BETWEEN 5 AND 3600),
 lease_owner TEXT,
 lease_expires_at TEXT,
 payload_json TEXT NOT NULL DEFAULT '{}',
 result_json TEXT,
 error_code TEXT,
 error TEXT,
 schedule_id TEXT,
 created_at TEXT NOT NULL,
 started_at TEXT,
 finished_at TEXT
);

CREATE TABLE IF NOT EXISTS sports_ai_job_attempts (
 id TEXT PRIMARY KEY,
 job_id TEXT NOT NULL REFERENCES sports_ai_jobs(id),
 attempt INTEGER NOT NULL,
 status TEXT NOT NULL,
 error_code TEXT,
 error TEXT,
 started_at TEXT NOT NULL,
 finished_at TEXT
);

CREATE TABLE IF NOT EXISTS sports_ai_worker_leases (
 id TEXT PRIMARY KEY,
 owner TEXT NOT NULL,
 expires_at TEXT NOT NULL,
 heartbeat_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sports_ai_provider_health (
 id TEXT PRIMARY KEY,
 provider_id TEXT NOT NULL REFERENCES sports_ai_provider_registry(id) ON DELETE CASCADE,
 status TEXT NOT NULL,
 last_success_at TEXT,
 last_error TEXT,
 freshness_minutes REAL,
 consecutive_failures INTEGER NOT NULL DEFAULT 0,
 paused INTEGER NOT NULL DEFAULT 0 CHECK(paused IN (0,1)),
 details_json TEXT NOT NULL DEFAULT '{}',
 observed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sports_ai_data_quality_incidents (
 id TEXT PRIMARY KEY,
 fixture_id TEXT,
 provider_id TEXT,
 severity TEXT NOT NULL CHECK(severity IN ('low','medium','high','critical')),
 incident_type TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('open','quarantined','resolved')),
 details_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS sports_ai_model_training_runs (
 id TEXT PRIMARY KEY,
 training_run_id TEXT REFERENCES sports_ai_training_runs(id),
 sport TEXT NOT NULL,
 status TEXT NOT NULL,
 promoted INTEGER NOT NULL DEFAULT 0 CHECK(promoted IN (0,1)),
 dataset_version TEXT NOT NULL,
 details_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sports_ai_model_evaluations (
 id TEXT PRIMARY KEY,
 model_id TEXT,
 sport TEXT NOT NULL,
 market TEXT NOT NULL,
 sample_size INTEGER NOT NULL CHECK(sample_size>=0),
 brier_score REAL,
 log_loss REAL,
 calibration_error REAL,
 hit_rate REAL,
 coverage REAL,
 completeness REAL,
 metrics_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sports_ai_model_drift_events (
 id TEXT PRIMARY KEY,
 model_id TEXT,
 sport TEXT NOT NULL,
 market TEXT NOT NULL,
 drift_type TEXT NOT NULL,
 metric_value REAL,
 threshold REAL NOT NULL,
 details_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sports_ai_model_promotions (
 id TEXT PRIMARY KEY,
 model_id TEXT NOT NULL,
 action TEXT NOT NULL CHECK(action IN ('promote','reject','rollback')),
 approved_by TEXT,
 status TEXT NOT NULL,
 reason TEXT NOT NULL,
 created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sports_ai_notification_rules (
 id TEXT PRIMARY KEY,
 event_type TEXT NOT NULL,
 channel TEXT NOT NULL CHECK(channel IN ('dashboard','email','telegram')),
 enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
 template TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(event_type,channel)
);

CREATE TABLE IF NOT EXISTS sports_ai_notification_deliveries (
 id TEXT PRIMARY KEY,
 rule_id TEXT REFERENCES sports_ai_notification_rules(id),
 event_type TEXT NOT NULL,
 channel TEXT NOT NULL,
 entity_key TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('queued','sent','failed','not_configured','duplicate')),
 attempt INTEGER NOT NULL DEFAULT 1,
 provider_response TEXT,
 payload_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(event_type,channel,entity_key)
);

CREATE TABLE IF NOT EXISTS sports_ai_recovery_cases (
 id TEXT PRIMARY KEY,
 job_id TEXT REFERENCES sports_ai_jobs(id),
 fixture_id TEXT,
 error_code TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('open','retried','resolved','escalated')),
 attempt_count INTEGER NOT NULL,
 details_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS sports_ai_workflows_state ON sports_ai_workflows(state,updated_at);
CREATE INDEX IF NOT EXISTS sports_ai_workflow_events_fixture ON sports_ai_workflow_events(fixture_id,created_at);
CREATE INDEX IF NOT EXISTS sports_ai_jobs_status_available ON sports_ai_jobs(status,available_at);
CREATE INDEX IF NOT EXISTS sports_ai_provider_health_observed ON sports_ai_provider_health(provider_id,observed_at);
CREATE INDEX IF NOT EXISTS sports_ai_model_evaluations_created ON sports_ai_model_evaluations(sport,market,created_at);
CREATE INDEX IF NOT EXISTS sports_ai_recovery_cases_status ON sports_ai_recovery_cases(status,created_at);

INSERT OR IGNORE INTO sports_ai_worker_leases (id,owner,expires_at,heartbeat_at)
VALUES ('scheduler-leader','','1970-01-01T00:00:00+00:00','1970-01-01T00:00:00+00:00');

INSERT OR IGNORE INTO sports_ai_schedules
 (id,name,interval_minutes,enabled,job_kind,cron_expression,timezone)
VALUES
 ('schedule-discovery','Fixture discovery',360,0,'collection',NULL,'UTC'),
 ('schedule-prediction','Prediction generation',60,0,'prediction','0 * * * *','UTC'),
 ('schedule-accumulator','Accumulator optimization',60,0,'accumulator',NULL,'UTC'),
 ('schedule-settlement','Result settlement',30,0,'settlement',NULL,'UTC'),
 ('schedule-model-evaluation','Model performance evaluation',1440,0,'model_evaluation','0 6 * * *','UTC');

INSERT OR IGNORE INTO sports_ai_notification_rules (id,event_type,channel,enabled,template)
VALUES
 ('rule-qualified-dashboard','qualified_opportunity','dashboard',0,'Qualified opportunity {entity_key}'),
 ('rule-ticket-dashboard','ticket_generated','dashboard',0,'Accumulator ticket {entity_key}'),
 ('rule-provider-dashboard','provider_unavailable','dashboard',0,'Provider needs attention {entity_key}'),
 ('rule-drift-dashboard','model_drift','dashboard',0,'Model drift {entity_key}'),
 ('rule-summary-email','daily_summary','email',0,'Daily Sports AI summary'),
 ('rule-summary-telegram','daily_summary','telegram',0,'Daily Sports AI summary');
