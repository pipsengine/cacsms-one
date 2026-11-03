UPDATE sports_ai_schedules
SET enabled=1, interval_minutes=15, cron_expression=NULL
WHERE id='schedule-prediction';

UPDATE sports_ai_schedules
SET enabled=1
WHERE id IN ('schedule-accumulator','schedule-settlement','schedule-model-evaluation');
