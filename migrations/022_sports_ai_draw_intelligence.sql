CREATE TABLE IF NOT EXISTS sports_ai_draw_leagues (
 competition TEXT PRIMARY KEY,
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
 updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sports_ai_draw_teams (
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
 PRIMARY KEY (competition, team)
);

CREATE TABLE IF NOT EXISTS sports_ai_draw_fixtures (
 fixture_id TEXT PRIMARY KEY,
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

CREATE INDEX IF NOT EXISTS sports_ai_draw_fixtures_rank
 ON sports_ai_draw_fixtures(rank_score, starts_at);
