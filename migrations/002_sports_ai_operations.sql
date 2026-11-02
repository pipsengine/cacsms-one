-- Additive SQLite-only sports operations extension. No existing trading tables modified.
CREATE TABLE IF NOT EXISTS sports_ai_results (id TEXT PRIMARY KEY,fixture_id TEXT NOT NULL UNIQUE REFERENCES sports_ai_fixtures(id),home_score INTEGER NOT NULL CHECK(home_score>=0),away_score INTEGER NOT NULL CHECK(away_score>=0),status TEXT NOT NULL DEFAULT 'final',source TEXT NOT NULL,settled_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS sports_ai_odds_history (id TEXT PRIMARY KEY,fixture_id TEXT NOT NULL REFERENCES sports_ai_fixtures(id),market TEXT NOT NULL,decimal_odds REAL NOT NULL CHECK(decimal_odds>1),source TEXT NOT NULL,observed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sports_ai_watchlist (id TEXT PRIMARY KEY,fixture_id TEXT NOT NULL UNIQUE REFERENCES sports_ai_fixtures(id),note TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS sports_ai_settings (key TEXT PRIMARY KEY,value_json TEXT NOT NULL,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS sports_ai_schedules (id TEXT PRIMARY KEY,name TEXT NOT NULL,interval_minutes INTEGER NOT NULL CHECK(interval_minutes>=5),enabled INTEGER NOT NULL DEFAULT 0,last_run_at TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS sports_ai_job_history (id TEXT PRIMARY KEY,schedule_id TEXT,started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,finished_at TEXT,status TEXT NOT NULL,details_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS sports_ai_alert_rules (id TEXT PRIMARY KEY,event_type TEXT NOT NULL,channel TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS sports_ai_data_sources (id TEXT PRIMARY KEY,name TEXT NOT NULL UNIQUE,sport TEXT NOT NULL,kind TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 0,notes TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS sports_ai_roles (id TEXT PRIMARY KEY,role_name TEXT NOT NULL UNIQUE,permissions_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS sports_ai_odds_history_fixture_time ON sports_ai_odds_history(fixture_id,observed_at);
CREATE INDEX IF NOT EXISTS sports_ai_results_fixture ON sports_ai_results(fixture_id);
CREATE INDEX IF NOT EXISTS sports_ai_job_history_started ON sports_ai_job_history(started_at);
