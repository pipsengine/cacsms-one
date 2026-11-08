import { DatabaseSync } from 'node:sqlite'
const db = new DatabaseSync('backend/sports_demo.sqlite3', { readOnly: true })
const now = new Date().toISOString()
console.log('now', now)
const rows = db.prepare(`
  SELECT f.sport,
    CASE
      WHEN q.decimal_odds < 2 THEN '<2'
      WHEN q.decimal_odds < 3 THEN '2-3'
      WHEN q.decimal_odds < 4 THEN '3-4'
      WHEN q.decimal_odds <= 5 THEN '4-5'
      ELSE '>5'
    END band,
    COUNT(*) n
  FROM sports_ai_engine_predictions p
  JOIN sports_ai_fixtures f ON f.id=p.fixture_id
  JOIN sports_ai_odds_snapshots q ON q.fixture_id=f.id AND q.bookmaker='SportyBet'
  WHERE p.probability >= 0.80
    AND f.status='scheduled'
    AND f.starts_at > ?
    AND f.sport IN ('football','basketball')
  GROUP BY f.sport, band
  ORDER BY f.sport, band
`).all(now)
console.log(rows)
console.log('qualified odds>=3')
console.log(db.prepare(`
  SELECT COUNT(*) n FROM sports_ai_qualification_decisions d
  JOIN sports_ai_fixtures f ON f.id=d.fixture_id
  JOIN sports_ai_value_opportunities o ON o.id=d.opportunity_id
  JOIN sports_ai_odds_snapshots q ON q.id=o.odds_id
  WHERE d.state='QUALIFIED' AND q.decimal_odds>=3 AND f.starts_at>?
`).get(now))
console.log('diagnostics for odds>=3 tickets')
for (const row of db.prepare(`
  SELECT d.reason_code, COUNT(*) n
  FROM sports_ai_booking_diagnostics d
  JOIN sports_ai_accumulator_tickets t ON t.id=d.ticket_id
  WHERE t.combined_odds>=3
  GROUP BY d.reason_code ORDER BY n DESC LIMIT 12
`).all()) console.log(JSON.stringify(row))
