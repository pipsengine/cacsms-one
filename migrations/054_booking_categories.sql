UPDATE sports_ai_high_odds_categories
SET enabled=0
WHERE id<>'jackpot';

INSERT INTO sports_ai_high_odds_categories
    (id,label,min_odds,max_odds,priority,enabled,max_active)
VALUES
    ('100-200','100.00–200.00',100,200,10,1,5),
    ('201-500','201.00–500.00',201,500,20,1,5),
    ('501-1000','501.00–1,000.00',501,1000,30,1,5);
