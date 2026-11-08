import { DatabaseSync } from 'node:sqlite'
const db = new DatabaseSync('backend/sports_demo.sqlite3', { readOnly: true })
console.log('tickets by odds')
for (const row of db.prepare(`
  SELECT
    CASE
      WHEN combined_odds < 2 THEN '<2'
      WHEN combined_odds < 3 THEN '2-3'
      WHEN combined_odds < 5 THEN '3-5'
      WHEN combined_odds < 10 THEN '5-10'
      ELSE '10+'
    END AS band,
    status,
    COUNT(*) n
  FROM sports_ai_accumulator_tickets
  GROUP BY band, status
  ORDER BY band, status
`).all()) console.log(JSON.stringify(row))

console.log('coded vs not among qualified odds>=3')
console.log(db.prepare(`
  SELECT
    SUM(CASE WHEN b.code IS NULL THEN 1 ELSE 0 END) AS uncoded,
    SUM(CASE WHEN b.code IS NOT NULL THEN 1 ELSE 0 END) AS coded
  FROM sports_ai_accumulator_tickets t
  LEFT JOIN sports_ai_booking_codes b ON b.accumulator_ticket_id=t.id AND b.verification_method='displayed' AND TRIM(COALESCE(b.code,''))<>''
  WHERE t.combined_odds>=3 AND t.status='qualified'
`).get())

console.log('recent jobs')
for (const row of db.prepare(`
  SELECT status, reason_code, substr(error,1,180) error, COUNT(*) n
  FROM sports_ai_booking_jobs
  GROUP BY status, reason_code, substr(error,1,180)
  ORDER BY n DESC LIMIT 20
`).all()) console.log(JSON.stringify(row))

console.log('high odds tickets')
for (const row of db.prepare(`
  SELECT category_id, registration_state, COUNT(*) n
  FROM sports_ai_high_odds_tickets
  GROUP BY category_id, registration_state
`).all()) console.log(JSON.stringify(row))

console.log('latest job errors')
for (const row of db.prepare(`
  SELECT status, reason_code, substr(error,1,220) error, updated_at
  FROM sports_ai_booking_jobs
  ORDER BY updated_at DESC LIMIT 8
`).all()) console.log(JSON.stringify(row))
