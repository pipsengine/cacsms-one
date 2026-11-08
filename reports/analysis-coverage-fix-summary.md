# Analysis coverage repairs — 8 November 2026

Implemented and applied the following repairs to the running installation:

- Sports and league selection now rotate fairly, complete first attempts before retrying earlier gaps, and persist a reason and retry time for every attempted fixture.
- Instant kickoffs and retry timestamps use chronological comparisons instead of mixed ISO/SQLite text ordering.
- Country, division, gender and youth distinctions are retained in league identity. Women's upcoming fixtures and historical results now use the same sport code. The existing data repair updated 73 fixtures.
- General history collection retains capacity while priority leagues need results. More result sports are covered, empty/error pages cannot permanently pin a cursor, tennis tournament CSS modifiers are parsed, and abbreviated tennis names are expanded only when the source event slug agrees. Esports results retain the source game's identity.
- New match-winner features and inference use each sport's own chronological results. Individual participants can use their history across competitions of that same sport. Unsupported totals/spreads are not inferred from these winner models; cricket requires an explicit sourced winner rather than innings totals.
- Basketball outcome labels now match the order of its home/away probabilities. Calibration candidates are selected before the final holdout. Production inference uses the family actually scored on that holdout.
- Dataset changes trigger model refreshes, including previously unvalidated models. Training preserves pre-kickoff information cutoffs, excludes future finals, uses at most 600 usable recent training fixtures and caches repeated source/name queries.
- Old models affected by the identity change are excluded from qualification and current prediction selection. Old feature snapshots are refreshed before use with the repaired pipeline.
- Ice-hockey final match-winner analysis is separated from regulation-result markets. Legacy quotes without that settlement distinction cannot become full-match winner prices.
- The worker checks the scheduler lease before starting any stage. Model training no longer holds the identity-repair write transaction open.
- Historical backfill is enabled for eight held leagues per day. Existing source permission and validation gates remain in effect.
- Prediction, fixture and research filters support the additional sports. The all-sports prediction page and Coverage & Health show model samples, validation reasons and league progress. Prediction totals also support status filters without SQL alias errors.
- Odds-refresh challenge detection covers sports pages beyond football. Winner booking selects the correct market and rejects an ambiguous three-way price list.

## Live outcome

There are model records for all 24 canonical sports currently represented by stored fixtures. Football and ice hockey passed validation and are active. Other models remain in research or validation review where the available evidence does not pass the existing gates. In particular:

- Basketball still has insufficient cutoff-safe historical training observations; newly collected old scores do not establish that they were known before earlier training kickoffs.
- The current women's-football candidate has adequate samples, but its selected family does not beat the baseline on the holdout. It remains inactive, with the reason displayed in Coverage & Health.
- Sports without enough observed finals or participant histories wait for collection and automatic refresh. No probabilities or passing validation results were fabricated to remove these blockers.

The live history refresh processed 17,607 result records, and the corrected tennis parser subsequently imported 360 tennis finals from a verified result page. These figures include refreshed existing records. The coverage page was checked with 630 upcoming league groups and a backfill budget of eight per day; these live counts change as collection runs.

## Verification

- Full suite: 274 tests run, one optional browser test skipped, no failures. A subsequent 17-test focused run passed, including the final API and esports parsing changes.
- The earlier accumulator-test failures were corrected by setting the fixture predictions' explicit simulation timestamp and using the current SportyBet quote contract. Production qualification gates were not relaxed.
- Production frontend build passed.
- Browser checks verified Coverage & Health and selected Tennis on All Sports Predictions. The prediction endpoint returned HTTP 200 with no internal-server-error banner or JavaScript page errors.
- The database migration created a timestamped SQLite backup before applying its additive schema changes. Services were reloaded after repair.

Detailed live progress is available in `reports/analysis-coverage-repair.json` and in Source Management Centre → Coverage & Health.
