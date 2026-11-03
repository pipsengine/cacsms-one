ALTER TABLE sports_ai_model_registry ADD COLUMN market TEXT NOT NULL DEFAULT 'match_outcome';
ALTER TABLE sports_ai_model_registry ADD COLUMN feature_schema_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE sports_ai_model_registry ADD COLUMN artifact_ref TEXT NOT NULL DEFAULT '';
ALTER TABLE sports_ai_model_registry ADD COLUMN calibration_status TEXT NOT NULL DEFAULT 'uncalibrated';
ALTER TABLE sports_ai_model_registry ADD COLUMN replaced_by TEXT;

ALTER TABLE sports_ai_feature_snapshots ADD COLUMN completeness REAL;
ALTER TABLE sports_ai_feature_snapshots ADD COLUMN input_fingerprint TEXT NOT NULL DEFAULT '';

ALTER TABLE sports_ai_value_opportunities ADD COLUMN rank_score REAL;
ALTER TABLE sports_ai_value_opportunities ADD COLUMN uncertainty REAL;
ALTER TABLE sports_ai_value_opportunities ADD COLUMN data_completeness REAL;

CREATE TABLE IF NOT EXISTS sports_ai_training_datasets (
 id TEXT PRIMARY KEY,
 training_run_id TEXT NOT NULL REFERENCES sports_ai_training_runs(id) ON DELETE CASCADE,
 fixture_id TEXT NOT NULL,
 kickoff_at TEXT NOT NULL,
 features_json TEXT NOT NULL,
 label_json TEXT NOT NULL,
 split TEXT NOT NULL CHECK(split IN ('train','calibration','holdout')),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sports_ai_validation_results (
 id TEXT PRIMARY KEY,
 training_run_id TEXT NOT NULL REFERENCES sports_ai_training_runs(id) ON DELETE CASCADE,
 model_family TEXT NOT NULL,
 market TEXT NOT NULL,
 fold_index INTEGER NOT NULL,
 metrics_json TEXT NOT NULL,
 sample_size INTEGER NOT NULL CHECK(sample_size>=0),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(training_run_id,model_family,market,fold_index)
);

CREATE TABLE IF NOT EXISTS sports_ai_model_versions (
 id TEXT PRIMARY KEY,
 model_id TEXT NOT NULL REFERENCES sports_ai_model_registry(id) ON DELETE CASCADE,
 version TEXT NOT NULL,
 status TEXT NOT NULL,
 parameters_json TEXT NOT NULL,
 metrics_json TEXT NOT NULL,
 artifact_ref TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sports_ai_prediction_explanations (
 id TEXT PRIMARY KEY,
 prediction_id TEXT NOT NULL UNIQUE REFERENCES sports_ai_engine_predictions(id) ON DELETE CASCADE,
 summary TEXT NOT NULL,
 factors_json TEXT NOT NULL,
 missing_json TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sports_ai_market_mappings (
 id TEXT PRIMARY KEY,
 sport TEXT NOT NULL CHECK(sport IN ('football','basketball')),
 provider_market TEXT NOT NULL,
 provider_outcome TEXT NOT NULL,
 line REAL,
 canonical_market TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(sport,provider_market,provider_outcome,line)
);

CREATE TABLE IF NOT EXISTS sports_ai_prediction_jobs (
 id TEXT PRIMARY KEY,
 idempotency_key TEXT NOT NULL UNIQUE,
 trigger TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('queued','running','complete','failed','timed_out','skipped')),
 attempt INTEGER NOT NULL DEFAULT 1 CHECK(attempt>=1),
 started_at TEXT,
 finished_at TEXT,
 timeout_seconds INTEGER NOT NULL DEFAULT 600 CHECK(timeout_seconds BETWEEN 30 AND 7200),
 details_json TEXT NOT NULL DEFAULT '{}',
 error TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS sports_ai_training_datasets_run
 ON sports_ai_training_datasets(training_run_id,split);
CREATE INDEX IF NOT EXISTS sports_ai_validation_run
 ON sports_ai_validation_results(training_run_id,model_family);
CREATE INDEX IF NOT EXISTS sports_ai_model_versions_model
 ON sports_ai_model_versions(model_id,created_at);
CREATE INDEX IF NOT EXISTS sports_ai_prediction_jobs_status
 ON sports_ai_prediction_jobs(status,created_at);
CREATE INDEX IF NOT EXISTS sports_ai_market_mappings_lookup
 ON sports_ai_market_mappings(sport,provider_market,provider_outcome);
