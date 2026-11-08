"""Repair stored identities and refresh sport models without relaxing validation."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from db import connect, initialize
from analysis_coverage import coverage_report, repair_identities, refresh_models
from catalog_engine import set_backfill_policy
from prediction_engine import govern_models, run_analysis


def main():
    initialize()  # Additive migration with a timestamped SQLite backup.
    try:
        with connect() as connection:
            print(json.dumps(repair_identities(connection)), flush=True)
            set_backfill_policy(connection, 8, 'analysis-coverage-repair')
            connection.execute("DELETE FROM sports_ai_prediction_jobs WHERE idempotency_key LIKE 'insufficient:%'")
        for _ in range(40):
            with connect() as connection:
                result = refresh_models(connection, limit=1)
            if not result:
                break
            model = result[0]
            print(json.dumps({key: model[key] for key in ('sport', 'model_id', 'status', 'metrics')}), flush=True)
        with connect() as connection:
            governed = govern_models(connection, 'analysis-coverage-repair', None)
            print(json.dumps(governed), flush=True)
        for _ in range(10):
            with connect() as connection:
                result = run_analysis(connection, 'analysis-coverage-repair', lambda *args: None, coverage=True, limit=12)
            print(json.dumps(result), flush=True)
            if not result['fixtures']:
                break
        with connect() as connection:
            report = coverage_report(connection)
        output = Path(__file__).resolve().parents[1] / 'reports' / 'analysis-coverage-repair.json'
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps({'coverage_report': str(output), 'counts': report['counts'], 'models': report['models']}), flush=True)
    finally:
        with connect() as connection:
            connection.execute("UPDATE sports_ai_worker_leases SET expires_at='2000-01-01T00:00:00+00:00' WHERE owner='analysis-coverage-repair'")


if __name__ == '__main__':
    main()
