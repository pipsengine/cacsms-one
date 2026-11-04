CREATE TABLE IF NOT EXISTS sports_ai_qualification_policies (
 id TEXT PRIMARY KEY,
 version INTEGER NOT NULL UNIQUE,
 policy_json TEXT NOT NULL,
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sports_ai_qualification_decisions (
 id TEXT PRIMARY KEY,
 prediction_id TEXT NOT NULL,
 opportunity_id TEXT,
 fixture_id TEXT NOT NULL,
 sport TEXT NOT NULL,
 competition TEXT NOT NULL DEFAULT '',
 market TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('QUALIFIED','MONITORING','REJECTED','AWAITING_DATA','EXPIRED','SUSPENDED')),
 reason_code TEXT NOT NULL,
 reason TEXT NOT NULL,
 evidence_json TEXT NOT NULL,
 products_json TEXT NOT NULL,
 rank_score REAL,
 policy_version INTEGER NOT NULL,
 decision_version INTEGER NOT NULL,
 fingerprint TEXT NOT NULL,
 starts_at TEXT,
 expires_at TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(prediction_id, market)
);

CREATE TABLE IF NOT EXISTS sports_ai_qualification_history (
 id TEXT PRIMARY KEY,
 decision_id TEXT NOT NULL,
 prediction_id TEXT NOT NULL,
 fixture_id TEXT NOT NULL,
 market TEXT NOT NULL,
 state TEXT NOT NULL,
 reason_code TEXT NOT NULL,
 reason TEXT NOT NULL,
 evidence_json TEXT NOT NULL,
 products_json TEXT NOT NULL,
 policy_version INTEGER NOT NULL,
 decision_version INTEGER NOT NULL,
 fingerprint TEXT NOT NULL,
 created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS sports_ai_qualification_state
 ON sports_ai_qualification_decisions(state, sport, market);
CREATE INDEX IF NOT EXISTS sports_ai_qualification_fixture
 ON sports_ai_qualification_decisions(fixture_id, market, state);
