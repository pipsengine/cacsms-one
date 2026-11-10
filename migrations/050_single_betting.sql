-- Single Betting AI: autonomous PAPER singles fed by the shared qualification stage.
-- Additive only: two new tables and indexes on them. No existing table is touched.
-- Real-money submission is not implemented; every row is a paper record (mode='PAPER').

CREATE TABLE IF NOT EXISTS sports_ai_single_bets (
 id TEXT PRIMARY KEY,
 decision_id TEXT,
 prediction_id TEXT,
 fixture_id TEXT NOT NULL,
 sport TEXT NOT NULL,
 competition TEXT NOT NULL DEFAULT '',
 home TEXT NOT NULL DEFAULT '',
 away TEXT NOT NULL DEFAULT '',
 market TEXT NOT NULL,
 selection TEXT NOT NULL,
 decimal_odds REAL NOT NULL CHECK(decimal_odds>1),
 probability REAL NOT NULL CHECK(probability>0 AND probability<1),
 expected_value REAL NOT NULL,
 stake REAL NOT NULL CHECK(stake>0),
 mode TEXT NOT NULL DEFAULT 'PAPER' CHECK(mode='PAPER'),
 status TEXT NOT NULL CHECK(status IN ('QUEUED','PLACED','WON','LOST','VOID','CANCELLED')),
 reason TEXT NOT NULL DEFAULT '',
 local_date TEXT NOT NULL,
 starts_at TEXT NOT NULL,
 odds_observed_at TEXT,
 payout REAL,
 profit REAL,
 created_at TEXT NOT NULL,
 placed_at TEXT,
 settled_at TEXT
);
-- One live single per fixture: the correlation rule. A fixture is never backed twice.
-- A cancelled queue entry (stale price, lost qualification) does not block a later retry.
CREATE UNIQUE INDEX IF NOT EXISTS sports_ai_single_bets_fixture
 ON sports_ai_single_bets(fixture_id) WHERE status!='CANCELLED';
CREATE INDEX IF NOT EXISTS sports_ai_single_bets_day ON sports_ai_single_bets(local_date, status);
CREATE INDEX IF NOT EXISTS sports_ai_single_bets_status ON sports_ai_single_bets(status, created_at);

CREATE TABLE IF NOT EXISTS sports_ai_single_events (
 id TEXT PRIMARY KEY,
 bet_id TEXT,
 action TEXT NOT NULL,
 detail TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sports_ai_single_events_created ON sports_ai_single_events(created_at);
