ALTER TABLE sports_ai_provider_registry ADD COLUMN terms_url TEXT NOT NULL DEFAULT '';
ALTER TABLE sports_ai_provider_registry ADD COLUMN credential_env TEXT NOT NULL DEFAULT '';
ALTER TABLE sports_ai_provider_registry ADD COLUMN interval_minutes INTEGER NOT NULL DEFAULT 360 CHECK(interval_minutes BETWEEN 5 AND 10080);
ALTER TABLE sports_ai_provider_registry ADD COLUMN last_attempt_at TEXT;
ALTER TABLE sports_ai_provider_registry ADD COLUMN next_run_at TEXT;
ALTER TABLE sports_ai_provider_registry ADD COLUMN cursor_json TEXT NOT NULL DEFAULT '{}';

CREATE TABLE IF NOT EXISTS sports_ai_fixture_source_map (
 id TEXT PRIMARY KEY,
 provider_id TEXT NOT NULL REFERENCES sports_ai_provider_registry(id) ON DELETE CASCADE,
 external_id TEXT NOT NULL,
 fixture_id TEXT NOT NULL REFERENCES sports_ai_fixtures(id) ON DELETE CASCADE,
 source_updated_at TEXT,
 observed_at TEXT NOT NULL,
 time_precision TEXT NOT NULL DEFAULT 'instant' CHECK(time_precision IN ('date','instant')),
 UNIQUE(provider_id,external_id)
);

CREATE TABLE IF NOT EXISTS sports_ai_match_statistics (
 id TEXT PRIMARY KEY,
 fixture_id TEXT NOT NULL REFERENCES sports_ai_fixtures(id) ON DELETE CASCADE,
 provider_id TEXT NOT NULL REFERENCES sports_ai_provider_registry(id),
 metric TEXT NOT NULL,
 value_json TEXT NOT NULL,
 unit TEXT NOT NULL DEFAULT '',
 source_url TEXT NOT NULL,
 source_updated_at TEXT,
 observed_at TEXT NOT NULL,
 UNIQUE(fixture_id,provider_id,metric,observed_at)
);

CREATE TABLE IF NOT EXISTS sports_ai_standings_snapshots (
 id TEXT PRIMARY KEY,
 provider_id TEXT NOT NULL REFERENCES sports_ai_provider_registry(id),
 competition TEXT NOT NULL,
 season TEXT NOT NULL,
 team TEXT NOT NULL,
 position INTEGER,
 played INTEGER,
 won INTEGER,
 drawn INTEGER,
 lost INTEGER,
 points INTEGER,
 goal_difference INTEGER,
 details_json TEXT NOT NULL DEFAULT '{}',
 source_url TEXT NOT NULL,
 observed_at TEXT NOT NULL,
 UNIQUE(provider_id,competition,season,team,observed_at)
);

