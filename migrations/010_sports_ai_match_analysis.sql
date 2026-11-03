CREATE TABLE IF NOT EXISTS sports_ai_match_analyses (
 id TEXT PRIMARY KEY,
 fixture_id TEXT NOT NULL REFERENCES sports_ai_fixtures(id) ON DELETE CASCADE,
 status TEXT NOT NULL CHECK(status IN ('complete','descriptive','failed')),
 report_json TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS sports_ai_match_analyses_fixture
 ON sports_ai_match_analyses(fixture_id, created_at);
