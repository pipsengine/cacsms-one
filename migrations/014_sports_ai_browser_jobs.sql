CREATE TABLE IF NOT EXISTS sports_ai_browser_jobs (
 id TEXT PRIMARY KEY,
 url TEXT NOT NULL,
 status TEXT NOT NULL,
 created_at TEXT NOT NULL,
 started_at TEXT,
 finished_at TEXT,
 heartbeat_at TEXT,
 error TEXT,
 title TEXT,
 observations INTEGER NOT NULL DEFAULT 0,
 attempt INTEGER NOT NULL DEFAULT 0,
 max_attempts INTEGER NOT NULL DEFAULT 3,
 next_attempt_at TEXT
);

CREATE TABLE IF NOT EXISTS sports_ai_browser_observations (
 id TEXT PRIMARY KEY,
 job_id TEXT NOT NULL REFERENCES sports_ai_browser_jobs(id),
 source_url TEXT NOT NULL,
 fixture_name TEXT NOT NULL,
 market TEXT NOT NULL,
 selection TEXT NOT NULL,
 decimal_odds REAL NOT NULL CHECK(decimal_odds > 1),
 observed_at TEXT NOT NULL,
 confirmed INTEGER NOT NULL DEFAULT 0 CHECK(confirmed IN (0,1))
);

CREATE INDEX IF NOT EXISTS sports_ai_browser_jobs_status
 ON sports_ai_browser_jobs(status, created_at);

CREATE INDEX IF NOT EXISTS sports_ai_browser_observations_job
 ON sports_ai_browser_observations(job_id);

INSERT OR IGNORE INTO sports_ai_schedules
 (id,name,interval_minutes,enabled,job_kind,cron_expression,timezone)
VALUES
 ('schedule-browser','Authorized browser research',15,1,'browser_queue',NULL,'UTC');
