CREATE TABLE IF NOT EXISTS sports_ai_analysis_coverage (
 fixture_id TEXT PRIMARY KEY REFERENCES sports_ai_fixtures(id) ON DELETE CASCADE,
 sport TEXT NOT NULL,
 competition TEXT NOT NULL,
 status TEXT NOT NULL,
 reason TEXT NOT NULL,
 model_id TEXT,
 attempted_at TEXT NOT NULL,
 retry_after TEXT NOT NULL,
 pipeline_version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sports_ai_analysis_coverage_retry
 ON sports_ai_analysis_coverage(retry_after,sport);
CREATE INDEX IF NOT EXISTS sports_ai_result_settlement
 ON sports_ai_results(status,settled_at,fixture_id);
INSERT OR IGNORE INTO sports_ai_settings(key,value_json,updated_at)
 VALUES('catalog_backfill','{"per_day":8}',CURRENT_TIMESTAMP);
