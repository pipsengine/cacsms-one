ALTER TABLE sports_ai_engine_predictions ADD COLUMN cohort TEXT;

CREATE INDEX IF NOT EXISTS sports_ai_engine_predictions_created
 ON sports_ai_engine_predictions(created_at);

CREATE INDEX IF NOT EXISTS sports_ai_engine_predictions_cohort
 ON sports_ai_engine_predictions(cohort, created_at);
