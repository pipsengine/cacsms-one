-- On a populated PostgreSQL database, create this index concurrently before applying
-- the migration to avoid blocking price writes during the index build.
CREATE INDEX IF NOT EXISTS sports_ai_odds_bookmaker_observed
 ON sports_ai_odds_snapshots(bookmaker, observed_at);
