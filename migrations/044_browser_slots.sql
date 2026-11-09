-- Phase 2: browser operator stability.
-- One row per Chromium slot shared by the API, the worker and its background
-- threads. A crashed holder's lease expires instead of blocking forever.
CREATE TABLE IF NOT EXISTS sports_ai_browser_slots (
 slot INTEGER PRIMARY KEY,
 owner TEXT NOT NULL DEFAULT '',
 purpose TEXT NOT NULL DEFAULT '',
 acquired_at TEXT,
 expires_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00'
);
INSERT OR IGNORE INTO sports_ai_browser_slots(slot) VALUES (1),(2);

-- Stage failures keep their error and are matched to a recovery case.
CREATE INDEX IF NOT EXISTS sports_ai_recovery_cases_code
 ON sports_ai_recovery_cases(error_code,status);
