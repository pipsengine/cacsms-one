-- Rebuild the category constraint while retaining existing ticket/run references.
CREATE TABLE sports_ai_high_odds_categories_next (
 id TEXT PRIMARY KEY,
 label TEXT NOT NULL,
 min_odds REAL NOT NULL CHECK(min_odds>=2),
 max_odds REAL NOT NULL CHECK(max_odds>min_odds),
 priority INTEGER NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
 max_active INTEGER NOT NULL DEFAULT 3 CHECK(max_active BETWEEN 1 AND 10),
 CHECK((id='jackpot' AND min_odds=2000000 AND max_odds=2200000)
    OR (id<>'jackpot' AND max_odds<=1000))
);
INSERT INTO sports_ai_high_odds_categories_next SELECT * FROM sports_ai_high_odds_categories;
DROP TABLE sports_ai_high_odds_categories;
ALTER TABLE sports_ai_high_odds_categories_next RENAME TO sports_ai_high_odds_categories;
INSERT INTO sports_ai_high_odds_categories(id,label,min_odds,max_odds,priority,enabled,max_active)
 VALUES('jackpot','Jackpot (NGN200 Million)',2000000,2200000,90,1,5);
