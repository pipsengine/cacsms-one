ALTER TABLE sports_ai_model_registry ADD COLUMN lifecycle TEXT NOT NULL DEFAULT 'research';

UPDATE sports_ai_model_registry SET lifecycle='active' WHERE active=1 AND status='active';
UPDATE sports_ai_model_registry SET lifecycle='validated' WHERE lifecycle='research' AND status='validated';
UPDATE sports_ai_model_registry SET lifecycle='suspended' WHERE status IN ('inactive','rejected');

CREATE TABLE IF NOT EXISTS sports_ai_market_suitability (
 id TEXT PRIMARY KEY,
 prediction_id TEXT NOT NULL UNIQUE REFERENCES sports_ai_engine_predictions(id) ON DELETE CASCADE,
 sport TEXT NOT NULL,
 market TEXT NOT NULL,
 suitable INTEGER NOT NULL CHECK(suitable IN (0,1)),
 booking_ready INTEGER NOT NULL CHECK(booking_ready IN (0,1)),
 rank_score REAL NOT NULL,
 reasons_json TEXT NOT NULL,
 missing_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS sports_ai_market_suitability_rank
 ON sports_ai_market_suitability(sport, market, rank_score);
