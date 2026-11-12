# Sure Odds display and code-request repair ? 12 November 2026

## Cause

Five market-priced Sure Odds drafts were stored at 13:12 WAT. The stored-ticket reader required model qualification for every ticket, including these explicitly market-priced tickets. The daily cap already counted all five stored drafts, so the board showed no tickets and the booking routine could not rediscover the four uncoded ones. The previously displayed model-qualification explanation did not describe this failure.

One existing SportyBet code belonged to the fifth ticket after a dropped leg and displayed price changes reduced its total to 1.52. That total is below this panel's 2.00 minimum, so it remains excluded. Its historical record was retained.

## Changes

- Stored market-priced Sure Odds tickets use the same structural and price-band validation as their existing booking path. The model-qualification gate remains mandatory for model-priced tickets.
- The reader checks 2?3 selections, combined odds 2.00?2.95, the market probability floor, actual fixture status and kickoff, and existing bookmaker provenance. Started or out-of-band tickets remain excluded.
- Stored market tickets remain discoverable when the daily creation cap has been reached. No daily limit was raised and no additional draft was created by this repair.
- Market-priced probabilities are labelled as such. AI confidence remains unavailable, rather than being invented. Ticket prices are labelled as stored prices.
- Stable ticket IDs are returned and used as React keys, so several tickets with the same odds band and selection count render independently.
- Before requesting a Sure Odds code, the browser checks the actual offered selections and displayed total. It stops if the remaining 2?3 selections total outside 2.00?2.95.
- The panel now carries the actual booking failure reason instead of an indefinite generic awaiting-code message.

## Live code checks

The four previously hidden tickets are now visible. Code checks found actual totals of 1.54, 1.98, 1.69 and 1.99 after unavailable selections and/or changed prices. Every total is below 2.00. All four requests were stopped before requesting a code; no new valid code was accepted. The individual reasons are displayed beside the drafts. A code can only be accepted when a live slip actually meets the product's band; a stored 2.00?2.01 draft alone is not proof that SportyBet currently offers that total.

These were booking-code operations with places_wager=false. No manual paid test bet was submitted. Weekly prize-ladder settings and other concurrent repository changes were preserved.

## Validation

38 targeted tests passed across stored Sure Odds, command view, high-odds board and booking tests. A final 15-test view regression run also passed after stable IDs were added. The production build passed. Live browser and API verification confirm the restored rows and explicit failed-code explanations. Evidence is in sure-odds-display-final-status.json, sure-odds-final-tests.log and sure-odds-restored-panel.png.
