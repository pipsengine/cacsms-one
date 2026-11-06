CREATE TABLE sports_ai_draw_leagues_next (
 sport TEXT NOT NULL DEFAULT 'football',
 competition TEXT NOT NULL,
 matches INTEGER NOT NULL,
 draws INTEGER NOT NULL,
 draw_rate REAL,
 reliability REAL,
 uncertainty REAL,
 seasons INTEGER NOT NULL,
 score_00 REAL,
 score_11 REAL,
 score_22 REAL,
 goals_for REAL,
 goals_against REAL,
 quality TEXT NOT NULL,
 detail_json TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 PRIMARY KEY (sport, competition)
);

INSERT INTO sports_ai_draw_leagues_next
 (sport,competition,matches,draws,draw_rate,reliability,uncertainty,seasons,score_00,score_11,score_22,
  goals_for,goals_against,quality,detail_json,updated_at)
SELECT 'football',competition,matches,draws,draw_rate,reliability,uncertainty,seasons,score_00,score_11,score_22,
       goals_for,goals_against,quality,detail_json,updated_at
FROM sports_ai_draw_leagues;

DROP TABLE sports_ai_draw_leagues;

ALTER TABLE sports_ai_draw_leagues_next RENAME TO sports_ai_draw_leagues;

CREATE TABLE sports_ai_draw_teams_next (
 sport TEXT NOT NULL DEFAULT 'football',
 competition TEXT NOT NULL,
 team TEXT NOT NULL,
 matches INTEGER NOT NULL,
 draw_rate REAL,
 home_matches INTEGER NOT NULL,
 home_draw_rate REAL,
 away_matches INTEGER NOT NULL,
 away_draw_rate REAL,
 roll5_matches INTEGER NOT NULL,
 roll5_rate REAL,
 roll10_matches INTEGER NOT NULL,
 roll10_rate REAL,
 roll20_matches INTEGER NOT NULL,
 roll20_rate REAL,
 goals_for REAL,
 goals_against REAL,
 reliability REAL,
 uncertainty REAL,
 schedule_strength REAL,
 adjusted_balance REAL,
 quality TEXT NOT NULL,
 detail_json TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 PRIMARY KEY (sport, competition, team)
);

INSERT INTO sports_ai_draw_teams_next
 (sport,competition,team,matches,draw_rate,home_matches,home_draw_rate,away_matches,away_draw_rate,
  roll5_matches,roll5_rate,roll10_matches,roll10_rate,roll20_matches,roll20_rate,
  goals_for,goals_against,reliability,uncertainty,schedule_strength,adjusted_balance,
  quality,detail_json,updated_at)
SELECT 'football',competition,team,matches,draw_rate,home_matches,home_draw_rate,away_matches,away_draw_rate,
       roll5_matches,roll5_rate,roll10_matches,roll10_rate,roll20_matches,roll20_rate,
       goals_for,goals_against,reliability,uncertainty,schedule_strength,adjusted_balance,
       quality,detail_json,updated_at
FROM sports_ai_draw_teams;

DROP TABLE sports_ai_draw_teams;

ALTER TABLE sports_ai_draw_teams_next RENAME TO sports_ai_draw_teams;

CREATE TABLE sports_ai_draw_fixtures_next (
 fixture_id TEXT PRIMARY KEY,
 sport TEXT NOT NULL DEFAULT 'football',
 competition TEXT NOT NULL,
 home TEXT NOT NULL,
 away TEXT NOT NULL,
 starts_at TEXT NOT NULL,
 model_probability REAL,
 implied_probability REAL,
 edge REAL,
 calibrated INTEGER NOT NULL,
 qualification_state TEXT,
 league_reliability REAL,
 home_reliability REAL,
 away_reliability REAL,
 rank_score REAL,
 explanation TEXT NOT NULL,
 quality TEXT NOT NULL,
 updated_at TEXT NOT NULL
);

INSERT INTO sports_ai_draw_fixtures_next
 (fixture_id,sport,competition,home,away,starts_at,model_probability,implied_probability,edge,calibrated,
  qualification_state,league_reliability,home_reliability,away_reliability,rank_score,explanation,quality,updated_at)
SELECT fixture_id,'football',competition,home,away,starts_at,model_probability,implied_probability,edge,calibrated,
       qualification_state,league_reliability,home_reliability,away_reliability,rank_score,explanation,quality,updated_at
FROM sports_ai_draw_fixtures;

DROP TABLE sports_ai_draw_fixtures;

ALTER TABLE sports_ai_draw_fixtures_next RENAME TO sports_ai_draw_fixtures;

CREATE INDEX IF NOT EXISTS sports_ai_draw_fixtures_rank
 ON sports_ai_draw_fixtures(rank_score, starts_at);
