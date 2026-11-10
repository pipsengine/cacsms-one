CREATE TABLE sports_ai_basketball_reconciliations (
 id TEXT PRIMARY KEY, selection_id TEXT NOT NULL REFERENCES sports_ai_basketball_bet_selections(id),
 prediction_id TEXT NOT NULL, placed_at TEXT NOT NULL, source_reference TEXT NOT NULL,
 snapshot_json TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TRIGGER sports_ai_basketball_reconciliation_immutable_update BEFORE UPDATE ON sports_ai_basketball_reconciliations BEGIN SELECT RAISE(ABORT,'Reconciliation evidence is immutable'); END;
CREATE TRIGGER sports_ai_basketball_reconciliation_immutable_delete BEFORE DELETE ON sports_ai_basketball_reconciliations BEGIN SELECT RAISE(ABORT,'Reconciliation evidence is immutable'); END;
