CREATE TABLE IF NOT EXISTS sports_ai_market_registry (
 id TEXT PRIMARY KEY,
 sport TEXT NOT NULL,
 market_key TEXT NOT NULL,
 selection TEXT NOT NULL,
 period TEXT NOT NULL,
 line_value REAL,
 settlement_rule TEXT NOT NULL,
 data_requirements TEXT NOT NULL,
 model_source TEXT NOT NULL,
 model_version TEXT,
 status TEXT NOT NULL CHECK(status IN ('research','validated','active','suspended','blocked','unsupported')),
 evidence_json TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(sport, market_key)
);

CREATE INDEX IF NOT EXISTS sports_ai_market_registry_status
 ON sports_ai_market_registry(sport, status);

CREATE TABLE IF NOT EXISTS sports_ai_market_recommendations (
 fixture_id TEXT PRIMARY KEY,
 prediction_id TEXT,
 market TEXT NOT NULL,
 probability REAL,
 expected_edge REAL,
 reason TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
