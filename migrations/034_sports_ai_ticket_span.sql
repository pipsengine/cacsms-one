UPDATE sports_ai_accumulator_runs SET config_id=NULL WHERE config_id IS NOT NULL;

CREATE TABLE sports_ai_accumulator_configs_next (
 id TEXT PRIMARY KEY,
 name TEXT NOT NULL,
 targets_json TEXT NOT NULL,
 tolerance REAL NOT NULL CHECK(tolerance>0 AND tolerance<1),
 min_legs INTEGER NOT NULL CHECK(min_legs>=2 AND min_legs<=25),
 max_legs INTEGER NOT NULL CHECK(max_legs>=min_legs AND max_legs<=25),
 max_per_competition INTEGER NOT NULL CHECK(max_per_competition BETWEEN 1 AND 12),
 max_per_team INTEGER NOT NULL CHECK(max_per_team BETWEEN 1 AND 6),
 min_probability REAL NOT NULL CHECK(min_probability>=0 AND min_probability<1),
 min_edge REAL NOT NULL,
 min_model_reliability REAL NOT NULL CHECK(min_model_reliability>=0 AND min_model_reliability<=1),
 min_completeness REAL NOT NULL CHECK(min_completeness>=0 AND min_completeness<=1),
 odds_max_age_minutes INTEGER NOT NULL CHECK(odds_max_age_minutes BETWEEN 1 AND 1440),
 max_tickets_per_tier INTEGER NOT NULL CHECK(max_tickets_per_tier BETWEEN 1 AND 20),
 beam_width INTEGER NOT NULL CHECK(beam_width BETWEEN 4 AND 200),
 search_limit INTEGER NOT NULL CHECK(search_limit BETWEEN 100 AND 200000),
 timeout_seconds INTEGER NOT NULL CHECK(timeout_seconds BETWEEN 1 AND 120),
 allow_same_fixture INTEGER NOT NULL DEFAULT 0 CHECK(allow_same_fixture IN (0,1)),
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO sports_ai_accumulator_configs_next
 (id,name,targets_json,tolerance,min_legs,max_legs,max_per_competition,max_per_team,
  min_probability,min_edge,min_model_reliability,min_completeness,odds_max_age_minutes,
  max_tickets_per_tier,beam_width,search_limit,timeout_seconds,allow_same_fixture,updated_at)
SELECT id,name,targets_json,tolerance,min_legs,max_legs,max_per_competition,max_per_team,
       min_probability,min_edge,min_model_reliability,min_completeness,odds_max_age_minutes,
       max_tickets_per_tier,beam_width,search_limit,timeout_seconds,allow_same_fixture,updated_at
FROM sports_ai_accumulator_configs;

DROP TABLE sports_ai_accumulator_configs;

ALTER TABLE sports_ai_accumulator_configs_next RENAME TO sports_ai_accumulator_configs;

UPDATE sports_ai_accumulator_configs SET max_legs=25, updated_at=CURRENT_TIMESTAMP
 WHERE id='accumulator-default' AND max_legs=8;

UPDATE sports_ai_accumulator_runs SET config_id='accumulator-default' WHERE config_id IS NULL;

CREATE TABLE sports_ai_accumulator_legs_next (
 id TEXT PRIMARY KEY,
 ticket_id TEXT NOT NULL REFERENCES sports_ai_accumulator_tickets(id),
 opportunity_id TEXT NOT NULL,
 fixture_id TEXT NOT NULL,
 sport TEXT NOT NULL,
 competition TEXT NOT NULL,
 home TEXT NOT NULL,
 away TEXT NOT NULL,
 starts_at TEXT NOT NULL,
 market TEXT NOT NULL,
 selection TEXT NOT NULL,
 decimal_odds REAL NOT NULL CHECK(decimal_odds>1),
 probability REAL,
 edge REAL,
 bookmaker TEXT NOT NULL,
 odds_observed_at TEXT NOT NULL,
 quote_market TEXT NOT NULL,
 quote_outcome TEXT NOT NULL,
 quote_line REAL,
 model_id TEXT NOT NULL,
 model_version TEXT NOT NULL,
 data_completeness REAL,
 reliability REAL,
 settlement_kind TEXT NOT NULL CHECK(settlement_kind IN ('binary','partial','unknown')),
 result TEXT NOT NULL DEFAULT 'pending' CHECK(result IN ('pending','won','lost','void','push','half_win','half_loss')),
 qualification_json TEXT NOT NULL,
 UNIQUE(ticket_id,fixture_id)
);

INSERT INTO sports_ai_accumulator_legs_next
 (id,ticket_id,opportunity_id,fixture_id,sport,competition,home,away,starts_at,market,selection,
  decimal_odds,probability,edge,bookmaker,odds_observed_at,quote_market,quote_outcome,quote_line,
  model_id,model_version,data_completeness,reliability,settlement_kind,result,qualification_json)
SELECT id,ticket_id,opportunity_id,fixture_id,sport,competition,home,away,starts_at,market,selection,
       decimal_odds,probability,edge,bookmaker,odds_observed_at,quote_market,quote_outcome,quote_line,
       model_id,model_version,data_completeness,reliability,settlement_kind,result,qualification_json
FROM sports_ai_accumulator_legs;

DROP TABLE sports_ai_accumulator_legs;

ALTER TABLE sports_ai_accumulator_legs_next RENAME TO sports_ai_accumulator_legs;

CREATE INDEX IF NOT EXISTS sports_ai_accumulator_legs_fixture
 ON sports_ai_accumulator_legs(fixture_id,ticket_id);
