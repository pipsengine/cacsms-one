import { DatabaseSync } from 'node:sqlite'
const db = new DatabaseSync('backend/sports_demo.sqlite3', { readOnly: true })
console.log('qualified tickets')
for (const row of db.prepare(`
  SELECT id, status, combined_odds, joint_probability, leg_count, created_at,
         json_extract(parameters_json,'$.product') product,
         json_extract(parameters_json,'$.tier_label') label
  FROM sports_ai_accumulator_tickets
  WHERE status IN ('qualified','unverified_probability','review_required','awaiting_odds')
  ORDER BY created_at DESC LIMIT 25
`).all()) console.log(JSON.stringify(row))
console.log('codes by ticket odds')
for (const row of db.prepare(`
  SELECT
    CASE WHEN t.combined_odds>=3 THEN '>=3' ELSE '<3' END band,
    COUNT(*) n
  FROM sports_ai_booking_codes b
  JOIN sports_ai_accumulator_tickets t ON t.id=b.accumulator_ticket_id
  WHERE b.verification_method='displayed' AND TRIM(COALESCE(b.code,''))<>''
  GROUP BY band
`).all()) console.log(JSON.stringify(row))
