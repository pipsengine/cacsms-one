# Cacsms Sport AI — End-to-End Research & Prediction Audit

**Date:** 2026-11-14 (evidence captured 05:30–06:00 UTC)  **Commit:** `0dfbb2a` (main, clean)
**Method:** Read-only. Source inspection, plus SQL against the live DB opened with `mode=ro` + `PRAGMA query_only=1`, plus existing logs and evidence screenshots. No code, config, DB rows, or workflows were changed. No collection, booking, or betting was triggered. The test suite was **not run** because of the known risk that tests touch the live session/DB.

Labels used: **[FACT]** verified from code, DB or artifacts · **[INFERENCE]** reasoned from facts · **[SUSPECTED]** plausible, not proven · **NOT VERIFIED**.

---

## 1. Executive summary

**Verdict: No.** Cacsms Sport AI does **not** perform comprehensive, autonomous, evidence-driven match research before it predicts and selects markets.

What it actually does today:

1. **It scrapes final scores, not match research.** Playwright reads BetExplorer/Soccerway results tables and stores scores only. Injuries, lineups, xG, pace, first-half data, tactics and player availability are **0% populated** across all 4,204 feature snapshots. Standings exist for one German table only, and that table is copied under 7 different competitions.
2. **Its own trained models have failed in prospective use.** From Nov 3–9 the tickets were built from model probabilities. Legs claimed a 52.1% win chance and won **37.0%**; claimed edge was +33%. Ticket hypothetical P&L is **−32.5%**. The tennis model has *less* skill than a coin flip. The football model is worse than SportyBet's own price (holdout Brier 0.169 vs 0.142).
3. **Since Nov 11 it has quietly switched to SportyBet's own odds as "intelligence".** 100% of ticket legs from Nov 13–14 carry `model_id='market-price'`, `model_version='sportybet-fair-price'`. Their "probability" is SportyBet's margin-free price multiplied by an in-sample "history factor", capped at ±15%. Average stored edge on those legs is **−6.3%**.
4. **It is betting real money on that basis.** `execution_mode = LIVE` is active with a live authorization (₦100 stakes, up to 10/day). 41 real submissions have been made: 24 settled, 13 won and 11 lost, ₦2,400 staked for ₦2,350.60 returned. The stored EV of the bets actually placed averages **negative**. The code labels the favourites product *"Favourite (no edge) – expected to lose money over time."*
5. **The hard integrity gate does not apply to what it books.** Market-priced tickets bypass `prediction_integrity.require_bookable` and `require_current_qualification`. The qualification engine reports **0 QUALIFIED** selections and the capability gate reports **0 ELIGIBLE**, yet 45 tickets are labelled `qualified`, booking codes are generated, and real bets are placed.
6. **There is no computer vision.** Screenshots are saved as evidence and never interpreted. All extraction is DOM/HTML text parsing. No LLM or vision model is present (requirements: fastapi, uvicorn, pydantic, playwright, cryptography).

What genuinely works, with execution evidence: SportyBet price collection (fresh to 05:49 UTC), booking-code generation (real codes with screenshots, e.g. `TWWQU6`), real-money placement automation, the leader-leased scheduler, results scraping at volume (68k BetExplorer + 15k Soccerway results), and leakage-safe feature cutoffs (1 violation in 4,204).

**Operational readiness:** the *automation plumbing* is substantially built. The *intelligence layer* is not fit for purpose, and real money is being staked on bookmaker-derived probabilities with no demonstrated edge.

---

## 2. Capability assessment matrix

