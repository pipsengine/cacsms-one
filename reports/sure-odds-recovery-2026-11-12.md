# Sure Odds recovery ? 12 November 2026

## Changes applied

- Expire price-refresh requests for fixtures that have started or are no longer scheduled. The live recovery retired 337 obsolete requests.
- Missing or unrelated market prices no longer occupy the entire refresh batch. These requests yield for two minutes; enqueueing preserves their retry time.
- Prefer the active model when linking quotes. Forecast selection for qualification now keeps the latest forecast per model, so a newer inactive forecast cannot hide the active model's forecast.
- Restrict the Sure Odds candidate reader to active models.
- Add authenticated GET /api/sports-ai/sure-odds and poll it independently every 15 seconds. This avoids waiting for the large dashboard history request. Manual Refresh also refreshes this card.
- Explain missing tickets using today's upcoming football/basketball decision blockers, with one latest decision per fixture and market, plus last source collection and last ticket timestamps. Loading and refresh failures are distinguished from a confirmed empty result.
- Rebuild the tracked frontend distribution and restart the verified local API and worker through the existing Vite supervisor.

## Live recovery and remaining blockers

A controlled pass checked 120 price-refresh requests, linked current stored quotes for 19 fixtures and left 101 waiting for matching prices. Recovering the interrupted collection imported 13 additional observations; 287 were skipped by the existing acceptance checks.

Qualification reevaluation checked 788 decisions and changed 379. Its resulting counts were 0 QUALIFIED, 4 MONITORING, 6 REJECTED, 262 AWAITING_DATA, 186 EXPIRED and 330 SUSPENDED. These are decision counts, not matches. Eligibility gates and model weights were not relaxed.

The basketball audit reviewed 3,581 historical forecasts with no new threshold breaches. The frozen validation candidate remains RESEARCH with only 84 usable observations across leagues. Approval requires sufficient chronological train/calibration/holdout evidence for each league and market, not merely a large aggregate forecast count. Full evidence is in sure-odds-basketball-validation-2026-11-12.json.

Football's existing model-performance restrictions, missing markets and stale prices still prevent tickets. Where the learned football sport fallback assigns zero model weight, positive edge cannot be produced from that fallback. No model was promoted solely to populate the card. Forward validation and basketball release requirements still need additional real evidence.

Repeated SQLite write-lock errors initially prevented recovery. Restarting the local API initially cleared the writer contention; subsequent queue repair, import and audit completed. A second broad live collection later failed again at accept_displayed_quotes with sqlite3.OperationalError: database is locked. Further diagnosis showed a write lock could remain after restarting the API, implicating background-worker transactions too. Broad collection reliability remains unresolved; the successful smaller recovery should not be interpreted as a complete repair of database contention. No active SUBMITTING order was recorded when services were restarted.

## Validation

- 36 pytest tests passed across pipeline, recovery, qualification and feed suites, including regressions for old requests starving upcoming matches, retry fairness, unrelated prices and inactive forecast selection.
- 10 command-view tests and 11 high-odds board tests passed separately: 57 targeted tests total.
- npm run build passed. The existing bundle-size warning remains.
- The authenticated local Sure Odds endpoint returned the expected date, zero tickets and detailed blockers in 0.57 seconds.
- The live browser loaded the independent explanatory card in 7.13 seconds with no horizontal overflow; desktop screenshot: sure-odds-recovery-desktop.png.

The card can still legitimately contain zero tickets. The repairs restore refresh processing and make that result explicit; they do not fabricate or force qualified selections.

A final focused run also passed 22 tests covering the active-forecast regression, command view and high-odds board. The active-forecast regression now verifies both candidate selection and targeted qualification retain the active forecast when an inactive forecast is newer.
