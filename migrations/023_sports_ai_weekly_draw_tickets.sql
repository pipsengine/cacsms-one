CREATE TABLE IF NOT EXISTS sports_ai_weekly_draw_watch (
 fixture_id TEXT NOT NULL,
 week_id TEXT NOT NULL,
 mode TEXT NOT NULL CHECK(mode IN ('WEEKLY','SATURDAY_ONLY')),
 home TEXT NOT NULL,
 away TEXT NOT NULL,
 competition TEXT NOT NULL,
 starts_at TEXT NOT NULL,
 state TEXT NOT NULL,
 detail_json TEXT NOT NULL,
 prepared_at TEXT NOT NULL,
 PRIMARY KEY (fixture_id, week_id, mode)
);

CREATE INDEX IF NOT EXISTS sports_ai_weekly_draw_watch_week
 ON sports_ai_weekly_draw_watch(week_id, mode, starts_at);

INSERT OR IGNORE INTO sports_ai_schedules
 (id,name,interval_minutes,enabled,job_kind,cron_expression,timezone)
VALUES
 ('schedule-weekly-discovery','Weekly draw discovery',30,1,'weekly_draw',NULL,'WAT'),
 ('schedule-saturday-watchlist','Friday Saturday draw watchlist',30,1,'weekly_draw',NULL,'WAT'),
 ('schedule-saturday-revalidation','Saturday draw revalidation',30,1,'weekly_draw',NULL,'WAT');