| # | Capability | Status | Key evidence |
|---|---|---|---|
| 1 | Fixture discovery | **IMPLEMENTED** | 119k fixtures; SportyBet-displayed fixtures through Dec 2026; schedules running (E-OPS-01) |
| 2 | SportyBet odds acquisition | **IMPLEMENTED** | 5.6M snapshots, latest 05:49:19 UTC; 7.9M observations |
| 3 | Final-score results (BetExplorer/Soccerway) | **IMPLEMENTED** (accuracy NOT VERIFIED) | 68,478 + 15,092 results; 0/822 identity links resolved (E-SRC-05) |
| 4 | League standings | **FAILED** | 162 rows, one source table copied under 7 competitions, stale since 11-09 (E-RES-03) |
| 5 | H2H | **PARTIALLY IMPLEMENTED** | Derived from stored scores; populated in 48% of football and 0% of women's football snapshots |
| 6 | Recent form / home-away / rest / congestion | **PARTIALLY IMPLEMENTED** | Score-derived only; contaminated by misdated results (E-RES-02) |
| 7 | Injuries / lineups / player availability | **MISSING** | 0 non-null values across 4,204 snapshots (E-RES-01) |
| 8 | xG / pace / tactical / first-half / sport-specific stats | **MISSING** | `sports_ai_match_statistics` has 0 rows; xG, pace and H1 are 0% |
| 9 | Computer vision page interpretation | **MISSING** | No CV/OCR/LLM code or deps; screenshots are evidence only (E-CV-01) |
| 10 | Generic browser research operator | **NOT VERIFIED / unused** | `browser_sessions/pages/observations`, `extraction_configs` all have 0 rows |
| 11 | Autonomous scheduling | **IMPLEMENTED** | Leader lease heartbeat 05:36:53; 17 schedules with recent `last_run_at` |
| 12 | Prediction models (football/basketball/etc.) | **FAILED** (prospectively) | See §6; tennis skill below 50/50; football worse than market |
| 13 | Probability calibration | **FAILED** | Ticket claimed vs actual: 74.8%→28.3%, 36.1%→17.5% (E-MOD-04) |
| 14 | Market evaluation / EV | **PARTIALLY IMPLEMENTED** | Machinery exists; inputs are the bookmaker's own price (E-MKT-01) |
| 15 | Qualification gate | **IMPLEMENTED but bypassed** | 0 QUALIFIED / 0 ELIGIBLE, yet bookings proceed via the market-price path (E-MKT-02) |
| 16 | Ticket generation | **IMPLEMENTED** (quality FAILED) | 2,213 tickets; claimed EVs up to 492,055× (E-MKT-03) |
| 17 | Booking codes | **IMPLEMENTED** | 239 CODE_GENERATED with screenshots; 135 FAILED (E-BK-01) |
| 18 | Real-money placement | **IMPLEMENTED** (contradicts README) | 41 live submissions; LIVE mode active (E-BET-01) |
| 19 | Settlement | **PARTIALLY IMPLEMENTED** | Only ~22k of 330k predictions graded; 250 past predicted fixtures have no result |
| 20 | Learning / retraining | **PARTIALLY IMPLEMENTED** | 68 model versions in 11 days; 171 drift events with no action; promotion does not require beating the market |
| 21 | Cross-source verification | **MISSING** | 98,593 of 105,881 reconciliations are `single_source`; identity links all unresolved |
| 22 | Kill switch / revoke reliability | **PARTIALLY IMPLEMENTED** | Revoke failed with `database is locked` at 03:49 UTC today (E-OPS-03) |

---

## 3. Findings register

### S1 — Critical

**F-01 · Real-money betting on probabilities with no demonstrated edge** · Evidence E-BET-01, E-MKT-01
- **Code:** `backend/single_betting.py:52-65` (favourites and Sure Odds real-money products; L57 `FAVOURITE_NOTICE = 'Favourite (no edge) - expected to lose money over time.'`); `backend/sportybet_account.py:813-856` (LIVE mode).
- **DB:** `sports_ai_settings.single_betting_runtime` = `{"execution_mode":"LIVE"}` (set 03:50:56 UTC 11-14). Authorization `fd7dd7da…` is active until 11-21 (₦100 × 10/day). `sports_ai_single_live_orders`: 8 PLACED, 13 SETTLED_WON, 11 SETTLED_LOST, 17 FAILED, 3 REJECTED, 2 PENDING. Settled total: ₦2,400 staked, ₦2,350.60 returned. Latest submission was 05:37:55 UTC with screenshot `account_evidence/b87f4e48….png`. Balance is ₦1,282.26.
- **DB:** `sports_ai_single_bets` WON/LOST rows show average stored EV of −0.058 (FAVOURITE) and −0.169 (TICKET).
- **Expected:** stake only on selections with validated, conservative positive EV. **Actual:** stakes are placed on market-priced favourites and tickets whose own stored EV is negative.
- **Also:** the README states "No wager is placed" and "the worker never places wagers". That is false. `single_betting_status` reports `"mode":"PAPER","places_wager":false` while runtime is LIVE, so the status reporting is inconsistent.
- **Confidence:** High. **Impact:** direct financial loss expectation. **Root cause:** after the trained models failed, the product was re-based on bookmaker prices, but real-money automation was left on.

