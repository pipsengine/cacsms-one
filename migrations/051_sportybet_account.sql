-- Authenticated SportyBet account for the Single Betting engine.
-- Additive only. Real bookmaker activity lives in its own tables and never shares
-- rows with paper records (sports_ai_single_bets.mode stays 'PAPER').

-- One row: the connected account as last observed on SportyBet. No password is stored;
-- the session itself is an encrypted file outside the database.
CREATE TABLE IF NOT EXISTS sports_ai_sportybet_account (
 id TEXT PRIMARY KEY CHECK(id='primary'),
 status TEXT NOT NULL CHECK(status IN ('NOT_CONNECTED','AWAITING_LOGIN','CONNECTED','EXPIRED','ERROR')),
 detail TEXT NOT NULL DEFAULT '',
 session_saved_at TEXT,
 last_check_at TEXT,
 last_ok_at TEXT,
 balance REAL,
 balance_currency TEXT,
 balance_observed_at TEXT,
 updated_at TEXT NOT NULL
);
INSERT OR IGNORE INTO sports_ai_sportybet_account(id,status,detail,updated_at)
 VALUES('primary','NOT_CONNECTED','No SportyBet session has been connected.',CURRENT_TIMESTAMP);

-- An explicit, time-boxed grant for real-money submission. Never created from a balance.
CREATE TABLE IF NOT EXISTS sports_ai_single_authorizations (
 id TEXT PRIMARY KEY,
 granted_by TEXT NOT NULL,
 acknowledgement TEXT NOT NULL,
 stake REAL NOT NULL CHECK(stake=100),
 daily_count INTEGER NOT NULL CHECK(daily_count BETWEEN 1 AND 25),
 daily_stake REAL NOT NULL CHECK(daily_stake BETWEEN 100 AND 2500),
 granted_at TEXT NOT NULL,
 expires_at TEXT NOT NULL,
 revoked_at TEXT,
 revoked_by TEXT
);

-- Rehearsals (betslip prepared in the account, Place Bet not clicked) and submissions.
CREATE TABLE IF NOT EXISTS sports_ai_single_live_orders (
 id TEXT PRIMARY KEY,
 bet_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('REHEARSAL','SUBMISSION')),
 status TEXT NOT NULL CHECK(status IN ('PENDING','REHEARSED','SUBMITTING','PLACED','REJECTED','UNKNOWN','FAILED',
                                       'SETTLED_WON','SETTLED_LOST','SETTLED_VOID')),
 local_date TEXT NOT NULL,
 stake REAL NOT NULL CHECK(stake=100),
 expected_odds REAL,
 page_odds REAL,
 bookmaker_ref TEXT,
 authorization_id TEXT,
 checks_json TEXT NOT NULL DEFAULT '{}',
 evidence_path TEXT,
 error TEXT,
 payout REAL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 submitted_at TEXT,
 reconciled_at TEXT
);
-- A bet is submitted to SportyBet at most once; an unclear result is reconciled, never retried.
CREATE UNIQUE INDEX IF NOT EXISTS sports_ai_single_live_orders_one_submission
 ON sports_ai_single_live_orders(bet_id) WHERE kind='SUBMISSION';
CREATE INDEX IF NOT EXISTS sports_ai_single_live_orders_day ON sports_ai_single_live_orders(local_date, kind, status);

-- Raw Bet History page captures used for reconciliation.
CREATE TABLE IF NOT EXISTS sports_ai_sportybet_history (
 id TEXT PRIMARY KEY,
 captured_at TEXT NOT NULL,
 page TEXT NOT NULL,
 text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sports_ai_sportybet_history_captured ON sports_ai_sportybet_history(captured_at);
