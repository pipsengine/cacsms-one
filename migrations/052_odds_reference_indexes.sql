-- Settled-snapshot cleanup deletes rows from sports_ai_odds_snapshots inside one write
-- transaction. Two references to those rows had no index:
--   sports_ai_value_opportunities.odds_id     (FOREIGN KEY ... ON DELETE SET NULL)
--   sports_ai_sportybet_observations.odds_id  (cleared by the cleanup before deleting)
-- Without them every deleted snapshot scanned ~370k opportunities and every 400-id chunk
-- scanned ~4.3M observations, holding SQLite's write lock for minutes. The worker
-- heartbeat, the booking lane and API writes then failed with "database is locked".
CREATE INDEX IF NOT EXISTS sports_ai_value_opportunities_odds
 ON sports_ai_value_opportunities(odds_id);
CREATE INDEX IF NOT EXISTS sports_ai_sportybet_observations_odds
 ON sports_ai_sportybet_observations(odds_id);
