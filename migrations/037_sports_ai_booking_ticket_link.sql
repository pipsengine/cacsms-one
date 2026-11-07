ALTER TABLE sports_ai_booking_jobs ADD COLUMN ticket_id TEXT;

CREATE INDEX IF NOT EXISTS sports_ai_booking_jobs_ticket
 ON sports_ai_booking_jobs(ticket_id);

UPDATE sports_ai_booking_jobs
SET ticket_id = NULLIF(json_extract(selections_json, '$[0].ticket_id'), '')
WHERE ticket_id IS NULL;
