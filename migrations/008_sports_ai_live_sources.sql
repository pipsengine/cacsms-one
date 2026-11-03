ALTER TABLE sports_ai_fixture_source_map ADD COLUMN source_url TEXT NOT NULL DEFAULT '';

UPDATE sports_ai_provider_registry
 SET config_json='{"dataset_url":"https://raw.githubusercontent.com/openfootball/football.json/master/2025-26/en.1.json"}',
     terms_url='https://github.com/openfootball/football.json',
     enabled=1,
     terms_reviewed=1,
     status='untested'
 WHERE provider_key='openfootball' AND credential_env='';

UPDATE sports_ai_provider_registry
 SET enabled=1,
     terms_reviewed=1
 WHERE provider_key='openligadb'
   AND config_json LIKE '%"league":"bl1"%';

UPDATE sports_ai_provider_registry
 SET config_json='{"dataset_url":"https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_nba_schedules/nba_schedule_2025.csv","season":"2025"}',
     terms_url='https://github.com/sportsdataverse/sportsdataverse-data/releases/tag/espn_nba_schedules',
     enabled=1,
     terms_reviewed=1,
     status='untested'
 WHERE provider_key='sportsdataverse' AND credential_env='';

INSERT OR IGNORE INTO sports_ai_acquisition_schedules
 (provider_id,enabled,interval_minutes,paused,updated_by,updated_at)
VALUES
 ('provider-openligadb',1,360,0,'sports-ai-live-sources',CURRENT_TIMESTAMP),
 ('provider-openfootball',1,1440,0,'sports-ai-live-sources',CURRENT_TIMESTAMP),
 ('provider-sportsdataverse',1,1440,0,'sports-ai-live-sources',CURRENT_TIMESTAMP);

UPDATE sports_ai_acquisition_schedules
 SET enabled=1, paused=0, updated_by='sports-ai-live-sources', updated_at=CURRENT_TIMESTAMP
 WHERE provider_id IN ('provider-openligadb','provider-openfootball','provider-sportsdataverse');
