CREATE TABLE sports_ai_basketball_bet_evidence (
 id TEXT PRIMARY KEY, external_ticket_id TEXT NOT NULL UNIQUE,
 source_kind TEXT NOT NULL CHECK(source_kind IN ('sportybet_export','user_screenshot')),
 source_reference TEXT NOT NULL, content_hash TEXT NOT NULL, payload_json TEXT NOT NULL,
 currency TEXT NOT NULL, stake REAL NOT NULL CHECK(stake>0), total_return REAL NOT NULL CHECK(total_return>=0),
 outcome TEXT NOT NULL CHECK(outcome IN ('won','lost','void','partial')),
 placed_at TEXT, settled_at TEXT, imported_at TEXT NOT NULL
);
CREATE TABLE sports_ai_basketball_bet_selections (
 id TEXT PRIMARY KEY, evidence_id TEXT NOT NULL REFERENCES sports_ai_basketball_bet_evidence(id),
 ordinal INTEGER NOT NULL, fixture_id TEXT, home TEXT NOT NULL, away TEXT NOT NULL,
 sport TEXT NOT NULL, competition TEXT, starts_at TEXT NOT NULL,
 market TEXT NOT NULL, period TEXT NOT NULL, decimal_odds REAL NOT NULL CHECK(decimal_odds>1),
 result TEXT NOT NULL, home_score REAL, away_score REAL,
 prediction_id TEXT, attribution TEXT NOT NULL, snapshot_json TEXT NOT NULL,
 UNIQUE(evidence_id,ordinal)
);
CREATE TABLE sports_ai_basketball_loss_runs (
 id TEXT PRIMARY KEY, dataset_hash TEXT NOT NULL, policy_json TEXT NOT NULL,
 report_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE sports_ai_basketball_quarantines (
 id TEXT PRIMARY KEY, scope TEXT NOT NULL, model_id TEXT NOT NULL DEFAULT '*',
 competition TEXT NOT NULL DEFAULT '*', market_family TEXT NOT NULL DEFAULT '*',
 reason TEXT NOT NULL, evidence_json TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL, UNIQUE(scope,model_id,competition,market_family)
);
CREATE TABLE sports_ai_basketball_candidates (
 id TEXT PRIMARY KEY, dataset_hash TEXT NOT NULL, recipe_version TEXT NOT NULL,
 artifact_json TEXT NOT NULL, metrics_json TEXT NOT NULL, status TEXT NOT NULL,
 created_at TEXT NOT NULL, UNIQUE(dataset_hash,recipe_version)
);
CREATE TABLE sports_ai_basketball_market_approvals (
 candidate_id TEXT NOT NULL REFERENCES sports_ai_basketball_candidates(id),
 competition TEXT NOT NULL, market_family TEXT NOT NULL, period TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('validated','quarantined','research')),
 evidence_json TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(candidate_id,competition,market_family,period)
);
CREATE TABLE sports_ai_basketball_evidence_events (
 id TEXT PRIMARY KEY, action TEXT NOT NULL, actor TEXT NOT NULL,
 evidence_id TEXT, details_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX sports_ai_basketball_selection_fixture ON sports_ai_basketball_bet_selections(fixture_id,market);
ALTER TABLE sports_ai_odds_snapshots ADD COLUMN settlement_period TEXT NOT NULL DEFAULT 'unknown';
UPDATE sports_ai_odds_snapshots SET settlement_period='including_overtime'
 WHERE fixture_id IN (SELECT id FROM sports_ai_fixtures WHERE sport='basketball')
 AND (lower(original_label) LIKE '%including overtime%' OR lower(original_label) LIKE '%incl. overtime%' OR lower(original_label) LIKE '%incl overtime%');
CREATE TRIGGER sports_ai_basketball_evidence_immutable_update BEFORE UPDATE ON sports_ai_basketball_bet_evidence BEGIN SELECT RAISE(ABORT,'Bookmaker evidence is immutable'); END;
CREATE TRIGGER sports_ai_basketball_evidence_immutable_delete BEFORE DELETE ON sports_ai_basketball_bet_evidence BEGIN SELECT RAISE(ABORT,'Bookmaker evidence is immutable'); END;
CREATE TRIGGER sports_ai_basketball_selection_immutable_update BEFORE UPDATE ON sports_ai_basketball_bet_selections BEGIN SELECT RAISE(ABORT,'Original selection evidence is immutable'); END;
CREATE TRIGGER sports_ai_basketball_selection_immutable_delete BEFORE DELETE ON sports_ai_basketball_bet_selections BEGIN SELECT RAISE(ABORT,'Original selection evidence is immutable'); END;
