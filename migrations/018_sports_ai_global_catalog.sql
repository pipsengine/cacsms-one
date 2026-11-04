CREATE TABLE IF NOT EXISTS sports_ai_sports (
 code TEXT PRIMARY KEY,
 name TEXT NOT NULL,
 prediction_status TEXT NOT NULL CHECK(prediction_status IN ('modelled','not_modelled')),
 acquisition_status TEXT NOT NULL CHECK(acquisition_status IN ('adapters_available','awaiting_authorized_source','source_catalog_only')),
 notes TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sports_ai_countries (
 code TEXT PRIMARY KEY,
 name TEXT NOT NULL,
 source_url TEXT NOT NULL,
 observed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sports_ai_leagues (
 id TEXT PRIMARY KEY,
 sport_code TEXT NOT NULL REFERENCES sports_ai_sports(code),
 country_code TEXT,
 name TEXT NOT NULL,
 division TEXT NOT NULL DEFAULT '',
 season TEXT NOT NULL DEFAULT '',
 canonical_key TEXT NOT NULL,
 league_kind TEXT NOT NULL DEFAULT 'competition' CHECK(league_kind IN ('competition','odds_market')),
 status TEXT NOT NULL DEFAULT 'discovered',
 quality_score REAL,
 freshness TEXT NOT NULL DEFAULT 'not_collected',
 last_observed_at TEXT,
 fixture_count INTEGER NOT NULL DEFAULT 0,
 result_count INTEGER NOT NULL DEFAULT 0,
 source_count INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(sport_code, canonical_key, season)
);

CREATE TABLE IF NOT EXISTS sports_ai_league_sources (
 id TEXT PRIMARY KEY,
 league_id TEXT NOT NULL REFERENCES sports_ai_leagues(id) ON DELETE CASCADE,
 provider_id TEXT REFERENCES sports_ai_provider_registry(id) ON DELETE SET NULL,
 provider_key TEXT NOT NULL,
 external_id TEXT NOT NULL,
 source_url TEXT NOT NULL,
 priority INTEGER NOT NULL DEFAULT 100,
 observed_at TEXT NOT NULL,
 UNIQUE(provider_key, external_id)
);

CREATE TABLE IF NOT EXISTS sports_ai_teams (
 id TEXT PRIMARY KEY,
 sport_code TEXT NOT NULL REFERENCES sports_ai_sports(code),
 canonical_key TEXT NOT NULL,
 name TEXT NOT NULL,
 country_code TEXT,
 source_url TEXT NOT NULL DEFAULT '',
 observed_at TEXT NOT NULL,
 UNIQUE(sport_code, canonical_key)
);

CREATE TABLE IF NOT EXISTS sports_ai_team_aliases (
 id TEXT PRIMARY KEY,
 team_id TEXT NOT NULL REFERENCES sports_ai_teams(id) ON DELETE CASCADE,
 alias TEXT NOT NULL,
 alias_key TEXT NOT NULL,
 provider_key TEXT NOT NULL,
 UNIQUE(team_id, alias_key, provider_key)
);

CREATE TABLE IF NOT EXISTS sports_ai_catalog_runs (
 id TEXT PRIMARY KEY,
 provider_key TEXT NOT NULL,
 kind TEXT NOT NULL,
 status TEXT NOT NULL,
 records_seen INTEGER NOT NULL DEFAULT 0,
 records_saved INTEGER NOT NULL DEFAULT 0,
 source_url TEXT NOT NULL DEFAULT '',
 evidence_sha256 TEXT NOT NULL DEFAULT '',
 error TEXT,
 started_at TEXT NOT NULL,
 finished_at TEXT
);

CREATE TABLE IF NOT EXISTS sports_ai_catalog_evidence (
 id TEXT PRIMARY KEY,
 run_id TEXT NOT NULL REFERENCES sports_ai_catalog_runs(id) ON DELETE CASCADE,
 source_url TEXT NOT NULL,
 sha256 TEXT NOT NULL,
 byte_length INTEGER NOT NULL,
 storage_path TEXT NOT NULL DEFAULT '',
 captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sports_ai_backfill_tasks (
 id TEXT PRIMARY KEY,
 league_source_id TEXT NOT NULL UNIQUE REFERENCES sports_ai_league_sources(id) ON DELETE CASCADE,
 provider_id TEXT REFERENCES sports_ai_provider_registry(id) ON DELETE SET NULL,
 status TEXT NOT NULL CHECK(status IN ('held','queued','running','complete','failed','skipped')),
 attempt INTEGER NOT NULL DEFAULT 0,
 records_saved INTEGER NOT NULL DEFAULT 0,
 last_error TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sports_ai_fixture_reconciliations (
 id TEXT PRIMARY KEY,
 fixture_id TEXT NOT NULL REFERENCES sports_ai_fixtures(id) ON DELETE CASCADE,
 provider_key TEXT NOT NULL,
 external_id TEXT NOT NULL,
 status TEXT NOT NULL,
 detail TEXT NOT NULL DEFAULT '',
 observed_at TEXT NOT NULL,
 UNIQUE(fixture_id, provider_key, external_id)
);

ALTER TABLE sports_ai_fixtures ADD COLUMN league_id TEXT;
ALTER TABLE sports_ai_fixtures ADD COLUMN canonical_key TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS sports_ai_leagues_sport_name ON sports_ai_leagues(sport_code, name);
CREATE INDEX IF NOT EXISTS sports_ai_leagues_country ON sports_ai_leagues(country_code);
CREATE INDEX IF NOT EXISTS sports_ai_teams_sport_name ON sports_ai_teams(sport_code, name);
CREATE INDEX IF NOT EXISTS sports_ai_catalog_runs_finished ON sports_ai_catalog_runs(kind, finished_at);
CREATE INDEX IF NOT EXISTS sports_ai_backfill_status ON sports_ai_backfill_tasks(status, updated_at);
CREATE INDEX IF NOT EXISTS sports_ai_fixtures_league ON sports_ai_fixtures(league_id);
CREATE INDEX IF NOT EXISTS sports_ai_fixtures_canonical ON sports_ai_fixtures(canonical_key);

INSERT OR IGNORE INTO sports_ai_sports (code, name, prediction_status, acquisition_status, notes) VALUES
 ('football','Football','modelled','adapters_available','OpenLigaDB, OpenFootball and football-data.org adapters are registered. football-data.org stays idle until SPORTS_FOOTBALL_DATA_API_KEY is set.'),
 ('basketball','Basketball','modelled','adapters_available','BALLDONTLIE and SportsDataverse CSV adapters are registered. BALLDONTLIE stays idle until SPORTS_BALLDONTLIE_API_KEY is set. A dataset URL is required before SportsDataverse collection.'),
 ('tennis','Tennis','not_modelled','awaiting_authorized_source','No tennis fixture, result or price is stored until an administrator approves an authorized feed. Football and basketball models do not score tennis.'),
 ('volleyball','Volleyball','not_modelled','awaiting_authorized_source','No volleyball fixture, result or price is stored until an administrator approves an authorized feed.'),
 ('baseball','Baseball','not_modelled','awaiting_authorized_source','No baseball fixture or result is stored until an authorized feed is approved. The Odds API can register market keys only when SPORTS_ODDS_API_KEY is set; it does not create scores.'),
 ('ice_hockey','Ice hockey','not_modelled','awaiting_authorized_source','No ice-hockey fixture or result is stored until an authorized feed is approved. The Odds API can register market keys only when SPORTS_ODDS_API_KEY is set; it does not create scores.');
