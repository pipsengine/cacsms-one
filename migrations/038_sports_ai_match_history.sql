INSERT OR IGNORE INTO sports_ai_provider_registry
 (id,provider_key,display_name,sport,adapter,enabled,terms_reviewed,status,config_json,terms_url)
 VALUES
 ('provider-soccerway','soccerway','Soccerway','football','history_browser',1,1,'untested',
  '{"site":"https://ng.soccerway.com/","displayed_scores_only":true,"places_wager":false}',
  'https://ng.soccerway.com/');

UPDATE sports_ai_provider_registry
 SET enabled=1,
     terms_reviewed=1,
     status='untested',
     config_json='{"site":"https://www.betexplorer.com/","displayed_scores_only":true,"places_wager":false}',
     terms_url='https://www.betexplorer.com/'
 WHERE provider_key='betexplorer';

INSERT OR IGNORE INTO sports_ai_source_permissions
 (id,hostname,terms_url,notes,approved,reviewed_at)
 VALUES
 ('permission-soccerway','ng.soccerway.com','https://ng.soccerway.com/',
  'Operator requested displayed football results. A score is stored only when the page shows it.',
  1,CURRENT_TIMESTAMP);

UPDATE sports_ai_source_permissions
 SET approved=1,
     terms_url='https://www.betexplorer.com/',
     notes='Operator requested displayed match history. A score is stored only when the page shows it. A challenge stores nothing.',
     reviewed_at=CURRENT_TIMESTAMP
 WHERE hostname='www.betexplorer.com';
