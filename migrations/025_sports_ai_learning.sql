CREATE TABLE IF NOT EXISTS sports_ai_learning_runs (
 id TEXT PRIMARY KEY,
 run_date TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL CHECK(status IN ('complete','failed')),
 leakage_excluded INTEGER NOT NULL CHECK(leakage_excluded>=0),
 selection_sample INTEGER NOT NULL CHECK(selection_sample>=0),
 ticket_sample INTEGER NOT NULL CHECK(ticket_sample>=0),
 summary_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sports_ai_learning_slices (
 id TEXT PRIMARY KEY,
 run_id TEXT NOT NULL REFERENCES sports_ai_learning_runs(id),
 report TEXT NOT NULL,
 metrics_json TEXT NOT NULL,
 UNIQUE(run_id, report)
);

CREATE TABLE IF NOT EXISTS sports_ai_learning_controls (
 id TEXT PRIMARY KEY,
 model_id TEXT NOT NULL,
 action TEXT NOT NULL CHECK(action IN ('suspend','recommend_retrain','hold')),
 status TEXT NOT NULL,
 reason TEXT NOT NULL,
 evidence_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);

INSERT OR IGNORE INTO sports_ai_schedules
 (id,name,interval_minutes,enabled,job_kind,cron_expression,timezone)
VALUES
 ('schedule-learning-evaluation','Daily performance evaluation',1440,1,'learning','','UTC');
