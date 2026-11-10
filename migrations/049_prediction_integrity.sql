-- Prediction integrity, conservative EV, capability levels and closing-line value.
-- Additive only: new tables and indexes on those new tables. No existing table is rewritten.

-- One immutable row per distinct (prediction, market, price, probability, model, policy).
-- Qualification evidence, opportunity views and booking jobs reference it by id.
CREATE TABLE IF NOT EXISTS sports_ai_probability_records (
 id TEXT PRIMARY KEY,
 prediction_id TEXT NOT NULL,
 fixture_id TEXT NOT NULL,
 sport TEXT NOT NULL,
 competition TEXT NOT NULL DEFAULT '',
 market TEXT NOT NULL,
 model_id TEXT,
 model_version TEXT,
 feature_id TEXT,
 prediction_created_at TEXT,
 odds_snapshot_id TEXT,
 bookmaker TEXT,
 decimal_odds REAL,
 odds_observed_at TEXT,
 model_probability REAL,
 consensus_probability REAL,
 consensus_sources INTEGER NOT NULL DEFAULT 0,
 blended_probability REAL,
 uncertainty_margin REAL,
 conservative_probability REAL,
 break_even REAL,
 point_ev REAL,
 conservative_ev REAL,
 capability_level TEXT,
 integrity_status TEXT NOT NULL CHECK(integrity_status IN ('ok','quarantined')),
 integrity_issues_json TEXT NOT NULL DEFAULT '[]',
 evidence_json TEXT NOT NULL DEFAULT '{}',
 policy_version TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sports_ai_probability_records_prediction
 ON sports_ai_probability_records(prediction_id, market, created_at);
CREATE INDEX IF NOT EXISTS sports_ai_probability_records_fixture
 ON sports_ai_probability_records(fixture_id);

CREATE TRIGGER IF NOT EXISTS sports_ai_probability_records_immutable
BEFORE UPDATE ON sports_ai_probability_records
BEGIN
 SELECT RAISE(ABORT, 'probability records are immutable');
END;

-- DISCOVERY / RESEARCH / ELIGIBLE per sport-market (competition='') and per sport-league-market.
CREATE TABLE IF NOT EXISTS sports_ai_capability_levels (
 id TEXT PRIMARY KEY,
 sport TEXT NOT NULL,
 competition TEXT NOT NULL DEFAULT '',
 market TEXT NOT NULL,
 level TEXT NOT NULL CHECK(level IN ('DISCOVERY','RESEARCH','ELIGIBLE')),
 reason TEXT NOT NULL,
 evidence_json TEXT NOT NULL DEFAULT '{}',
 changed_at TEXT NOT NULL,
 evaluated_at TEXT NOT NULL,
 UNIQUE(sport, competition, market)
);

-- Closing-line value for paper-traded, qualified and booked selections.
CREATE TABLE IF NOT EXISTS sports_ai_clv_tracking (
 id TEXT PRIMARY KEY,
 prediction_id TEXT NOT NULL,
 market TEXT NOT NULL,
 fixture_id TEXT NOT NULL,
 sport TEXT NOT NULL,
 competition TEXT NOT NULL DEFAULT '',
 starts_at TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('paper','qualified','booked')),
 model_version TEXT,
 probability_record_id TEXT,
 probability REAL,
 conservative_ev REAL,
 bookmaker TEXT NOT NULL,
 quote_market TEXT NOT NULL,
 quote_outcome TEXT NOT NULL,
 quote_line REAL,
 settlement_period TEXT NOT NULL DEFAULT 'unknown',
 first_odds REAL NOT NULL,
 first_seen_at TEXT NOT NULL,
 qualification_odds REAL,
 qualified_at TEXT,
 booking_odds REAL,
 booked_at TEXT,
 taken_odds REAL NOT NULL,
 taken_at TEXT NOT NULL,
 closing_odds REAL,
 closing_observed_at TEXT,
 closing_fair_probability REAL,
 closing_overround REAL,
 clv_price REAL,
 clv_fair_ev REAL,
 status TEXT NOT NULL CHECK(status IN ('open','closed','no_valid_close')),
 settled_outcome TEXT,
 profit REAL,
 settled_at TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(prediction_id, market)
);
CREATE INDEX IF NOT EXISTS sports_ai_clv_tracking_status ON sports_ai_clv_tracking(status, starts_at);
CREATE INDEX IF NOT EXISTS sports_ai_clv_tracking_fixture ON sports_ai_clv_tracking(fixture_id);
