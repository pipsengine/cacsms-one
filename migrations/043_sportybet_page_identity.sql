-- Phase 1: booking reliability.
-- SportyBet's own event id, so the booking operator can find a match by the
-- bookmaker's identity instead of by names from other providers.
ALTER TABLE sports_ai_sportybet_observations ADD COLUMN event_id TEXT;

-- Booking looks up the latest SportyBet observation per fixture.
CREATE INDEX IF NOT EXISTS sports_ai_sportybet_observations_fixture
 ON sports_ai_sportybet_observations(fixture_id, observed_at);

-- Names that differ between the fixture store and SportyBet's page. Rows are
-- learned from matched observations or entered by an operator, never guessed.
CREATE TABLE IF NOT EXISTS sports_ai_bookmaker_team_names (
 id TEXT PRIMARY KEY,
 sport TEXT NOT NULL,
 canonical_name TEXT NOT NULL,
 bookmaker TEXT NOT NULL,
 bookmaker_name TEXT NOT NULL,
 source TEXT NOT NULL CHECK(source IN ('observation','operator')),
 evidence_json TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL,
 UNIQUE(sport, canonical_name, bookmaker)
);
