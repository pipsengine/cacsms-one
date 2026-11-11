-- Single Betting AI: minimum odds 1.20 and combined selections.
-- Additive only. ADD COLUMN is a metadata change in SQLite; no existing row is rewritten.
-- A combined selection is one ₦100 paper bet over 2–3 high-probability legs on different
-- fixtures, used when each leg alone is priced below the 1.20 single minimum.
-- The parent row keeps the first leg's fixture_id, so the existing one-bet-per-fixture
-- unique index still guards it; the other legs are guarded in code via this table.

ALTER TABLE sports_ai_single_bets ADD COLUMN kind TEXT NOT NULL DEFAULT 'SINGLE';

CREATE TABLE IF NOT EXISTS sports_ai_single_bet_legs (
 bet_id TEXT NOT NULL,
 leg_no INTEGER NOT NULL,
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
 starts_at TEXT NOT NULL,
 odds_observed_at TEXT,
 status TEXT NOT NULL DEFAULT 'OPEN' CHECK(status IN ('OPEN','WON','LOST','VOID')),
 PRIMARY KEY (bet_id, leg_no)
);
CREATE INDEX IF NOT EXISTS sports_ai_single_bet_legs_fixture ON sports_ai_single_bet_legs(fixture_id);
