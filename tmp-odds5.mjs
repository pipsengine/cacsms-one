import { DatabaseSync } from 'node:sqlite'
const db = new DatabaseSync('backend/sports_demo.sqlite3', { readOnly: true })
console.log('recent odds>=3 tickets')
for (const row of db.prepare(`
  SELECT status, leg_count, ROUND(combined_odds,2) odds, ROUND(joint_probability,3) joint,
         json_extract(parameters_json,'$.tier_label') label,
         json_extract(parameters_json,'$.coverage') coverage,
         json_extract(parameters_json,'$.product') product,
         substr(created_at,1,16) created
  FROM sports_ai_accumulator_tickets
  WHERE combined_odds>=3 AND created_at>='2026-11-06'
  ORDER BY created_at DESC LIMIT 20
`).all()) console.log(JSON.stringify(row))
console.log('jobs for odds>=3')
console.log(db.prepare(`
  SELECT COUNT(*) n FROM sports_ai_booking_jobs j
  JOIN sports_ai_booking_job_items i ON i.job_id=j.id
  JOIN sports_ai_accumulator_tickets t ON t.id=i.ticket_id
  WHERE t.combined_odds>=3
`).get())
console.log('80pct upcoming distinct fixtures by odds')
const now = new Date().toISOString()
for (const row of db.prepare(`
  WITH latest AS (
    SELECT fixture_id, market, outcome, decimal_odds,
           ROW_NUMBER() OVER (PARTITION BY fixture_id, market, outcome ORDER BY observed_at DESC) rn
    FROM sports_ai_odds_snapshots WHERE bookmaker='SportyBet'
  )
  SELECT f.sport,
    CASE WHEN q.decimal_odds<3 THEN '<3' WHEN q.decimal_odds<4 THEN '3-4' WHEN q.decimal_odds<=5 THEN '4-5' ELSE '>5' END band,
    COUNT(DISTINCT f.id) fixtures
  FROM sports_ai_engine_predictions p
  JOIN sports_ai_fixtures f ON f.id=p.fixture_id
  JOIN latest q ON q.fixture_id=f.id AND q.rn=1 AND q.market=p.market AND q.outcome=p.outcome
  WHERE p.probability>=0.80 AND f.status='scheduled' AND f.starts_at>? AND f.sport IN ('football','basketball')
  GROUP BY f.sport, band
`).all(now)) console.log(JSON.stringify(row))
