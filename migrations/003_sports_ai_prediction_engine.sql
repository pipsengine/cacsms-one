CREATE TABLE IF NOT EXISTS sports_ai_provider_registry (
 id TEXT PRIMARY KEY,
 provider_key TEXT NOT NULL UNIQUE,
 display_name TEXT NOT NULL,
 sport TEXT NOT NULL CHECK(sport IN ('football','basketball','both')),
 adapter TEXT NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
 terms_reviewed INTEGER NOT NULL DEFAULT 0 CHECK(terms_reviewed IN (0,1)),
 status TEXT NOT NULL DEFAULT 'untested',
 config_json TEXT NOT NULL DEFAULT '{}',
 last_success_at TEXT,
 last_error TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sports_ai_odds_snapshots (
 id TEXT PRIMARY KEY,
 fixture_id TEXT NOT NULL REFERENCES sports_ai_fixtures(id) ON DELETE CASCADE,
 provider_id TEXT NOT NULL REFERENCES sports_ai_provider_registry(id),
 bookmaker TEXT NOT NULL,
 market TEXT NOT NULL,
 outcome TEXT NOT NULL,
 line REAL,
 decimal_odds REAL NOT NULL CHECK(decimal_odds>1),
 source_url TEXT NOT NULL,
 observed_at TEXT NOT NULL,
 UNIQUE(fixture_id,provider_id,bookmaker,market,outcome,line,observed_at)
);

CREATE TABLE IF NOT EXISTS sports_ai_feature_snapshots (
 id TEXT PRIMARY KEY,
 fixture_id TEXT NOT NULL REFERENCES sports_ai_fixtures(id) ON DELETE CASCADE,
 sport TEXT NOT NULL CHECK(sport IN ('football','basketball')),
 cutoff_at TEXT NOT NULL,
 features_json TEXT NOT NULL,
 source_refs_json TEXT NOT NULL,
 generated_at TEXT NOT NULL,
 UNIQUE(fixture_id,cutoff_at)
);

CREATE TABLE IF NOT EXISTS sports_ai_training_runs (
 id TEXT PRIMARY KEY,
 sport TEXT NOT NULL CHECK(sport IN ('football','basketball')),
 model_name TEXT NOT NULL,
 version TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('insufficient_data','validated','failed')),
 training_window_json TEXT NOT NULL,
 parameters_json TEXT NOT NULL,
 metrics_json TEXT NOT NULL,
 sample_size INTEGER NOT NULL CHECK(sample_size>=0),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(sport,model_name,version)
);

CREATE TABLE IF NOT EXISTS sports_ai_model_registry (
 id TEXT PRIMARY KEY,
 training_run_id TEXT NOT NULL REFERENCES sports_ai_training_runs(id),
 sport TEXT NOT NULL CHECK(sport IN ('football','basketball')),
 model_name TEXT NOT NULL,
 version TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('unvalidated','validated','active','inactive','rejected')),
 active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0,1)),
 parameters_json TEXT NOT NULL,
 metrics_json TEXT NOT NULL,
 activated_at TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(sport,model_name,version)
);

CREATE UNIQUE INDEX IF NOT EXISTS sports_ai_model_one_active_per_sport
 ON sports_ai_model_registry(sport) WHERE active=1;

CREATE TABLE IF NOT EXISTS sports_ai_calibration_results (
 id TEXT PRIMARY KEY,
 model_id TEXT NOT NULL REFERENCES sports_ai_model_registry(id) ON DELETE CASCADE,
 market TEXT NOT NULL,
 method TEXT NOT NULL,
 sample_size INTEGER NOT NULL CHECK(sample_size>=0),
 metrics_json TEXT NOT NULL,
 held_out_start TEXT,
 held_out_end TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(model_id,market)
);

CREATE TABLE IF NOT EXISTS sports_ai_engine_predictions (
 id TEXT PRIMARY KEY,
 run_id TEXT NOT NULL REFERENCES sports_ai_analysis_runs(id) ON DELETE CASCADE,
 fixture_id TEXT NOT NULL REFERENCES sports_ai_fixtures(id) ON DELETE CASCADE,
 model_id TEXT NOT NULL REFERENCES sports_ai_model_registry(id),
 feature_id TEXT NOT NULL REFERENCES sports_ai_feature_snapshots(id),
 market TEXT NOT NULL,
 outcome TEXT NOT NULL,
 probability REAL NOT NULL CHECK(probability>=0 AND probability<=1),
 settlement_json TEXT NOT NULL,
 explanation_json TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(run_id,fixture_id,market,outcome)
);

CREATE TABLE IF NOT EXISTS sports_ai_value_opportunities (
 id TEXT PRIMARY KEY,
 prediction_id TEXT NOT NULL REFERENCES sports_ai_engine_predictions(id) ON DELETE CASCADE,
 odds_id TEXT REFERENCES sports_ai_odds_snapshots(id) ON DELETE SET NULL,
 implied_probability REAL,
 expected_edge REAL,
 status TEXT NOT NULL CHECK(status IN ('under_analysis','awaiting_odds','awaiting_calibration','qualified','rejected','expired','suspended')),
 reason TEXT NOT NULL,
 evaluated_at TEXT NOT NULL,
 UNIQUE(prediction_id,odds_id)
);

CREATE TABLE IF NOT EXISTS sports_ai_data_quality_issues (
 id TEXT PRIMARY KEY,
 fixture_id TEXT REFERENCES sports_ai_fixtures(id) ON DELETE CASCADE,
 provider_id TEXT REFERENCES sports_ai_provider_registry(id),
 severity TEXT NOT NULL CHECK(severity IN ('low','medium','high','critical')),
 issue_type TEXT NOT NULL,
 details_json TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS sports_ai_odds_fixture_time
 ON sports_ai_odds_snapshots(fixture_id,observed_at);
CREATE INDEX IF NOT EXISTS sports_ai_features_fixture_cutoff
 ON sports_ai_feature_snapshots(fixture_id,cutoff_at);
CREATE INDEX IF NOT EXISTS sports_ai_engine_predictions_fixture
 ON sports_ai_engine_predictions(fixture_id,created_at);
CREATE INDEX IF NOT EXISTS sports_ai_opportunities_status
 ON sports_ai_value_opportunities(status,evaluated_at);
CREATE INDEX IF NOT EXISTS sports_ai_quality_open_fixture
 ON sports_ai_data_quality_issues(fixture_id,severity,resolved_at);

INSERT OR IGNORE INTO sports_ai_provider_registry
 (id,provider_key,display_name,sport,adapter,status,config_json)
 VALUES
 ('provider-openligadb','openligadb','OpenLigaDB','football','openligadb','untested','{"league":"bl1","season":2026}'),
 ('provider-openfootball','openfootball','OpenFootball public dataset','football','openfootball','untested','{"dataset_url":""}'),
 ('provider-basketball-csv','basketball-csv','Licensed basketball results CSV','basketball','csv_import','untested','{}'),
 ('provider-odds-api','the-odds-api','The Odds API','both','the_odds_api','untested','{"sport_keys":["soccer_epl","basketball_nba"],"regions":"uk","markets":"h2h"}'),
 ('provider-manual-odds','manual-odds','Permitted manual odds import','both','manual_odds','untested','{}');
