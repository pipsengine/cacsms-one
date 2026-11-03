UPDATE sports_ai_provider_registry
 SET config_json='{"dataset_url":"https://raw.githubusercontent.com/openfootball/football.json/master/2026-27/en.1.json"}',
     status='untested'
 WHERE provider_key='openfootball' AND credential_env='';

UPDATE sports_ai_provider_registry
 SET config_json='{"dataset_url":"https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_nba_schedules/nba_schedule_2027.csv","season":"2027"}',
     status='untested'
 WHERE provider_key='sportsdataverse' AND credential_env='';