CREATE TABLE IF NOT EXISTS sports_ai_source_permissions (
 id TEXT PRIMARY KEY,
 hostname TEXT NOT NULL UNIQUE,
 terms_url TEXT NOT NULL,
 notes TEXT NOT NULL DEFAULT '',
 approved INTEGER NOT NULL DEFAULT 0 CHECK(approved IN (0,1)),
 approved_by TEXT,
 reviewed_at TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sports_ai_browser_sessions (
 id TEXT PRIMARY KEY,
 fixture_id TEXT REFERENCES sports_ai_fixtures(id) ON DELETE SET NULL,
 query TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('running','complete','partial','failed')),
 started_at TEXT NOT NULL,
 finished_at TEXT,
 requested_urls_json TEXT NOT NULL DEFAULT '[]',
 pages_json TEXT NOT NULL DEFAULT '[]',
 error TEXT,
 actor TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sports_ai_browser_pages (
 id TEXT PRIMARY KEY,
 session_id TEXT NOT NULL REFERENCES sports_ai_browser_sessions(id) ON DELETE CASCADE,
 source_id TEXT REFERENCES sports_ai_provider_registry(id) ON DELETE SET NULL,
 requested_url TEXT NOT NULL,
 final_url TEXT,
 title TEXT,
 status_code INTEGER,
 screenshot_path TEXT,
 captured_at TEXT NOT NULL,
 error TEXT,
 UNIQUE(session_id,requested_url)
);

CREATE TABLE IF NOT EXISTS sports_ai_extraction_configs (
 id TEXT PRIMARY KEY,
 provider_id TEXT NOT NULL UNIQUE REFERENCES sports_ai_provider_registry(id) ON DELETE CASCADE,
 fields_json TEXT NOT NULL DEFAULT '{}',
 updated_by TEXT NOT NULL,
 updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sports_ai_extraction_evidence (
 id TEXT PRIMARY KEY,
 session_id TEXT NOT NULL REFERENCES sports_ai_browser_sessions(id) ON DELETE CASCADE,
 page_id TEXT NOT NULL REFERENCES sports_ai_browser_pages(id) ON DELETE CASCADE,
 fixture_id TEXT REFERENCES sports_ai_fixtures(id) ON DELETE SET NULL,
 field TEXT NOT NULL,
 value_json TEXT NOT NULL,
 source_url TEXT NOT NULL,
 captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sports_ai_acquisition_jobs (
 id TEXT PRIMARY KEY,
 provider_id TEXT REFERENCES sports_ai_provider_registry(id) ON DELETE SET NULL,
 session_id TEXT REFERENCES sports_ai_browser_sessions(id) ON DELETE SET NULL,
 retry_of TEXT REFERENCES sports_ai_acquisition_jobs(id) ON DELETE SET NULL,
 kind TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('running','complete','partial','failed')),
 attempt INTEGER NOT NULL DEFAULT 1,
 started_at TEXT NOT NULL,
 finished_at TEXT,
 records_seen INTEGER NOT NULL DEFAULT 0,
 records_saved INTEGER NOT NULL DEFAULT 0,
 logs_json TEXT NOT NULL DEFAULT '[]',
 error TEXT
);

CREATE TABLE IF NOT EXISTS sports_ai_acquisition_schedules (
 provider_id TEXT PRIMARY KEY REFERENCES sports_ai_provider_registry(id) ON DELETE CASCADE,
 enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
 interval_minutes INTEGER NOT NULL DEFAULT 360 CHECK(interval_minutes BETWEEN 5 AND 10080),
 paused INTEGER NOT NULL DEFAULT 0 CHECK(paused IN (0,1)),
 updated_by TEXT NOT NULL,
 updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS sports_ai_fixture_source_lookup
 ON sports_ai_fixture_source_map(provider_id,external_id);
CREATE INDEX IF NOT EXISTS sports_ai_match_stats_fixture_time
 ON sports_ai_match_statistics(fixture_id,observed_at);
CREATE INDEX IF NOT EXISTS sports_ai_standings_competition_time
 ON sports_ai_standings_snapshots(competition,observed_at);
CREATE INDEX IF NOT EXISTS sports_ai_browser_sessions_started
 ON sports_ai_browser_sessions(started_at);
CREATE INDEX IF NOT EXISTS sports_ai_evidence_fixture_time
 ON sports_ai_extraction_evidence(fixture_id,captured_at);
CREATE INDEX IF NOT EXISTS sports_ai_acquisition_jobs_started
 ON sports_ai_acquisition_jobs(started_at,status);

UPDATE sports_ai_provider_registry
 SET terms_url='https://www.football-data.org/coverage',
     credential_env='SPORTS_FOOTBALL_DATA_API_KEY'
 WHERE provider_key='football-data';
UPDATE sports_ai_provider_registry
 SET terms_url='https://www.openligadb.de/Documentation'
 WHERE provider_key='openligadb';
UPDATE sports_ai_provider_registry
 SET terms_url='https://github.com/openfootball/football.json'
 WHERE provider_key='openfootball';
UPDATE sports_ai_provider_registry
 SET terms_url='https://docs.balldontlie.io/',
     credential_env='SPORTS_BALLDONTLIE_API_KEY'
 WHERE provider_key='balldontlie';
UPDATE sports_ai_provider_registry
 SET terms_url='https://sportsdataverse.org/',
     credential_env=''
 WHERE provider_key='sportsdataverse';
UPDATE sports_ai_provider_registry
 SET terms_url='https://sportsdataverse.org/',
     credential_env=''
 WHERE provider_key='basketball-csv';
UPDATE sports_ai_provider_registry
 SET terms_url='https://the-odds-api.com/#get-access',
     credential_env='SPORTS_ODDS_API_KEY'
 WHERE provider_key='the-odds-api';

INSERT OR IGNORE INTO sports_ai_provider_registry
 (id,provider_key,display_name,sport,adapter,status,config_json,terms_url,credential_env)
 VALUES
 ('provider-football-data','football-data','football-data.org','football','football_data','untested','{"competition":"PL","season":2026}','https://www.football-data.org/coverage','SPORTS_FOOTBALL_DATA_API_KEY'),
 ('provider-balldontlie','balldontlie','BALLDONTLIE NBA','basketball','balldontlie','untested','{"season":2026,"per_page":100}','https://docs.balldontlie.io/','SPORTS_BALLDONTLIE_API_KEY'),
 ('provider-sportsdataverse','sportsdataverse','SportsDataverse basketball dataset','basketball','sportsdataverse_csv','untested','{"dataset_url":""}','https://sportsdataverse.org/',''),
 ('provider-betexplorer','betexplorer','BetExplorer (research reference)','both','browser','disabled','{"reference_only":true}','https://www.betexplorer.com/','');

UPDATE sports_ai_provider_registry
 SET terms_url='https://the-odds-api.com/#get-access'
 WHERE provider_key='the-odds-api' AND terms_url='';

INSERT OR IGNORE INTO sports_ai_source_permissions
 (id,hostname,terms_url,notes,approved,reviewed_at)
 VALUES ('permission-betexplorer-reference','www.betexplorer.com',
         'https://www.betexplorer.com/','Reference only; automated extraction is not approved.',0,CURRENT_TIMESTAMP);
