CREATE TABLE IF NOT EXISTS sports_ai_booking_jobs (
 id TEXT PRIMARY KEY,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 status TEXT NOT NULL,
 sport TEXT NOT NULL,
 selections_json TEXT NOT NULL,
 signature TEXT NOT NULL,
 code TEXT,
 screenshot TEXT,
 error TEXT,
 odds_json TEXT,
 places_wager INTEGER NOT NULL DEFAULT 0 CHECK(places_wager = 0)
);

CREATE INDEX IF NOT EXISTS sports_ai_booking_jobs_status
 ON sports_ai_booking_jobs(status, updated_at);

INSERT OR IGNORE INTO sports_ai_schedules
 (id,name,interval_minutes,enabled,job_kind,cron_expression,timezone)
VALUES
 ('schedule-booking','SportyBet booking codes',15,1,'booking_queue',NULL,'UTC');
