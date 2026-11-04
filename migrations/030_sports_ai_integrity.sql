ALTER TABLE sports_ai_prediction_reviews ADD COLUMN disposition TEXT NOT NULL DEFAULT 'flagged';

CREATE TABLE IF NOT EXISTS sports_ai_feature_audits (
 id TEXT PRIMARY KEY,
 subject_id TEXT NOT NULL,
 finding TEXT NOT NULL,
 detail TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(subject_id, finding)
);

INSERT OR IGNORE INTO sports_ai_roles (id, role_name, permissions_json) VALUES
 ('role-super-administrator','super_administrator','["read","models.activate","policy.write","booking.execute","diagnostics.admin","config.write","credentials.manage"]'),
 ('role-administrator','administrator','["read","models.activate","policy.write","booking.execute","diagnostics.admin","config.write"]'),
 ('role-analyst','analyst','["read"]'),
 ('role-operator','operator','["read","booking.execute"]'),
 ('role-read-only','read_only','["read"]');
