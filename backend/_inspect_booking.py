import sqlite3
from pathlib import Path

db = Path(__file__).with_name('sports_demo.sqlite3')
c = sqlite3.connect(db)
c.row_factory = sqlite3.Row

def q(sql, args=()):
    return [dict(r) for r in c.execute(sql, args)]

print('DB', db, db.stat().st_size)
print('--- settings ---')
for row in q("SELECT key, substr(value_json,1,500) v FROM sports_ai_settings WHERE key IN ('autonomous','engine','qualification_status')"):
    print(row['key'], row['v'])
print('--- schedules ---')
for row in q('SELECT name, job_kind, enabled, last_run_at FROM sports_ai_schedules ORDER BY name'):
    print(dict(row))
print('--- ticket status ---')
for row in q('SELECT status, COUNT(*) n FROM sports_ai_accumulator_tickets GROUP BY status ORDER BY n DESC'):
    print(dict(row))
print('--- coverage x status ---')
for row in q("""
    SELECT COALESCE(json_extract(parameters_json,'$.coverage'), 'standard') kind,
           COALESCE(json_extract(parameters_json,'$.tier_mode'), 'standard') mode,
           status, COUNT(*) n
    FROM sports_ai_accumulator_tickets GROUP BY 1,2,3 ORDER BY n DESC LIMIT 40
"""):
    print(dict(row))
print('--- codes ---')
for row in q('SELECT status, verification_method, COUNT(*) n FROM sports_ai_booking_codes GROUP BY 1,2'):
    print(dict(row))
print('--- job status ---')
for row in q('SELECT status, reason_code, COUNT(*) n FROM sports_ai_booking_jobs GROUP BY 1,2'):
    print(dict(row))
print('--- latest jobs ---')
for row in q('SELECT status, reason_code, substr(COALESCE(error,\'\'),1,300) err, updated_at FROM sports_ai_booking_jobs ORDER BY updated_at DESC LIMIT 10'):
    print(dict(row))
print('--- diagnostics ---')
for row in q('SELECT reason_code, COUNT(*) n, MAX(created_at) latest FROM sports_ai_booking_diagnostics GROUP BY reason_code ORDER BY latest DESC'):
    print(dict(row))
print('--- latest logs ---')
for row in q('SELECT phase, level, substr(message,1,220) msg, created_at FROM sports_ai_booking_logs ORDER BY created_at DESC LIMIT 15'):
    print(dict(row))
print('--- decisions ---')
for row in q('SELECT state, COUNT(*) n FROM sports_ai_qualification_decisions GROUP BY state'):
    print(dict(row))
print('booking product', q("SELECT COUNT(*) n FROM sports_ai_qualification_decisions WHERE state='QUALIFIED' AND EXISTS (SELECT 1 FROM json_each(products_json) WHERE value='booking')"))
print('qualified tickets', q("SELECT COUNT(*) n FROM sports_ai_accumulator_tickets WHERE status='qualified'"))
print('heartbeat', q("SELECT heartbeat_at FROM sports_ai_worker_leases WHERE id='scheduler-leader'"))