**F-02 · The integrity and qualification gate is bypassed for market-priced bookings** · Evidence E-MKT-02
- **Code:** `backend/sportybet_booking.py:227-245`. When `_market_priced(...)` is true, `guarded = []`, so `require_current_qualification` is skipped, and `jackpot_low.require_bookable` replaces `prediction_integrity.require_bookable`.
- **DB:** `qualification_status.counts.QUALIFIED = 0`; `capability_refresh.eligible = 0`; `single_betting_status.rejected.NO_QUALIFIED_SELECTION = 2659`. Yet `sports_ai_accumulator_tickets` has 45 rows with status `qualified`, and 239 booking jobs are CODE_GENERATED.
- **Expected (README):** *"create_job applies this gate to every booking product; booking_policy relaxations cannot switch it off."* **Actual:** the gate applies only to model-priced tickets, which are now about 0% of output.
- **Confidence:** High. **Impact:** "qualified" is no longer meaningful, and the integrity system protects nothing currently booked.

**F-03 · Historical results misdated into the present and future (data corruption)** · Evidence E-RES-02
- **Code:** `backend/acquisition_engine.py:350-361`, `_football_txt_fixtures`. `year += 1` fires whenever a month is earlier than the previous month. UEFA Football.TXT files list fixtures group by group (Sep→Dec, then Sep again), so each group increments the year.
- **DB:** 295 OpenFootball `final` results on fixtures dated after now, up to 2030-12-14. Example: *Villarreal 0–2 Manchester United*, competition "UEFA Champions League 2021/22", stored as 2026-11-23. Across UCL/UEL/UECL 2021/22–2023/24, about 435 results are dated 2026-09 to 2030. Those in Sep–Nov 2026 fall inside current form windows.
- **Confidence:** High (cause confirmed by code plus data pattern). **Impact:** contaminates team ratings, form and H2H for European clubs. Results for "future" fixtures could also mis-settle or pollute training. *[SUSPECTED]* they also inflate the football holdout. **Secondary bug:** `acquisition_engine.py:454` references an undefined `parsed` (NameError when a payload has no `name`).

**F-04 · The trained models are uncalibrated and lose prospectively** · Evidence E-MOD-01…04
- **Leg level (DB `sports_ai_accumulator_legs`):** trained-model legs claimed p=0.521 and won 37.0% (1,538 / 4,156). Average claimed edge was +0.333.
- **Ticket level (`sports_ai_ticket_settlements`):** 1,763 settled, hypothetical profit −573.3 on 1,763 units (−32.5%). In every probability bin, actual results came in below the bookmaker-implied rate:

  | Claimed bin | n | Claimed | Actual | Bookmaker implied |
  |---|---|---|---|---|
  | <5% | 564 | 1.3% | 0.4% | 0.7% |
  | 5–15% | 375 | 9.4% | 5.1% | 5.5% |
  | 15–30% | 198 | 23.1% | 14.1% | 14.2% |
  | 30–50% | 257 | 36.1% | 17.5% | 23.4% |
  | 50%+ | 311 | 74.8% | 28.3% | 49.7% |

