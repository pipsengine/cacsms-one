CREATE TABLE sports_ai_high_odds_categories (
 id TEXT PRIMARY KEY,
 label TEXT NOT NULL,
 min_odds REAL NOT NULL CHECK(min_odds>=2),
 max_odds REAL NOT NULL CHECK(max_odds<=1000 AND max_odds>min_odds),
 priority INTEGER NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
 max_active INTEGER NOT NULL DEFAULT 3 CHECK(max_active BETWEEN 1 AND 10)
);
INSERT INTO sports_ai_high_odds_categories(id,label,min_odds,max_odds,priority) VALUES
 ('2-4.99','2.00–4.99',2,4.99,10),
 ('5-9.99','5.00–9.99',5,9.99,20),
 ('10-24.99','10.00–24.99',10,24.99,30),
 ('25-49.99','25.00–49.99',25,49.99,40),
 ('50-99.99','50.00–99.99',50,99.99,50),
 ('100-249.99','100.00–249.99',100,249.99,60),
 ('250-499.99','250.00–499.99',250,499.99,70),
 ('500-1000','500.00–1,000.00',500,1000,80);

CREATE TABLE sports_ai_high_odds_events (
 id TEXT PRIMARY KEY,
 event_key TEXT NOT NULL UNIQUE,
 event_type TEXT NOT NULL,
 payload_json TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','processing','completed','failed')),
 attempts INTEGER NOT NULL DEFAULT 0,
 available_at TEXT NOT NULL,
 locked_at TEXT,
 error TEXT,
 created_at TEXT NOT NULL,
 completed_at TEXT
);
CREATE INDEX high_odds_event_queue ON sports_ai_high_odds_events(status,available_at);

CREATE TABLE sports_ai_high_odds_tickets (
 ticket_no INTEGER PRIMARY KEY AUTOINCREMENT,
 ticket_id TEXT NOT NULL UNIQUE REFERENCES sports_ai_accumulator_tickets(id),
 category_id TEXT NOT NULL REFERENCES sports_ai_high_odds_categories(id),
 event_id TEXT NOT NULL REFERENCES sports_ai_high_odds_events(id),
 signature TEXT NOT NULL UNIQUE,
 snapshot_json TEXT NOT NULL,
 snapshot_hash TEXT NOT NULL,
 confidence REAL NOT NULL,
 lower_joint_probability REAL NOT NULL,
 risk REAL NOT NULL,
 registration_state TEXT NOT NULL DEFAULT 'pending',
 booking_job_id TEXT REFERENCES sports_ai_booking_jobs(id),
 attempts INTEGER NOT NULL DEFAULT 0,
 next_retry_at TEXT,
 last_error TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX high_odds_registration_queue ON sports_ai_high_odds_tickets(registration_state,next_retry_at);

CREATE TABLE sports_ai_high_odds_runs (
 id TEXT PRIMARY KEY,
 event_id TEXT NOT NULL REFERENCES sports_ai_high_odds_events(id),
 category_id TEXT NOT NULL REFERENCES sports_ai_high_odds_categories(id),
 status TEXT NOT NULL,
 candidate_count INTEGER NOT NULL,
 created_count INTEGER NOT NULL,
 report_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(event_id,category_id)
);
CREATE TABLE sports_ai_high_odds_audit (
 id TEXT PRIMARY KEY,
 ticket_id TEXT REFERENCES sports_ai_accumulator_tickets(id),
 event_id TEXT REFERENCES sports_ai_high_odds_events(id),
 action TEXT NOT NULL,
 details_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX high_odds_audit_ticket ON sports_ai_high_odds_audit(ticket_id,created_at);
