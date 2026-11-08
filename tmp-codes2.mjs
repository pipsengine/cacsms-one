import { DatabaseSync } from 'node:sqlite'
const db = new DatabaseSync('backend/sports_demo.sqlite3', { readOnly: true })
const jobs = db.prepare(`
  SELECT id, status, reason_code, substr(error,1,160) error, updated_at, ticket_id, substr(selections_json,1,400) selections
  FROM sports_ai_booking_jobs
  ORDER BY updated_at DESC LIMIT 6
`).all()
for (const job of jobs) {
  let odds = null
  let product = null
  if (job.ticket_id) {
    const ticket = db.prepare(`SELECT combined_odds, joint_probability, leg_count, status, substr(parameters_json,1,240) params FROM sports_ai_accumulator_tickets WHERE id=?`).get(job.ticket_id)
    odds = ticket
    product = ticket && ticket.params
  }
  console.log(JSON.stringify({ status: job.status, error: job.error, updated_at: job.updated_at, ticket: odds }))
}
console.log('--- sure/high tickets today')
for (const row of db.prepare(`
  SELECT json_extract(parameters_json,'$.product') product,
         json_extract(parameters_json,'$.band') band,
         status, COUNT(*) n,
         ROUND(MIN(combined_odds),2) min_odds,
         ROUND(MAX(combined_odds),2) max_odds
  FROM sports_ai_accumulator_tickets
  WHERE created_at >= '2026-11-07'
  GROUP BY product, band, status
`).all()) console.log(JSON.stringify(row))