- **Prospective model check (latest pre-kickoff prediction, graded):** tennis ML Brier **0.2538** (n=111), worse than a constant 0.5 forecast (0.25). Football is severely *under*-confident: predicted 0.45 won 71%, predicted 0.56 won 92% (n=121 fixtures).
- **Market-anchor holdout (`settings.market_anchor`):** football model Brier 0.1694 vs SportyBet 0.1423, so `proven:false` and weight 0.0. Tennis is 0.2413 vs 0.2010.
- **Governance:** football v8, tennis v7 and volleyball v9 are nevertheless `active / validated`. The promotion gate checks calibration error against a baseline, **not against the market**.
- **Confidence:** High. **Impact:** model output has no exploitable signal.

### S2 — High

**F-05 · No deep match research is collected** · E-RES-01
- **DB:** feature fill rates across 4,204 snapshots: `injuries_out_*` 0%, `*_xg_for` 0%, `*_pace` 0%, `*_h1_*` 0%, `lineup_known_*` absent. `sports_ai_match_statistics` has 0 rows. Every input is derived from past final scores: attack/defence, form, H2H, rest days, games in 3/7 days.
- **Code:** `history_browser.py:1-6` ("A score is stored only when the page shows a finished result"); `match_analysis.py:433-435` admits xG, injuries and lineups are absent. `qualification_policy.require_lineups=false`.
- **Expected:** pre-match research covering standings, injuries, lineups, tactics, and congestion. **Actual:** score history only. The reported `completeness` average of 0.87 is measured against this narrow schema and overstates research depth.

