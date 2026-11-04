CREATE TABLE sports_ai_market_registry_next (
 id TEXT PRIMARY KEY,
 sport TEXT NOT NULL,
 market_key TEXT NOT NULL,
 selection TEXT NOT NULL,
 period TEXT NOT NULL,
 line_value REAL,
 settlement_rule TEXT NOT NULL,
 data_requirements TEXT NOT NULL,
 model_source TEXT NOT NULL,
 model_version TEXT,
 status TEXT NOT NULL CHECK(status IN ('discovered','data_pending','research','validated','active','suspended','unsupported','blocked')),
 evidence_json TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(sport, market_key)
);

INSERT INTO sports_ai_market_registry_next
 (id,sport,market_key,selection,period,line_value,settlement_rule,data_requirements,model_source,model_version,status,evidence_json,updated_at)
SELECT id,sport,market_key,selection,period,line_value,settlement_rule,data_requirements,model_source,model_version,
       CASE status WHEN 'blocked' THEN 'data_pending' ELSE status END,
       evidence_json,updated_at
FROM sports_ai_market_registry;

DROP TABLE sports_ai_market_registry;

ALTER TABLE sports_ai_market_registry_next RENAME TO sports_ai_market_registry;

CREATE INDEX IF NOT EXISTS sports_ai_market_registry_status
 ON sports_ai_market_registry(sport, status);
