CREATE TABLE IF NOT EXISTS sports_ai_accumulator_configs (
 id TEXT PRIMARY KEY,
 name TEXT NOT NULL,
 targets_json TEXT NOT NULL,
 tolerance REAL NOT NULL CHECK(tolerance>0 AND tolerance<1),
 min_legs INTEGER NOT NULL CHECK(min_legs>=2 AND min_legs<=12),
 max_legs INTEGER NOT NULL CHECK(max_legs>=min_legs AND max_legs<=12),
 max_per_competition INTEGER NOT NULL CHECK(max_per_competition BETWEEN 1 AND 12),
 max_per_team INTEGER NOT NULL CHECK(max_per_team BETWEEN 1 AND 6),
 min_probability REAL NOT NULL CHECK(min_probability>=0 AND min_probability<1),
 min_edge REAL NOT NULL,
 min_model_reliability REAL NOT NULL CHECK(min_model_reliability>=0 AND min_model_reliability<=1),
 min_completeness REAL NOT NULL CHECK(min_completeness>=0 AND min_completeness<=1),
 odds_max_age_minutes INTEGER NOT NULL CHECK(odds_max_age_minutes BETWEEN 1 AND 1440),
 max_tickets_per_tier INTEGER NOT NULL CHECK(max_tickets_per_tier BETWEEN 1 AND 20),
 beam_width INTEGER NOT NULL CHECK(beam_width BETWEEN 4 AND 200),
 search_limit INTEGER NOT NULL CHECK(search_limit BETWEEN 100 AND 200000),
 timeout_seconds INTEGER NOT NULL CHECK(timeout_seconds BETWEEN 1 AND 120),
 allow_same_fixture INTEGER NOT NULL DEFAULT 0 CHECK(allow_same_fixture IN (0,1)),
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sports_ai_accumulator_runs (
 id TEXT PRIMARY KEY,
 config_id TEXT REFERENCES sports_ai_accumulator_configs(id),
 sport_mode TEXT NOT NULL CHECK(sport_mode IN ('football','basketball','mixed')),
 status TEXT NOT NULL CHECK(status IN ('running','complete','no_qualifying_ticket','failed','skipped')),
 trigger TEXT NOT NULL,
 idempotency_key TEXT NOT NULL UNIQUE,
 parameters_json TEXT NOT NULL,
 summary_json TEXT NOT NULL DEFAULT '{}',
 actor TEXT NOT NULL,
 started_at TEXT NOT NULL,
 finished_at TEXT,
 error TEXT
);

CREATE TABLE IF NOT EXISTS sports_ai_accumulator_tickets (
 id TEXT PRIMARY KEY,
 run_id TEXT REFERENCES sports_ai_accumulator_runs(id),
 family_id TEXT NOT NULL,
 version INTEGER NOT NULL CHECK(version>=1),
 signature TEXT NOT NULL,
 tier INTEGER NOT NULL,
 sport_mode TEXT NOT NULL CHECK(sport_mode IN ('football','basketball','mixed')),
 status TEXT NOT NULL CHECK(status IN ('qualified','unverified_probability','awaiting_odds','expired','review_required','settled','void')),
 combined_odds REAL,
 joint_probability REAL,
 estimated_edge REAL,
 leg_count INTEGER NOT NULL CHECK(leg_count>=0),
 probability_basis TEXT NOT NULL CHECK(probability_basis IN ('independent','unverified')),
 uncertainty REAL,
 explanation TEXT NOT NULL,
 parameters_json TEXT NOT NULL,
 score REAL,
 finalized INTEGER NOT NULL DEFAULT 0 CHECK(finalized IN (0,1)),
 created_at TEXT NOT NULL,
 UNIQUE(family_id,version)
);

CREATE TABLE IF NOT EXISTS sports_ai_accumulator_legs (
 id TEXT PRIMARY KEY,
 ticket_id TEXT NOT NULL REFERENCES sports_ai_accumulator_tickets(id),
 opportunity_id TEXT NOT NULL,
 fixture_id TEXT NOT NULL,
 sport TEXT NOT NULL CHECK(sport IN ('football','basketball')),
 competition TEXT NOT NULL,
 home TEXT NOT NULL,
 away TEXT NOT NULL,
 starts_at TEXT NOT NULL,
 market TEXT NOT NULL,
 selection TEXT NOT NULL,
 decimal_odds REAL NOT NULL CHECK(decimal_odds>1),
 probability REAL,
 edge REAL,
 bookmaker TEXT NOT NULL,
 odds_observed_at TEXT NOT NULL,
 quote_market TEXT NOT NULL,
 quote_outcome TEXT NOT NULL,
 quote_line REAL,
 model_id TEXT NOT NULL,
 model_version TEXT NOT NULL,
 data_completeness REAL,
 reliability REAL,
 settlement_kind TEXT NOT NULL CHECK(settlement_kind IN ('binary','partial','unknown')),
 result TEXT NOT NULL DEFAULT 'pending' CHECK(result IN ('pending','won','lost','void','push','half_win','half_loss')),
 qualification_json TEXT NOT NULL,
 UNIQUE(ticket_id,fixture_id)
);

CREATE TABLE IF NOT EXISTS sports_ai_ticket_versions (
 id TEXT PRIMARY KEY,
 family_id TEXT NOT NULL,
 ticket_id TEXT NOT NULL REFERENCES sports_ai_accumulator_tickets(id),
 version INTEGER NOT NULL,
 change_type TEXT NOT NULL,
 reason TEXT NOT NULL,
 created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sports_ai_ticket_qualification_logs (
 id TEXT PRIMARY KEY,
 run_id TEXT REFERENCES sports_ai_accumulator_runs(id),
 ticket_id TEXT REFERENCES sports_ai_accumulator_tickets(id),
 opportunity_id TEXT,
 decision TEXT NOT NULL CHECK(decision IN ('accepted','rejected')),
 reason TEXT NOT NULL,
 created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sports_ai_ticket_correlation_checks (
 id TEXT PRIMARY KEY,
 ticket_id TEXT NOT NULL REFERENCES sports_ai_accumulator_tickets(id),
 check_type TEXT NOT NULL,
 result TEXT NOT NULL CHECK(result IN ('pass','fail','unverified')),
 details_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sports_ai_ticket_monitoring_events (
 id TEXT PRIMARY KEY,
 ticket_id TEXT REFERENCES sports_ai_accumulator_tickets(id),
 run_id TEXT REFERENCES sports_ai_accumulator_runs(id),
 event_type TEXT NOT NULL,
 details_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sports_ai_ticket_settlements (
 id TEXT PRIMARY KEY,
 ticket_id TEXT NOT NULL UNIQUE REFERENCES sports_ai_accumulator_tickets(id),
 outcome TEXT NOT NULL CHECK(outcome IN ('won','lost','void','partial','pending')),
 payout_multiple REAL,
 stake_assumption REAL NOT NULL,
 hypothetical_profit REAL,
 settled_at TEXT NOT NULL,
 details_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sports_ai_accumulator_backtests (
 id TEXT PRIMARY KEY,
 created_at TEXT NOT NULL,
 window_json TEXT NOT NULL,
 parameters_json TEXT NOT NULL,
 metrics_json TEXT NOT NULL,
 sample_size INTEGER NOT NULL CHECK(sample_size>=0)
);

CREATE TABLE IF NOT EXISTS sports_ai_accumulator_performance (
 id TEXT PRIMARY KEY,
 tier INTEGER NOT NULL,
 sport_mode TEXT NOT NULL,
 settled_count INTEGER NOT NULL,
 wins INTEGER NOT NULL,
 losses INTEGER NOT NULL,
 voids INTEGER NOT NULL,
 partials INTEGER NOT NULL,
 hypothetical_profit REAL NOT NULL,
 stake_assumption REAL NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(tier,sport_mode)
);

CREATE INDEX IF NOT EXISTS sports_ai_accumulator_tickets_status
 ON sports_ai_accumulator_tickets(status,tier,sport_mode);
CREATE INDEX IF NOT EXISTS sports_ai_accumulator_tickets_signature
 ON sports_ai_accumulator_tickets(signature,status);
CREATE INDEX IF NOT EXISTS sports_ai_accumulator_legs_fixture
 ON sports_ai_accumulator_legs(fixture_id,ticket_id);
CREATE INDEX IF NOT EXISTS sports_ai_ticket_qualification_run
 ON sports_ai_ticket_qualification_logs(run_id,decision);
CREATE INDEX IF NOT EXISTS sports_ai_ticket_monitor_ticket
 ON sports_ai_ticket_monitoring_events(ticket_id,event_type,created_at);

INSERT OR IGNORE INTO sports_ai_accumulator_configs (
 id,name,targets_json,tolerance,min_legs,max_legs,max_per_competition,max_per_team,
 min_probability,min_edge,min_model_reliability,min_completeness,odds_max_age_minutes,
 max_tickets_per_tier,beam_width,search_limit,timeout_seconds,allow_same_fixture
) VALUES (
 'accumulator-default','Default accumulator policy',
 '[2,5,10,25,100,1000,5000,25000]',0.15,2,8,3,1,
 0.02,0.05,0,0.5,15,
 3,24,8000,8,0
);

CREATE TABLE IF NOT EXISTS sports_ai_booking_codes_v2 (
 id TEXT PRIMARY KEY,
 ticket_id TEXT UNIQUE REFERENCES sports_ai_tickets(id),
 accumulator_ticket_id TEXT REFERENCES sports_ai_accumulator_tickets(id),
 code TEXT NOT NULL,
 verification_method TEXT NOT NULL,
 evidence TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'unverified',
 bookmaker TEXT NOT NULL DEFAULT 'SportyBet',
 expires_at TEXT,
 verified_at TEXT,
 integration_status TEXT NOT NULL DEFAULT 'manual_only',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CHECK (ticket_id IS NOT NULL OR accumulator_ticket_id IS NOT NULL)
);

INSERT INTO sports_ai_booking_codes_v2 (
 id,ticket_id,code,verification_method,evidence,status,created_at
)
SELECT id,ticket_id,code,verification_method,evidence,status,created_at
FROM sports_ai_booking_codes;

DROP TABLE sports_ai_booking_codes;

ALTER TABLE sports_ai_booking_codes_v2 RENAME TO sports_ai_booking_codes;

CREATE INDEX IF NOT EXISTS sports_ai_booking_codes_accumulator
 ON sports_ai_booking_codes(accumulator_ticket_id,created_at);
