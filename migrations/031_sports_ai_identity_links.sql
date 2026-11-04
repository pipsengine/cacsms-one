CREATE TABLE IF NOT EXISTS sports_ai_fixture_identity_links (
 id TEXT PRIMARY KEY,
 source_fixture_id TEXT NOT NULL,
 canonical_fixture_id TEXT,
 status TEXT NOT NULL CHECK(status IN ('confirmed','ambiguous','unresolved','review')),
 confidence REAL,
 evidence_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(source_fixture_id)
);
