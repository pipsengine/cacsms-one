CREATE TABLE IF NOT EXISTS sports_ai_autonomous_cycles (
 id TEXT PRIMARY KEY,
 stage TEXT NOT NULL,
 status TEXT NOT NULL,
 detail_json TEXT NOT NULL,
 places_wager INTEGER NOT NULL DEFAULT 0 CHECK(places_wager=0),
 created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS sports_ai_autonomous_cycles_stage
 ON sports_ai_autonomous_cycles(stage, created_at);
