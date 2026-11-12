# Single-engine loading and submission investigation ? 12 November 2026

## Findings

Before the repair, GET /api/sports-ai/single-betting/engine took 7.26 seconds in an HTTP measurement. A direct profile took 5.24 seconds, including 4.36 seconds recounting the entire odds-history table for the shared pipeline banner. The portal additionally requested the full dashboard, fixture list, predictions and tickets on the single-engine route, even though that page has its own live endpoint.

The latest recorded accepted real-money submission was 12 November at 04:53 WAT. Earlier failures included an ambiguous or missing selection control, database contention, and a missing saved SportyBet session. One earlier unclear order was explicitly resolved by the operator from Bet History. Those historical failures were not replayed.

At investigation time, the account was connected, the worker heartbeat was current, the execution queue was empty, and there were zero QUALIFIED decisions. Relevant prices were outside the configured freshness window. There were no recent submission attempts to fail: the engine had no eligible queued selection.

The configured single odds range remains 1.60?1.99, with the existing qualification, probability and EV rules unchanged. The football sport-level market anchor assigns zero model weight where its fallback applies. At those prices, a market-only probability is below the favourites lane's 70% floor, so these fallback selections cannot become favourites in the configured single odds band either. This is an evidence/rules restriction, not a justification to invent a probability or force a wager.

## Repairs

- The engine endpoint now reads persisted pipeline snapshots and bounded upcoming-fixture price lookups using the existing fixture/time index. It no longer recounts all stored odds on each poll.
- Stale pipeline stages are labelled as waiting rather than completed. Actual price freshness is separate from the timestamp of an eligible candidate.
- Single-engine and single-dashboard routes no longer make unrelated global-dashboard requests. Route changes restart the appropriate polling loop.
- The page shows a waiting/blocked explanation, latest relevant quote age and last accepted-bet timestamp. An old placed bet is no longer displayed as the current queued selection.
- Price collection has an independent, leader-gated worker lane. It checks the existing collection schedule each minute, so a long training or maintenance pass does not hold the next price collection.
- Overlapping collectors within the worker process yield instead of running duplicate collections.
- Collector pages are now streamed and published as received, rather than buffering an entire sport before saving its quotes. Existing source-permission and price/fixture checks remain in place.
- The frontend was rebuilt and the verified local services were reloaded with no paid submission in progress.

The operator explicitly confirmed during this investigation that the weekly prize ladder should remain enabled. Existing newer ladder/category work was preserved. Real-money authorization, limits, stake, qualification thresholds and the one-submission rule were not loosened. No manual paid test bet was submitted.

## Verification

- Three live engine requests completed in 0.395, 0.192 and 0.196 seconds, versus the earlier 7.26-second request.
- The browser displayed the repaired engine state in 2.073 seconds and made no global dashboard/fixture/prediction/ticket requests on this route.
- 35 targeted tests passed, covering single-bet rules, bounded engine reads, stale snapshot handling, leader-only price collection, collector overlap protection, streaming quote delivery, feed persistence and retired jackpot guards.
- npm run build passed with the existing bundle-size advisory.
- Final live status showed fresh relevant prices (about 3.5 minutes old), a connected account, a current worker heartbeat, zero queued selections and zero QUALIFIED decisions.

## Remaining condition

Freshness and loading improvements do not create a qualifying opportunity. Betting remains in WAITING until a selection passes the configured odds, model, market, EV and integrity rules. A new accepted wager has not been demonstrated by this repair.

Evidence: single-engine-response-times.json, single-engine-browser-check.json, single-engine-final-status.json, single-engine-repair-tests.log and single-engine-repaired-desktop.png.
