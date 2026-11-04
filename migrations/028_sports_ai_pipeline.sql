ALTER TABLE sports_ai_engine_predictions ADD COLUMN observation_class TEXT;
CREATE INDEX IF NOT EXISTS sports_ai_engine_predictions_observation
 ON sports_ai_engine_predictions(observation_class, created_at);

ALTER TABLE sports_ai_accumulator_tickets ADD COLUMN publication_state TEXT;

CREATE TABLE IF NOT EXISTS sports_ai_pipeline_snapshots (
 id TEXT PRIMARY KEY,
 created_at TEXT NOT NULL,
 stages_json TEXT NOT NULL,
 blockers_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sports_ai_pipeline_snapshots_created
 ON sports_ai_pipeline_snapshots(created_at);

CREATE TABLE IF NOT EXISTS sports_ai_odds_refresh_queue (
 fixture_id TEXT PRIMARY KEY,
 priority REAL NOT NULL,
 status TEXT NOT NULL,
 attempt INTEGER NOT NULL DEFAULT 0,
 next_attempt_at TEXT,
 last_error TEXT,
 reason TEXT,
 updated_at TEXT NOT NULL
);
