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

CREATE TABLE IF NOT EXISTS sports_ai_sportybet_observations (
 id TEXT PRIMARY KEY,
 session_id TEXT,
 sport TEXT,
 competition TEXT,
 home TEXT NOT NULL,
 away TEXT NOT NULL,
 starts_at TEXT,
 market_label TEXT NOT NULL,
 selection_label TEXT NOT NULL,
 decimal_odds REAL,
 source_url TEXT NOT NULL,
 observed_at TEXT NOT NULL,
 confirmation_status TEXT NOT NULL CHECK(confirmation_status IN ('proposed','confirmed','rejected')),
 match_status TEXT NOT NULL DEFAULT 'unmatched' CHECK(match_status IN ('unmatched','proposed','confirmed','ambiguous')),
 fixture_id TEXT,
 match_confidence REAL,
 market_status TEXT NOT NULL DEFAULT 'unvalidated' CHECK(market_status IN ('unvalidated','confirmed','unsupported')),
 canonical_market TEXT,
 line REAL,
 expiry_status TEXT NOT NULL DEFAULT 'current' CHECK(expiry_status IN ('current','stale','expired')),
 import_status TEXT NOT NULL DEFAULT 'not_imported' CHECK(import_status IN ('not_imported','imported','rejected')),
 odds_id TEXT,
 value_status TEXT,
 evidence_json TEXT NOT NULL DEFAULT '{}',
 rejection_reason TEXT,
 created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS sports_ai_sportybet_observation_identity
 ON sports_ai_sportybet_observations(
  source_url, home, away, market_label, selection_label, observed_at, IFNULL(decimal_odds, -1)
 );

CREATE INDEX IF NOT EXISTS sports_ai_sportybet_observations_status
 ON sports_ai_sportybet_observations(confirmation_status, match_status, created_at);

CREATE TABLE IF NOT EXISTS sports_ai_collection_authorizations (
 id TEXT PRIMARY KEY,
 bookmaker TEXT NOT NULL,
 scope TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('authorized','revoked')),
 evidence TEXT NOT NULL,
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL,
 expires_at TEXT,
 UNIQUE(bookmaker, scope, status)
);

ALTER TABLE sports_ai_odds_snapshots ADD COLUMN confirmation_status TEXT NOT NULL DEFAULT 'imported';
ALTER TABLE sports_ai_odds_snapshots ADD COLUMN expiry_status TEXT NOT NULL DEFAULT 'current';
ALTER TABLE sports_ai_odds_snapshots ADD COLUMN original_label TEXT;
ALTER TABLE sports_ai_odds_snapshots ADD COLUMN evidence_json TEXT NOT NULL DEFAULT '{}';

INSERT OR IGNORE INTO sports_ai_provider_registry
 (id,provider_key,display_name,sport,adapter,enabled,terms_reviewed,status,config_json)
 VALUES
 ('provider-sportybet','sportybet','SportyBet','both','manual',0,0,'untested','{}');

INSERT OR IGNORE INTO sports_ai_roles (id,role_name,permissions_json)
 VALUES (
  'role-sportybet-operator',
  'sportybet-operator',
  '["browser.control","quote.confirm","odds.import"]'
 );
