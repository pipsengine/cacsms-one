CREATE TABLE sports_ai_training_runs_next (
 id TEXT PRIMARY KEY,
 sport TEXT NOT NULL,
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

INSERT INTO sports_ai_training_runs_next
 (id,sport,model_name,version,status,training_window_json,parameters_json,metrics_json,sample_size,created_at)
SELECT id,sport,model_name,version,status,training_window_json,parameters_json,metrics_json,sample_size,created_at
FROM sports_ai_training_runs;

DROP TABLE sports_ai_training_runs;

ALTER TABLE sports_ai_training_runs_next RENAME TO sports_ai_training_runs;

CREATE TABLE sports_ai_model_registry_next (
 id TEXT PRIMARY KEY,
 training_run_id TEXT NOT NULL REFERENCES sports_ai_training_runs(id),
 sport TEXT NOT NULL,
 model_name TEXT NOT NULL,
 version TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('unvalidated','validated','active','inactive','rejected')),
 active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0,1)),
 parameters_json TEXT NOT NULL,
 metrics_json TEXT NOT NULL,
 activated_at TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 market TEXT NOT NULL DEFAULT 'match_outcome',
 feature_schema_json TEXT NOT NULL DEFAULT '[]',
 artifact_ref TEXT NOT NULL DEFAULT '',
 calibration_status TEXT NOT NULL DEFAULT 'uncalibrated',
 replaced_by TEXT,
 lifecycle TEXT NOT NULL DEFAULT 'research',
 UNIQUE(sport,model_name,version)
);

INSERT INTO sports_ai_model_registry_next
 (id,training_run_id,sport,model_name,version,status,active,parameters_json,metrics_json,activated_at,created_at,
  market,feature_schema_json,artifact_ref,calibration_status,replaced_by,lifecycle)
SELECT id,training_run_id,sport,model_name,version,status,active,parameters_json,metrics_json,activated_at,created_at,
       market,feature_schema_json,artifact_ref,calibration_status,replaced_by,lifecycle
FROM sports_ai_model_registry;

DROP TABLE sports_ai_model_registry;

ALTER TABLE sports_ai_model_registry_next RENAME TO sports_ai_model_registry;

CREATE UNIQUE INDEX IF NOT EXISTS sports_ai_model_one_active_per_sport
 ON sports_ai_model_registry(sport) WHERE active=1;

CREATE TABLE sports_ai_feature_snapshots_next (
 id TEXT PRIMARY KEY,
 fixture_id TEXT NOT NULL REFERENCES sports_ai_fixtures(id) ON DELETE CASCADE,
 sport TEXT NOT NULL,
 cutoff_at TEXT NOT NULL,
 features_json TEXT NOT NULL,
 source_refs_json TEXT NOT NULL,
 generated_at TEXT NOT NULL,
 completeness REAL,
 input_fingerprint TEXT NOT NULL DEFAULT '',
 UNIQUE(fixture_id,cutoff_at)
);

INSERT INTO sports_ai_feature_snapshots_next
 (id,fixture_id,sport,cutoff_at,features_json,source_refs_json,generated_at,completeness,input_fingerprint)
SELECT id,fixture_id,sport,cutoff_at,features_json,source_refs_json,generated_at,completeness,input_fingerprint
FROM sports_ai_feature_snapshots;

DROP TABLE sports_ai_feature_snapshots;

ALTER TABLE sports_ai_feature_snapshots_next RENAME TO sports_ai_feature_snapshots;

CREATE INDEX IF NOT EXISTS sports_ai_feature_snapshots_fixture
 ON sports_ai_feature_snapshots(fixture_id,cutoff_at);
