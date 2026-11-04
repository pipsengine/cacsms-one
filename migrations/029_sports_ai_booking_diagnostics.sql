ALTER TABLE sports_ai_booking_jobs ADD COLUMN reason_code TEXT;

ALTER TABLE sports_ai_engine_predictions ADD COLUMN settled_outcome TEXT;
ALTER TABLE sports_ai_engine_predictions ADD COLUMN result_settled_at TEXT;

CREATE TABLE IF NOT EXISTS sports_ai_booking_diagnostics (
 id TEXT PRIMARY KEY,
 ticket_id TEXT,
 reason_code TEXT NOT NULL,
 detail TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sports_ai_booking_diagnostics_ticket
 ON sports_ai_booking_diagnostics(ticket_id, created_at);

CREATE TABLE IF NOT EXISTS sports_ai_prediction_reviews (
 id TEXT PRIMARY KEY,
 prediction_id TEXT NOT NULL,
 fixture_id TEXT NOT NULL,
 market TEXT NOT NULL,
 reason_code TEXT NOT NULL,
 detail TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(prediction_id, reason_code)
);
