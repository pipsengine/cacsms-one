# Sure Odds empty-card investigation — 12 November 2026

Investigated the local application and backend/sports_demo.sqlite3 at approximately 03:10 WAT. Database access was read-only. No qualification thresholds, model states, booking settings or data were changed.

## Confirmed result

Calling high_odds_board.sure_odds_board against the database returned {"sure_tickets": []}; _candidates for today's WAT window returned zero. There are zero QUALIFIED decisions across the stored qualification table. The empty state therefore reflects the backend result.

The card requires at least two selections, a ready ticket, combined odds 2.00–2.95 and every leg still to play. The candidate path additionally requires an active validated model, an unexpired QUALIFIED decision allowing booking, a linked SportyBet quote, anchored probability at least 80%, and market holdout readiness. Relevant source: backend/high_odds_board.py (_rows, _candidates, sure_odds_board) and backend/command_view.py (_sure_tickets).

## Evidence

- Today's WAT fixture window contains 80 football and 75 basketball fixtures. Collection is not completely stopped.
- Latest acquisition runs completed at 02:50 and 02:51 WAT, saving 11,624 and 1,200 records respectively. Successful ingestion does not establish that every required quote is fresh.
- high_odds_worker heartbeat was 03:06:49 WAT and reported idle.
- Latest stored Sure Odds ticket was created 9 November at 16:51 WAT. This predates the reported onset; the database cannot establish exactly when the user first saw the empty card.
- For 11 November WAT, basketball had 604 suspended quarantine decisions; football had 586 fixture-started, 322 stale-odds and 12 negative-edge decisions. No qualified decisions remain for that date.
- For 12 November WAT, basketball had 317 suspended quarantine decisions. Football had 249 missing-market-price, 212 inactive-model, 209 stale-odds, 131 rejected-negative-edge and 104 uncalibrated-market decisions, plus smaller monitoring/expired groups. Counts are decisions across markets and model versions, not distinct fixtures.

## Underlying restrictions

Basketball requires a loss-audited candidate with passing league/market out-of-sample evidence. Active quarantine records explicitly say legacy models have no loss-audited release. An active model alone does not satisfy this restriction.

Football's learned sport-level market anchor currently assigns model weight 0.0, margin 1.0443. Its 274-match holdout Brier score was 0.1841 for the model versus 0.1493 for the market (lower is better). For leagues falling back to this sport setting, the anchored probability is the margin-adjusted bookmaker probability, so expected edge at that price is approximately -4.24%. Those selections cannot meet a positive-edge gate. Some leagues have their own overrides; this is not a claim that every league has zero model weight.

Stored evidence also identifies capabilities at RESEARCH level because forward validation has not met the configured promotion requirements, including 500 settled forward predictions and 100 closing-line observations. Refreshing prices alone cannot resolve model validation and capability restrictions.

## Separate operational issue

backend/logs/api-errors.log contains SQLite database-is-locked errors on SportyBet account connection requests, most recently 12 November 02:56 WAT. This needs separate attention for account connectivity, but does not explain away the independently reproduced zero-candidate result.

## Recommended resolution

1. Repair quote freshness and missing-market coverage, then reevaluate affected predictions against current active models.
2. Complete basketball loss-audit release and forward-validation requirements before those selections can qualify.
3. Review football model performance and league-specific validation. Do not increase model trust merely to populate the card.
4. Improve the card's empty-state explanation to report qualification blockers and last successful ticket/collection times.

No code fix was applied: the reproduced empty card is consistent with the current eligibility policy, and populating it would require resolving data/model evidence or explicitly changing product requirements.
