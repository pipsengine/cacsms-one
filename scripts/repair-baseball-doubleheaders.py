"""Split the two verified BetExplorer doubleheaders without overwriting scores.

Input is the captured source result rows in reports/baseball-conflict-source-records.json.
The repair report preserves the affected rows for review and rollback.
"""
import json
from pathlib import Path
import sys
import uuid
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from db import connect
from history_browser import _store


def main():
    records = {row['external_id']: row for row in json.loads(
        (ROOT / 'reports/baseball-conflict-source-records.json').read_text(encoding='utf-8'))}
    pairs = [('lO04gs1C', 'r1G8pnIs'), ('8palcP1m', '0Qnjuit2')]
    now = datetime.now(timezone.utc).isoformat()
    report = {'repaired_at': now, 'pairs': []}
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        provider = dict(connection.execute(
            "SELECT * FROM sports_ai_provider_registry WHERE provider_key='betexplorer'").fetchone())
        for original_id, second_id in pairs:
            original, second = records[original_id], records[second_id]
            assert original['sport'] == second['sport'] == 'baseball'
            assert original['home'] == second['home'] and original['away'] == second['away']
            assert original['starts_at'] != second['starts_at']
            mappings = [dict(row) for row in connection.execute('''SELECT *
                FROM sports_ai_fixture_source_map WHERE provider_id=? AND external_id IN (?,?)''',
                (provider['id'], original_id, second_id))]
            assert len(mappings) == 2
            first_map = next(row for row in mappings if row['external_id'] == original_id)
            second_map = next(row for row in mappings if row['external_id'] == second_id)
            fid = first_map['fixture_id']
            if second_map['fixture_id'] != fid:
                report['pairs'].append({'original': original_id, 'second': second_id, 'already_split': True})
                continue
            fixture = dict(connection.execute('SELECT * FROM sports_ai_fixtures WHERE id=?', (fid,)).fetchone())
            assert fixture['external_id'] == original_id and fixture['source'] == 'betexplorer'
            stored = dict(connection.execute('SELECT * FROM sports_ai_results WHERE fixture_id=?', (fid,)).fetchone())
            assert [stored['home_score'], stored['away_score']] == [original['home_score'], original['away_score']]
            issues = [dict(row) for row in connection.execute('''SELECT * FROM sports_ai_data_quality_issues
                WHERE fixture_id=? AND provider_id=? AND issue_type='result_conflict'
                  AND resolved_at IS NULL''', (fid, provider['id']))]
            assert len(issues) == 1
            details = json.loads(issues[0]['details_json'])
            assert details['incoming'] == [second['home_score'], second['away_score']]
            entry = {'fixture_before': fixture, 'result_before': stored, 'mappings_before': mappings,
                     'issue_before': issues[0], 'verified_sources': [original, second]}
            connection.execute('DELETE FROM sports_ai_fixture_source_map WHERE id=?', (second_map['id'],))
            connection.execute('''DELETE FROM sports_ai_fixture_reconciliations
                WHERE fixture_id=? AND provider_key='betexplorer' AND external_id=?''', (fid, second_id))
            _store(connection, provider, [original, second], now)
            new_id = connection.execute('''SELECT fixture_id FROM sports_ai_fixture_source_map
                WHERE provider_id=? AND external_id=?''', (provider['id'], second_id)).fetchone()[0]
            assert new_id != fid
            entry['new_fixture_id'] = new_id
            details['resolution'] = {'reason': 'Two distinct doubleheader event IDs were incorrectly merged.',
                                     'new_fixture_id': new_id, 'sources': [original['source_url'], second['source_url']]}
            connection.execute('''UPDATE sports_ai_data_quality_issues SET resolved_at=?,details_json=?
                WHERE id=?''', (now, json.dumps(details), issues[0]['id']))
            connection.execute('''INSERT INTO sports_ai_audit_events
                (id,actor,action,metadata_json,created_at) VALUES(?,?,?,?,?)''',
                (str(uuid.uuid4()), 'maintenance', 'results.doubleheader_repair', json.dumps(entry), now))
            report['pairs'].append(entry)
        # Write the recovery evidence before committing the database changes.
        (ROOT / 'reports/baseball-doubleheader-repair.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'pairs_checked': len(report['pairs']), 'report': 'reports/baseball-doubleheader-repair.json'}))


if __name__ == '__main__':
    main()
