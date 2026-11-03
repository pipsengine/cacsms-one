CREATE TABLE IF NOT EXISTS sports_ai_sportybet_sessions (
 id TEXT PRIMARY KEY,
 bookmaker TEXT NOT NULL DEFAULT 'SportyBet',
 mode TEXT NOT NULL CHECK(mode IN ('supervised')),
 state TEXT NOT NULL,
 source_url TEXT,
 last_operation TEXT,
 observation_count INTEGER NOT NULL DEFAULT 0,
 paused INTEGER NOT NULL DEFAULT 0 CHECK(paused IN (0,1)),
 error TEXT,
 started_at TEXT,
 heartbeat_at TEXT,
 ended_at TEXT,
 actor TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sports_ai_sportybet_events (
 id TEXT PRIMARY KEY,
 session_id TEXT NOT NULL,
 state TEXT NOT NULL,
 operation TEXT NOT NULL,
 source_url TEXT,
 detail TEXT NOT NULL,
 screenshot_path TEXT,
 created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS sports_ai_sportybet_events_session
 ON sports_ai_sportybet_events(session_id, created_at);
