CREATE TABLE IF NOT EXISTS sports_ai_rollover_campaigns (
 id TEXT PRIMARY KEY,
 name TEXT NOT NULL,
 mode TEXT NOT NULL CHECK(mode IN ('FULL_ROLLOVER_SIMULATION','CAPITAL_CONTROLLED_SIMULATION')),
 status TEXT NOT NULL CHECK(status IN ('active','stopped','completed')),
 start_date TEXT NOT NULL,
 length_days INTEGER NOT NULL CHECK(length_days BETWEEN 1 AND 366),
 opening_balance REAL NOT NULL CHECK(opening_balance>0),
 currency TEXT NOT NULL DEFAULT 'NGN',
 odds_target REAL NOT NULL CHECK(odds_target>1),
 odds_low REAL NOT NULL CHECK(odds_low>1),
 odds_high REAL NOT NULL CHECK(odds_high>=odds_low),
 stake_mode TEXT NOT NULL CHECK(stake_mode IN ('fixed','fractional')),
 fixed_stake REAL NOT NULL CHECK(fixed_stake>0),
 stake_fraction REAL NOT NULL CHECK(stake_fraction>0 AND stake_fraction<=1),
 max_stake_fraction REAL NOT NULL CHECK(max_stake_fraction>0 AND max_stake_fraction<=1),
 protected_profit_fraction REAL NOT NULL CHECK(protected_profit_fraction>=0 AND protected_profit_fraction<=1),
 profit_lock_multiple REAL NOT NULL CHECK(profit_lock_multiple>=1),
 stop_loss_fraction REAL NOT NULL CHECK(stop_loss_fraction>0 AND stop_loss_fraction<=1),
 bankroll REAL NOT NULL,
 protected_capital REAL NOT NULL DEFAULT 0,
 peak_equity REAL NOT NULL,
 consecutive_wins INTEGER NOT NULL DEFAULT 0,
 max_consecutive_wins INTEGER NOT NULL DEFAULT 0,
 max_drawdown REAL NOT NULL DEFAULT 0,
 wins INTEGER NOT NULL DEFAULT 0,
 losses INTEGER NOT NULL DEFAULT 0,
 skipped INTEGER NOT NULL DEFAULT 0,
 places_wager INTEGER NOT NULL DEFAULT 0 CHECK(places_wager=0),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sports_ai_rollover_days (
 id TEXT PRIMARY KEY,
 campaign_id TEXT NOT NULL REFERENCES sports_ai_rollover_campaigns(id),
 day_index INTEGER NOT NULL CHECK(day_index>=1),
 campaign_date TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('SELECTED','SKIPPED_DAY','WON','LOST','VOID')),
 fixture_id TEXT,
 sport TEXT,
 market TEXT,
 selection TEXT,
 home TEXT,
 away TEXT,
 decimal_odds REAL,
 probability REAL,
 edge REAL,
 uncertainty REAL,
 completeness REAL,
 stake REAL,
 bankroll_before REAL,
 bankroll_after REAL,
 protected_before REAL,
 protected_after REAL,
 reason TEXT NOT NULL,
 score_json TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL,
 UNIQUE(campaign_id, campaign_date)
);

CREATE INDEX IF NOT EXISTS sports_ai_rollover_days_campaign
 ON sports_ai_rollover_days(campaign_id, campaign_date);

INSERT OR IGNORE INTO sports_ai_schedules
 (id,name,interval_minutes,enabled,job_kind,cron_expression,timezone)
VALUES
 ('schedule-rollover-selection','Daily rollover simulation',30,1,'rollover',NULL,'WAT');
