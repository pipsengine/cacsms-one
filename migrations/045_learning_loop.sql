-- Phase 3: model learning loop.
-- SportyBet prices that arrived before their prediction. Partial index, so the
-- worker can re-assess them without scanning millions of observations.
CREATE INDEX IF NOT EXISTS sports_ai_sportybet_observations_awaiting_model
 ON sports_ai_sportybet_observations(fixture_id, observed_at) WHERE value_status='awaiting_model';

CREATE INDEX IF NOT EXISTS sports_ai_model_drift_events_sport
 ON sports_ai_model_drift_events(sport, created_at);