**F-06 · Standings data is wrong and stale** · E-RES-03
- **DB:** `sports_ai_standings_snapshots` (162 rows). Every row has `source_url = https://api.openligadb.de/getbltable/bl1/2026` (men's Bundesliga), but it is stored under 7 competitions: "Germany Bundesliga", "2. Bundesliga", "Austria Bundesliga", "Germany Amateur Women Bundesliga", "Simulated Reality League Bundesliga SRL" and others. Latest observation is 2026-11-09. No other league worldwide has standings.

**F-07 · Bookmaker-price "history factor" is in-sample and tiny** · E-MKT-01
- **Code:** `pick_scoring.py:60-117` builds buckets from the last 90 days of closing prices. `history_factor` is applied directly to selection with no holdout and is clamped to [0.85, 1.15].
- **Data (`pick_calibration.json`, built 11-13 15:13):** the bucket driving today's real-money football favourites is `football|result|1.6-2.0`, n=49, 37 won vs 24.7 expected. It hits the 1.15 cap, so every 1.6–2.0 football favourite worldwide gets +15%. Today's `single_day_plan` picks all show `model_chance: null`, `history_factor 1.15`, `history_sample 49` (e.g. Uganda v Botswana: 0.587 → 0.675, "EV +0.08").
- **[INFERENCE]** This manufactures positive EV from a small, in-sample, single-window result, which is the classic overfitting pattern. The evidence base is about 11 days of SportyBet observations (observations start around 11-03).

**F-08 · The ticket ledger doesn't match what was booked** · E-BK-02
- Ticket `4d802a95…`, code `TWWQU6`: `booking_jobs.selections_json` lists 5 legs and the explanation says "combined 62.71". `accumulator_legs` holds 2 legs, and the screenshot shows 2 legs at 4.73 total. Beni Khalled is stored at 1.89 in the job and 1.97 on the slip.
- Four of the five job legs have **0 predictions, 0 feature snapshots, 0 analyses**.

**F-09 · Settlement and learning coverage is thin** · E-LRN-01
- 330,081 engine predictions, of which about 22,160 are graded (6.7%). Only about 820 *past* fixtures were ever predicted. Football: 281 predicted, 131 with any result, 121 graded. 250 past predicted fixtures have no result. `forecast_settlement`: 620 `skipped_unusable_result`, 450 awaiting. `clv_tracking` has **0 rows** even though the README describes CLV as a promotion criterion.
- `model_promotions` has 0 rows. There are 68 model versions in 11 days (33 on 11-09) and 171 drift events with no corrective action.

**F-10 · The real-money kill switch is unreliable under DB lock contention** · E-OPS-03
- `backend/logs/api-errors.log`: `POST /single-betting/account/revoke` at 2026-11-14T03:49:09Z → `sqlite3.OperationalError: database is locked` (`sportybet_account.py:805`). It succeeded on retry at 03:50:29. There are 6 "database is locked" API errors in the log, and OpenLigaDB has been in `error: database is locked` since 11-10 (paused).

### S3 — Medium

- **F-11 No cross-source verification.** 98,593 of 105,881 reconciliations are `single_source`, and all 822 identity links are `unresolved`. Score accuracy is **NOT VERIFIED**. Results sources nest recursively (`linked:linked:betexplorer:…`).
- **F-12 Source freshness gaps.** BetExplorer basketball fixtures last dated 11-10, Soccerway football last dated 11-12, standings last updated 11-09, OpenLigaDB has had no successful pull since 11-05.
- **F-13 Unbounded DB growth.** 18.5 GB (it was about 8.5 GB on 11-10). `snapshot_cleanup.status = blocked` (1,271 fixtures blocked by unsettled tickets), so nothing has been deleted. The C: drive was already near full.
- **F-14 Inconsistent status reporting.** `single_bets.mode` reads PAPER on rows that were real-money orders. Basketball shows `governance v7 inactive` while registry v2 is active. Market Brier for basketball is 0.2548, which is worse than a coin flip for moneyline; *[SUSPECTED]* a quote-to-outcome mapping error in basketball market data.
- **F-15 Value pipeline backlog.** `sports_ai_value_opportunities` holds 488,189 `awaiting_calibration` and 263,588 `awaiting_odds` rows. `value_cycle`: 18 checked, 18 stale.

### S4 — Low
- **F-16** README materially misdescribes current behaviour: "no wager", "BetExplorer manual", "hard gate for every product".
- **F-17** No worker log file exists, only `api-errors.log`. Worker execution history lives in DB tables only.

---

## 4. Source coverage and research completeness

| Source | Enabled | Actually collects | Freshness | Used for |
|---|---|---|---|---|
| SportyBet (Playwright) | yes (authorization 11-03) | Displayed prices, fixtures | 05:49 UTC today | Odds; **now also the probability** |
| BetExplorer (Playwright) | yes | Final scores (18 sports) | 05:28 UTC today | Form/H2H/settlement |
| Soccerway (Playwright) | yes | Final football scores | results 11-13 | Form/settlement |
| OpenFootball | yes | Fixtures + results | 03:45 today | **Corrupts dates (F-03)** |
| SportsDataverse | yes | NBA schedule/results | 03:45 today | Basketball |
| OpenLigaDB | paused/error | Fixtures, one standings table | last OK 11-05 | **Mislabelled standings** |
| football-data, BALLDONTLIE, Odds API | disabled, no keys | — | — | — |
| Any injury / lineup / news / xG source | **none** | — | — | — |

Research coverage per fixture: score history only. **League coverage (settings):** 190 of 435 leagues predicted (43.7%); `qualified: 0` in every sport.

## 5. Computer vision and browser autonomy

- **Browser automation: real.** Playwright drives SportyBet (prices, booking, account), BetExplorer and Soccerway on schedules without supervision. 154 `inspected` and 27 `failed` browser jobs; 239 codes generated with screenshots. Verified from screenshot `booking_evidence/f7ebe1b2….png`, which shows SportyBet's "Booking Code TWWQU6" modal.
- **Computer vision: absent.** No image interpretation, OCR, or LLM anywhere. `page.screenshot()` (e.g. `browser_operator.py:160`, `sportybet_account.py:1064`) is only stored as audit evidence. Page understanding is hand-written HTML/text parsers (`history_browser.parse_betexplorer_results`, `sportybet_research.parse_visible_text`). These break silently on layout changes.
- **Generic research operator:** `browser_operator.research_pages` exists, but its tables are empty, so it has never run operationally.
- **Recovery:** 38 of 39 recovery cases resolved; 4 jobs `retry_pending`. Challenge/CAPTCHA handling stops runs by design. Recovery under CAPTCHA is **NOT VERIFIED**.

## 6. Model intelligence, calibration and market selection

- **Algorithms (code):** football uses Poisson / Dixon-Coles with ridge/GBM rates. Basketball uses a points/margin Monte Carlo. Tennis, hockey and volleyball use rating-based "winner" models. All are fed only score-derived features.
- **Leakage:** feature cutoffs are clean (1 of 4,204 has cutoff after kickoff). The *pick history factor* is in-sample (F-07). Misdated results (F-03) are a contamination path *[SUSPECTED for holdouts]*.
- **Calibration:** failed prospectively (F-04). Holdout "calibration error" gates (≤0.20 policy; football 0.089) passed models that the market beats.
- **Market selection today:** candidates are SportyBet favourites priced 1.60–1.99 (singles) or Sure Odds tickets at 2.50–2.95. Probability = SportyBet price ÷ margin × history factor (±15%), blended 50/50 with the model only where an active validated prediction exists. Today's plan shows `model_chance: null` for every pick shown.
- **Leg sources over time:** Nov 3–9: 100% trained model (5,028 legs). Nov 11: 99% market-price. Nov 13–14: 100% market-price (781 legs).

## 7. Data integrity, settlement and learning

- Duplicates: only 1 same-day duplicate fixture group among upcoming fixtures, which is good.
- Settlement timing: 0 results settled before kickoff, which is good.
- Corruption: about 435 misdated UEFA results (F-03) and 162 mislabelled standings rows (F-06).
- Learning: 10 learning runs complete, but nothing changes the selection basis. Drift is recorded and not acted on. CLV is never recorded.

## 8. Root causes (S1/S2)

| Finding | Root cause |
|---|---|
| F-01, F-02, F-07 | Trained models failed, so the system pivoted to bookmaker prices plus a small in-sample tweak. Real-money automation and "qualified" labelling were kept, and the integrity gate was routed around for market-priced products. |
| F-03 | Football.TXT parser assumes chronological ordering of date lines. |
| F-04 | Inputs carry almost no information beyond what the market already prices (scores only). Promotion gates measure self-calibration, not skill vs market. Small, short holdouts. |
| F-05 | No permitted provider for injuries, lineups, xG or statistics was ever integrated. The browser only parses score tables. |
| F-06 | OpenLigaDB standings writer tags one table with whatever competition triggered the fetch. |
| F-09 | Results linkage depends on single-source name matching. Identity links are never resolved. CLV is never written. |
| F-10 | One SQLite file shared by API, worker and browser loops, with heavy write load (18.5 GB, millions of rows). |

## 9. Remediation roadmap (prioritized)

| P | Action | Depends on | Acceptance criteria |
|---|---|---|---|
| **P0** | Set execution mode to PAPER and revoke the live authorization until P3 is met | — | `single_betting_runtime.execution_mode='PAPER'`; no new `single_live_orders` rows |
| **P0** | Make revoke/PAPER fail-safe: retry with a busy timeout, plus a file-based kill flag checked before every submit | — | Revoke succeeds under induced lock in a test DB; submit refuses when the flag is set |
| **P1** | Route **all** booking/betting products through `prediction_integrity.require_bookable`, or rename market-priced products as "no-edge / entertainment" and never label them `qualified` | — | No `qualified` ticket without an ELIGIBLE capability row |
| **P1** | Fix the Football.TXT year inference (use the season span and reset per section), purge and re-import affected results, retrain | — | 0 results with fixture `starts_at` > now; no 2021/22–2023/24 results dated ≥ 2024 |
| **P1** | Fix the standings competition key; drop the 162 mislabelled rows | — | Each `(competition, source_url)` pair is unique and correct |
| **P2** | Add model-vs-market skill to promotion: a model must beat SportyBet's margin-free Brier/log-loss on a forward holdout (≥500 settled, Bonferroni) | P1 fixes | `model_promotions` rows record the market comparison; football/tennis demoted until they pass |
| **P2** | Hold out the history factor: build on window A, evaluate on window B, and accept only buckets whose out-of-sample factor CI excludes 1.0 | — | Report of out-of-sample factor per bucket |
| **P2** | Start CLV tracking and resolve identity links for result cross-checks | — | `clv_tracking` > 0 rows; ≥80% of settled fixtures have 2-source score agreement |
| **P3** | Integrate a permitted source for lineups, injuries and xG/statistics; populate `match_statistics` | licensing | ≥70% fill for those features on top leagues |
| **P3** | Re-enable real money only after a forward paper period shows positive conservative EV and CLV > 0 (95% lower bound) over ≥300 bets | P2 | Documented paper-trading report |
| **P4** | DB: move odds/observations to retention-bounded storage or a server DB; make cleanup independent of ticket settlement | — | DB size stable week over week; no "database is locked" in logs |
| **P4** | Correct the README and status endpoints (LIVE vs PAPER, gates, sources) | — | Docs match behaviour |

## 10. Final determination

**Does Cacsms Sport AI genuinely perform comprehensive, autonomous, evidence-driven match research before predicting and selecting markets? — No.**

- *Autonomous:* **yes** for collection, scheduling, booking and bet placement.
- *Comprehensive research:* **no.** Final scores only. No injuries, lineups, statistics, tactics or reliable standings.
- *Computer-vision-driven:* **no.** None exists.
- *Evidence-driven prediction:* **no.** Its own models were overconfident and lost (−32.5% on tickets; legs won 37% vs 52% claimed). Current selections are SportyBet's own prices nudged by an in-sample factor, and they carry negative stored edge.
- *Market selection with validated value:* **no.** 0 QUALIFIED and 0 ELIGIBLE, while bookings and real-money bets proceed through a path that bypasses the integrity gate.

### Not verified
Test-suite results (not run, to protect the live session/DB). Score accuracy of BetExplorer/Soccerway against a second source. CAPTCHA recovery. Real-money P&L beyond 24 settled bets, which is too few to judge in either direction (±₦50 so far).

## Appendix A — Evidence index
| ID | Source |
|---|---|
| E-OPS-01 | `sports_ai_schedules`, `sports_ai_worker_leases` (heartbeat 05:36:53Z) |
| E-OPS-03 | `backend/logs/api-errors.log` 2026-11-14T03:49:09Z revoke traceback |
| E-SRC-05 | `sports_ai_fixture_identity_links` (822 unresolved), `sports_ai_fixture_reconciliations` |
| E-RES-01 | `sports_ai_feature_snapshots` key fill profile (4,204 rows, 11-03→11-14) |
| E-RES-02 | `sports_ai_results` ⋈ `sports_ai_fixtures` where `starts_at` > now (295 rows); `acquisition_engine.py:350-361` |
| E-RES-03 | `sports_ai_standings_snapshots` grouped by competition/source_url |
| E-MOD-01 | `sports_ai_settings.market_anchor` (holdout Brier model vs market) |
| E-MOD-02 | `sports_ai_engine_predictions` latest pre-kickoff, graded (996 rows): Brier/reliability |
| E-MOD-03 | `sports_ai_accumulator_legs` by `model_id` (market-price vs trained) |
| E-MOD-04 | `sports_ai_ticket_settlements` ⋈ tickets, calibration bins |
| E-MKT-01 | `pick_scoring.py:26-31,167-184`; `day_plan.py:97-120`; `pick_calibration.json`; `settings.single_day_plan` |
| E-MKT-02 | `sportybet_booking.py:217-245`; `settings.qualification_status`, `capability_refresh` |
| E-MKT-03 | Tickets with `combined_odds` > 1000: claimed EV up to 492,055 |
| E-BK-01 | `sports_ai_booking_jobs` statuses; `sports_ai_booking_codes` (239, method `displayed`) |
| E-BK-02 | Ticket `4d802a95…` / code `TWWQU6`, screenshot `booking_evidence/f7ebe1b2….png` |
| E-BET-01 | `sports_ai_single_live_orders`, `sports_ai_single_authorizations`, `settings.single_betting_runtime`, `single_betting.py:52-65` |
| E-LRN-01 | Prediction→result→graded funnel; `clv_tracking`=0, `model_promotions`=0, `model_drift_events`=171 |
| E-CV-01 | Repo-wide search for ocr/vision/llm/anthropic/openai/cv2: none; `requirements.txt` |
