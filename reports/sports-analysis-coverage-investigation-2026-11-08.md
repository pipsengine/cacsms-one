# Sports and league analysis coverage investigation

Inspected the running SQLite database and scheduler, collection, modelling and analysis code on 8 November 2026, approximately 02:00 WAT. Counts are a snapshot of a database that remains live. No analysis settings, models or source permissions were changed during this investigation.

## Observed coverage

Fixtures are stored for 25 sports. Engine predictions are stored for only two sports:

| Sport | Stored fixtures | Stored final results | Fixtures with predictions | Leagues with predictions |
|---|---:|---:|---:|---:|
| Football | 15,396 | 11,330 | 871 | 26 |
| Basketball | 3,943 | 2,373 | 154 | 2 |
| Ice hockey | 2,338 | 1,669 | 0 | 0 |
| Women's football (`frauenfussball`) | 590 | 590 | 0 | 0 |
| Tennis | 238 | 0 | 0 | 0 |
| Volleyball | 177 | 0 | 0 | 0 |
| Table tennis | 221 | 0 | 0 | 0 |

These are all stored fixtures, including historical and future fixtures, rather than a claim that historical games should receive forecasts. Match reports, engine predictions and qualified booking selections are separate outputs.

## Confirmed causes

### 1. Most collected sports are excluded from inference

`backend/sport_support.py:12` defines `ANALYSED_SPORTS` as football, women's football, ice hockey, efootball, virtual football and basketball. Tennis, table tennis, volleyball and MMA are recognized separately but excluded from that inference list. Other collected sports, including cricket, baseball and American football, are also excluded.

`quoted_coverage_fixtures()` and `run_analysis()` in `backend/prediction_engine.py` use this list. `backend/sport_adapters.py` returns no probabilities; its presence does not implement a trained model. Adding names to the list alone would not supply the missing sport-specific feature and inference implementations.

### 2. Models are stale or absent, and new results do not automatically retrain them

The database contains four model entries, all created on 4 November:

- Football: active.
- Women's football: active, but no upcoming fixtures are stored under its sport code.
- Basketball: unvalidated and inactive, with zero scored training fixtures and zero holdout fixtures in its stored metrics.
- Ice hockey: unvalidated and inactive, with one scored fixture and one holdout fixture in its stored metrics.

The database now contains 2,373 basketball finals and 1,669 ice-hockey finals. Those raw totals do not establish sufficient cutoff-safe training samples, but the old metrics have not been refreshed to assess the new data.

`ensure_goal_sport_models()` skips a sport whenever any model already exists. `run_analysis()` creates an initial football/basketball model only when none exists. Scheduled `retrain_evaluation` explicitly skips retraining in `backend/operations_engine.py:506`. Governance evaluates existing candidates rather than training replacements. Ice hockey requires an active model to enter inference; basketball can produce research predictions from its inactive candidate.

### 3. History collection can permanently starve the other sports

In `backend/history_browser.py:439`, `plan_visits()` chooses either the priority crawl or the general crawl. Whenever any league is waiting for results, it chooses only football, basketball and ice hockey for BetExplorer, plus football pages on Soccerway.

The current general cursor remains at day 1, sport index 0, while the priority cursor has advanced to day 9. The next planned pass contains five football visits, two basketball visits and two ice-hockey visits; no tennis, volleyball, handball or baseball visits. The existing test `test_waiting_leagues_read_modelled_sports_before_the_general_walk` explicitly expects tennis to be absent.

Because result gaps persist, the general branch need not run. Some collected sports, including table tennis and MMA, are not in the general history list at all. This explains why fixture and odds collection can succeed while result collection and modelling remain empty.

### 4. League history and feature requirements leave substantial coverage gaps

The last stored result-coverage diagnostic lists 425 sport/competition names without an exact-name final result. This diagnostic is limited to `ANALYSED_SPORTS`; it is not a count across all collected sports. The coverage selector uses normalized league names, so its counts differ from the exact-name diagnostic.

Analysis requires league history and usable team histories. `MIN_TEAM_GAMES` is three. A result in a league does not ensure enough history for both teams. A read-only check of the next eight coverage candidates found that all eight returned no usable features. Recent completed runs explicitly report `insufficient_history` and `sport_not_modelled` with zero predictions.

The worker processes at most ten batches of eight coverage fixtures per cycle, and creates only two detailed match reports per cycle. These are throughput limits, not a promise that all leagues are processed each cycle. Fixtures without a usable model can be selected repeatedly because that skip does not receive the history-skip cooldown record.

### 5. Historical backfill is disabled by policy

The effective backfill policy is `per_day: 0`. There are 887 held backfill tasks and 12 completed tasks. Current-season collection has a separate narrow release path, so a zero historical cap does not mean every collection task is disabled. However, it prevents ordinary historical expansion needed to fill team-history gaps.

### 6. Sport and league identity are inconsistent

The live football feed stores women's competitions under `football`: 73 scheduled women's fixtures were found there. Historical women's results are stored under `frauenfussball`: 590 finals, with no scheduled fixtures under that code. Feature lookup matches sport exactly, separating those upcoming fixtures from that historical pool and from the active women's model.

The league-key function also collapses distinct competitions:

| Input | Current key |
|---|---|
| England Premier League | `premier-league` |
| Ghana Premier League | `premier-league` |
| Italy Serie A | `serie-a` |
| Italy Serie A, Women | `serie-a` |
| Portugal Liga Portugal | `primeira` |
| Portugal Liga Portugal 2 | `primeira` |

`competitions_for_league()` groups competition names using this key. This can falsely indicate historical coverage and mix league baselines while still failing to find the required team histories. Coverage reports therefore need canonical sport, country, competition, division and gender identity rather than substring-only names.

### 7. Timestamp comparisons make scheduling and retry eligibility inaccurate

Stored timestamps commonly use ISO `T` separators, while SQLite `datetime()` returns a space separator. `_not_recently_skipped_sql()` compares these values as text. At inspection, it treated 168 insufficient-history jobs as recent, while a chronological `julianday()` comparison found 159 within 45 minutes.

`upcoming_fixture_sql()` has the same text-comparison pattern for instant kickoffs, allowing elapsed fixtures from the current UTC date to remain eligible. The intended date-only-fixture allowance should be preserved while instant timestamps are compared chronologically.

## Recommended implementation order

1. Correct canonical sport/league identity and chronological comparisons; add regression checks for countries, divisions, women's fixtures and elapsed kickoffs.
2. Reserve history-crawl capacity for the general sports list on every pass and track progress per sport and league. Add supported result sources for collected sports absent from that list.
3. Fill the specific missing team histories, then train new basketball and ice-hockey candidates against cutoff-safe data. Evaluate validation gates before activation.
4. Implement dedicated feature, settlement, training and inference support for the remaining sports. Do not enable them by reusing an incompatible model or simply expanding the allowlist.
5. Introduce fair queues and explicit per-league reasons: unsupported model, missing results, insufficient team history, validation pending, queued, analyzed, or analyzed but unqualified.
6. Set a deliberate historical backfill budget and measure coverage against current fixtures, independently from booking qualification and ticket generation.

The scheduler was running with a current leader heartbeat during inspection. This is a coverage and implementation problem, rather than evidence that the whole analysis service has stopped.
