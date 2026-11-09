-- Phase 4: source permissions.
-- Terms decisions stay in sports_ai_source_permissions (migration 004).
-- This adds the cached robots.txt per host and an audit row per decision.
CREATE TABLE IF NOT EXISTS sports_ai_source_robots (
 host TEXT PRIMARY KEY,
 robots_status TEXT NOT NULL CHECK(robots_status IN ('present','absent','unreachable')),
 robots_txt TEXT,
 checked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sports_ai_source_permission_checks (
 id TEXT PRIMARY KEY,
 host TEXT NOT NULL,
 url TEXT NOT NULL,
 purpose TEXT NOT NULL,
 allowed INTEGER NOT NULL CHECK(allowed IN (0,1)),
 reason TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sports_ai_source_permission_checks_host
 ON sports_ai_source_permission_checks(host, created_at);
