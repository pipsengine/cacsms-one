ALTER TABLE sports_ai_booking_jobs ADD COLUMN attempt INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sports_ai_booking_jobs ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3;
ALTER TABLE sports_ai_booking_jobs ADD COLUMN session_id TEXT;

CREATE TABLE IF NOT EXISTS sports_ai_booking_logs (
 id TEXT PRIMARY KEY,
 job_id TEXT,
 ticket_id TEXT,
 session_id TEXT,
 phase TEXT NOT NULL,
 level TEXT NOT NULL,
 message TEXT NOT NULL,
 detail_json TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS sports_ai_booking_logs_job
 ON sports_ai_booking_logs(job_id, created_at);

INSERT OR IGNORE INTO sports_ai_schedules
 (id,name,interval_minutes,enabled,job_kind,cron_expression,timezone)
VALUES
 ('schedule-wat-discovery','WAT overnight discovery',30,1,'wat_window',NULL,'WAT'),
 ('schedule-wat-qualification','WAT qualification',30,1,'wat_window',NULL,'WAT'),
 ('schedule-wat-publication','WAT primary publication',30,1,'wat_window',NULL,'WAT'),
 ('schedule-wat-midday','WAT midday reassessment',30,1,'wat_window',NULL,'WAT'),
 ('schedule-wat-evening','WAT evening reassessment',30,1,'wat_window',NULL,'WAT');
