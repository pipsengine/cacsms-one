# Basketball loss investigation

Evidence-based audit of original stored forecasts and separately supplied SportyBet settlements.

## Actual betting records

These are user-supplied bookmaker records, not independently authenticated account transactions. The sample is not the full betting history.

- UNSPECIFIED: 5 tickets; stake 840.00; return 535.63; net -304.37; ROI -36.23%.

Basketball selection occurrences: 8; win rate 37.50%; confirmed original probability links: 0.
Repeated bets are counted as separate actual exposures. Duplicate screenshot views of the same bet ID are counted once.
Accumulator cash returns remain at ticket level. League/market/odds/model ROI uses singles only. Unknown placement, settlement timestamps and currency remain unknown.

The supplied football legs are still pending in these screenshots. This sample cannot establish a basketball-versus-football ROI comparison.

## Established defects and evidence limits

- The High Odds/Sure Odds path could read research predictions without the central active-model qualification check. The earlier patch closed generation/booking/code-storage bypasses.
- The Jena and Pinheiros stored pre-match home forecasts were approximately 93.45% and 93.77%, based on only 4–6 prior team games. Their screenshot selections lost. This demonstrates overconfidence in these examples, not the expected performance of every basketball market.
- The Koblenz screenshot set contains both home and away bets. The stored forecast favoured away (about 76.56%). No matching stored generated ticket establishes why the 4.40 home pick was shown. A reversed selection cannot be asserted as a proven root cause.
- Team scoring rates multiply sparse venue attack/defence estimates. Margin and total uncertainty shared the same independent-score variance; opponent quality and common pace effects were incompletely represented. New candidates fit margin/total residual distributions separately.
- Legacy training fitted temperature on baseline probabilities even when a different scoring family was selected; Monte Carlo selection and holdout/inference also differed. Basketball calibration now uses the selected family consistently, including the same simulation draw count and conditional no-tie winner distribution. Existing stored probabilities are unchanged. New candidate artifacts use one consistent scoring/calibration recipe and remain isolated from legacy promotion.
- Global calibration does not establish validity for each league, price range, alternate line, or overtime definition. Historical quotes with unknown market periods cannot establish release evidence.
- Missing injuries, lineups, possessions and player availability are coverage limitations. No injury effects or causal explanations have been fabricated.
- Local scheduled/pending fixture states did not establish actual bookmaker settlement. The evidence ledger preserves user-supplied settlements without overwriting official-result tables.

## Underperforming forecast cohorts

Original probabilities stored before kickoff; latest per fixture/market/model; official final score required. These metrics are not actual-bet cash returns.

| Model | League | Market family | Scored selections | Brier | Log loss | ECE |
|---|---|---|---:|---:|---:|---:|
| 2864ea8f-69dc-4a6c-a37f-8972c10a5841 | NBA 2027 (2 distinct fixtures; insufficient_distinct_fixtures) | total | 20 | 0.3805 | 1.0955 | 38.03% |
| 2864ea8f-69dc-4a6c-a37f-8972c10a5841 | NBA 2027 (2 distinct fixtures; insufficient_distinct_fixtures) | moneyline | 4 | 0.2083 | 0.6092 | 45.54% |
| 2864ea8f-69dc-4a6c-a37f-8972c10a5841 | NBA 2027 (2 distinct fixtures; insufficient_distinct_fixtures) | spread | 8 | 0.1054 | 0.3886 | 31.96% |
| bb14c78d-592f-4048-81bc-217ee847c802 | NBA 2027 (5 distinct fixtures; insufficient_distinct_fixtures) | moneyline | 10 | 0.1455 | 0.4639 | 36.03% |
| bb14c78d-592f-4048-81bc-217ee847c802 | Japan B League One (4 distinct fixtures; insufficient_distinct_fixtures) | moneyline | 8 | 0.2898 | 0.7771 | 10.75% |
| dc4ab9a0-aa3b-49a2-8a61-3a98b521db91 | NBA 2027 (2 distinct fixtures; insufficient_distinct_fixtures) | spread | 8 | 0.1277 | 0.4416 | 35.66% |
| dc4ab9a0-aa3b-49a2-8a61-3a98b521db91 | NBA 2027 (2 distinct fixtures; insufficient_distinct_fixtures) | moneyline | 4 | 0.2122 | 0.6172 | 46.04% |
| dc4ab9a0-aa3b-49a2-8a61-3a98b521db91 | Japan B League One (9 distinct fixtures; insufficient_distinct_fixtures) | moneyline | 18 | 0.2313 | 0.6398 | 25.70% |
| dc4ab9a0-aa3b-49a2-8a61-3a98b521db91 | Japan B League One (9 distinct fixtures; insufficient_distinct_fixtures) | spread | 36 | 0.1985 | 0.5704 | 17.64% |
| dc4ab9a0-aa3b-49a2-8a61-3a98b521db91 | Israel Super League (1 distinct fixtures; insufficient_distinct_fixtures) | spread | 4 | 0.6881 | 1.7719 | 82.94% |
| dc4ab9a0-aa3b-49a2-8a61-3a98b521db91 | Israel Super League (1 distinct fixtures; insufficient_distinct_fixtures) | moneyline | 2 | 0.5751 | 1.4203 | 75.84% |
| dc4ab9a0-aa3b-49a2-8a61-3a98b521db91 | Denmark Basketligaen (1 distinct fixtures; insufficient_distinct_fixtures) | spread | 4 | 0.0536 | 0.2627 | 23.08% |
| dc4ab9a0-aa3b-49a2-8a61-3a98b521db91 | Denmark Basketligaen (1 distinct fixtures; insufficient_distinct_fixtures) | total | 10 | 0.1603 | 0.4676 | 32.57% |
| dc4ab9a0-aa3b-49a2-8a61-3a98b521db91 | Denmark Basketligaen (1 distinct fixtures; insufficient_distinct_fixtures) | moneyline | 2 | 0.4762 | 1.1713 | 69.00% |
| ee44d9dd-045e-4f15-8880-d4059279df36 | NBA 2027 (1 distinct fixtures; insufficient_distinct_fixtures) | spread | 4 | 0.0525 | 0.2597 | 22.85% |
| ee44d9dd-045e-4f15-8880-d4059279df36 | NBA 2027 (1 distinct fixtures; insufficient_distinct_fixtures) | total | 10 | 0.5943 | 1.9491 | 68.13% |
| ee44d9dd-045e-4f15-8880-d4059279df36 | NBA 2027 (1 distinct fixtures; insufficient_distinct_fixtures) | moneyline | 2 | 0.4710 | 1.1592 | 68.63% |
| 06adf719-6563-47ba-8664-507f4bb15a3d | Japan B League One (6 distinct fixtures; insufficient_distinct_fixtures) | moneyline | 12 | 0.0688 | 0.2762 | 23.18% |

All market sides are evaluated for probability scoring; their combined win rate is not recommendation accuracy. Multiple lines and opposite sides do not increase the independent fixture count. Small cohorts are descriptive watchlist entries, not statistically established failure rates.

## Frozen correction and release criteria

Per-league ridge models predict margin and total using cutoff-safe scoring-strength, rest, congestion and form features. Optional player/pace inputs are used only if present throughout training. Residual variance is estimated from training only. A fixed temperature grid uses calibration only. All simultaneous kickoffs remain in one split, with an embargo on labels settled after the next split begins.

Moneyline, source-backed half-point totals and half-point spreads have separate calibration and approvals. Integer/push-capable lines stay unsupported by this binary correction model. No historical line or quote period is generated.

Requirements: 100 training fixtures, 40 calibration observations, 60 league holdout fixtures, at least 30 distinct holdout fixtures per released market with explicit pre-match including-overtime quotes; holdout ECE ≤ 0.08; Brier improvement ≥ 0.005; ECE improvement ≥ 0.01; improved log loss; paired fixture bootstrap 95% Brier-difference upper bound below zero.

Repeated validation of the same dataset reuses the frozen artifact. A new dataset has a new hash. These historical comparisons are research evidence, not a claim that a previously examined dataset is a never-seen future test.

Automatic quarantine: at least 30 distinct settled forecast fixtures in a model/league/family cohort and ECE > 0.15, Brier > 0.25, or log loss > 0.80. Legacy models without exact loss-audit approvals are held regardless of aggregate calibration. Data collection, forecasts and evaluation continue.

## Candidate results

### basketball-loss-audit-v1 — research

Dataset hash: `26211326497b618d28f9909a77025917d2c15c8622b4cc2c7d22ca516adcc066`

- Albania: Superliga: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 2 usable fixtures in this league; frozen chronological sample requirements not met.
- Argentina: Liga A: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 5 usable fixtures in this league; frozen chronological sample requirements not met.
- Argentina: Liga Femenina Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 8 usable fixtures in this league; frozen chronological sample requirements not met.
- Australia NBL: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 3 usable fixtures in this league; frozen chronological sample requirements not met.
- Australia: NBL: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 1 usable fixtures in this league; frozen chronological sample requirements not met.
- Australia: WNBL Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 3 usable fixtures in this league; frozen chronological sample requirements not met.
- Austria: Superliga: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 4 usable fixtures in this league; frozen chronological sample requirements not met.
- Austria: Superliga Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 4 usable fixtures in this league; frozen chronological sample requirements not met.
- Austria: Zweite Liga: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 3 usable fixtures in this league; frozen chronological sample requirements not met.
- Bahrain Premier League: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 1 usable fixtures in this league; frozen chronological sample requirements not met.
- Bahrain: Premier League: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 1 usable fixtures in this league; frozen chronological sample requirements not met.
- Belarus: Premier League: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 3 usable fixtures in this league; frozen chronological sample requirements not met.
- Belarus: Premier League Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 2 usable fixtures in this league; frozen chronological sample requirements not met.
- Belgium: Top Division Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 1 usable fixtures in this league; frozen chronological sample requirements not met.
- Bosnia and Herzegovina: Prvenstvo BiH: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 2 usable fixtures in this league; frozen chronological sample requirements not met.
- Brazil: NBB: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 11 usable fixtures in this league; frozen chronological sample requirements not met.
- Bulgaria: NBL: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 7 usable fixtures in this league; frozen chronological sample requirements not met.
- Chile: LNB: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 5 usable fixtures in this league; frozen chronological sample requirements not met.
- Colombia LBP: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 1 usable fixtures in this league; frozen chronological sample requirements not met.
- Colombia: LBP: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 3 usable fixtures in this league; frozen chronological sample requirements not met.
- Croatia: Premijer Liga Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 4 usable fixtures in this league; frozen chronological sample requirements not met.
- Croatia: Premijer liga: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 4 usable fixtures in this league; frozen chronological sample requirements not met.
- Croatia: Prva Liga: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 5 usable fixtures in this league; frozen chronological sample requirements not met.
- Cyprus: Division A: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 2 usable fixtures in this league; frozen chronological sample requirements not met.
- Czech Republic: 1. Liga: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 6 usable fixtures in this league; frozen chronological sample requirements not met.
- Czech Republic: 1. Liga Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 6 usable fixtures in this league; frozen chronological sample requirements not met.
- Czech Republic: NBL: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 7 usable fixtures in this league; frozen chronological sample requirements not met.
- Denmark: Basketligaen: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 7 usable fixtures in this league; frozen chronological sample requirements not met.
- Denmark: Kvindebasketligaen Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 4 usable fixtures in this league; frozen chronological sample requirements not met.
- Europe: AdmiralBet ABA League: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 5 usable fixtures in this league; frozen chronological sample requirements not met.
- Europe: BNXT League: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 3 usable fixtures in this league; frozen chronological sample requirements not met.
- Europe: ENBL: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 1 usable fixtures in this league; frozen chronological sample requirements not met.
- Europe: EuroCup Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 17 usable fixtures in this league; frozen chronological sample requirements not met.
- Europe: Eurocup: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 9 usable fixtures in this league; frozen chronological sample requirements not met.
- Europe: Euroleague: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 9 usable fixtures in this league; frozen chronological sample requirements not met.
- Europe: Euroleague Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 2 usable fixtures in this league; frozen chronological sample requirements not met.
- Europe: FIBA Europe Cup: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 16 usable fixtures in this league; frozen chronological sample requirements not met.
- Europe: Latvian-Estonian League: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 7 usable fixtures in this league; frozen chronological sample requirements not met.
- Finland Korisliiga: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 1 usable fixtures in this league; frozen chronological sample requirements not met.
- Finland: I Divisioona A: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 3 usable fixtures in this league; frozen chronological sample requirements not met.
- Finland: I Divisioona Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 2 usable fixtures in this league; frozen chronological sample requirements not met.
- Finland: Korisliiga: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 5 usable fixtures in this league; frozen chronological sample requirements not met.
- Finland: Korisliiga Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 2 usable fixtures in this league; frozen chronological sample requirements not met.
- France: Elite 2: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 9 usable fixtures in this league; frozen chronological sample requirements not met.
- France: Espoirs U21: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 7 usable fixtures in this league; frozen chronological sample requirements not met.
- France: LFB Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 5 usable fixtures in this league; frozen chronological sample requirements not met.
- France: LNB: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 6 usable fixtures in this league; frozen chronological sample requirements not met.
- France: Ligue 2 Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 7 usable fixtures in this league; frozen chronological sample requirements not met.
- Georgia: Superleague: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 2 usable fixtures in this league; frozen chronological sample requirements not met.
- Germany BBL: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 1 usable fixtures in this league; frozen chronological sample requirements not met.
- Germany: BBL: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 4 usable fixtures in this league; frozen chronological sample requirements not met.
- Germany: DBBL Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 5 usable fixtures in this league; frozen chronological sample requirements not met.
- Germany: Pro A: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 5 usable fixtures in this league; frozen chronological sample requirements not met.
- Greece: Basket League: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 5 usable fixtures in this league; frozen chronological sample requirements not met.
- Greece: Elite League: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 7 usable fixtures in this league; frozen chronological sample requirements not met.
- Greece: WBL1 Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 6 usable fixtures in this league; frozen chronological sample requirements not met.
- Hong Kong: A1: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 1 usable fixtures in this league; frozen chronological sample requirements not met.
- Hungary: NB I. A: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 6 usable fixtures in this league; frozen chronological sample requirements not met.
- Iceland: Premier League: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 5 usable fixtures in this league; frozen chronological sample requirements not met.
- Israel: Super League: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 2 usable fixtures in this league; frozen chronological sample requirements not met.
- Italy Serie A2: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 1 usable fixtures in this league; frozen chronological sample requirements not met.
- Italy: Lega A: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 1 usable fixtures in this league; frozen chronological sample requirements not met.
- Italy: Serie A1 Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 6 usable fixtures in this league; frozen chronological sample requirements not met.
- Italy: Serie A2: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 5 usable fixtures in this league; frozen chronological sample requirements not met.
- Italy: Serie A2 Women - Gr. A: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 4 usable fixtures in this league; frozen chronological sample requirements not met.
- Italy: Serie A2 Women - Gr. B: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 4 usable fixtures in this league; frozen chronological sample requirements not met.
- Japan: B.League One: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 12 usable fixtures in this league; frozen chronological sample requirements not met.
- Japan: B.League Premier: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 9 usable fixtures in this league; frozen chronological sample requirements not met.
- Kosovo: Superliga: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 2 usable fixtures in this league; frozen chronological sample requirements not met.
- Kosovo: Superliga Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 1 usable fixtures in this league; frozen chronological sample requirements not met.
- Lebanon: Division 1: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 1 usable fixtures in this league; frozen chronological sample requirements not met.
- Lithuania NKL: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 1 usable fixtures in this league; frozen chronological sample requirements not met.
- Lithuania: LKL: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 3 usable fixtures in this league; frozen chronological sample requirements not met.
- Lithuania: Moteru Lyga Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 5 usable fixtures in this league; frozen chronological sample requirements not met.
- Lithuania: NKL: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 16 usable fixtures in this league; frozen chronological sample requirements not met.
- Luxembourg: LBBL: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 4 usable fixtures in this league; frozen chronological sample requirements not met.
- Mexico: LNBP: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 1 usable fixtures in this league; frozen chronological sample requirements not met.
- NBA 2027: insufficient_data; split {'calibration': 6, 'holdout': 12, 'train': 16}. Only 39 usable fixtures in this league; frozen chronological sample requirements not met.
- Netherlands: WBL Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 5 usable fixtures in this league; frozen chronological sample requirements not met.
- New Zealand: Tauihi Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 1 usable fixtures in this league; frozen chronological sample requirements not met.
- Norway: BLNO: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 2 usable fixtures in this league; frozen chronological sample requirements not met.
- Norway: BLNO Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 1 usable fixtures in this league; frozen chronological sample requirements not met.
- Paraguay: LNB: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 1 usable fixtures in this league; frozen chronological sample requirements not met.
- Philippines PBA, Governors Cup: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 1 usable fixtures in this league; frozen chronological sample requirements not met.
- Philippines: MPBL: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 2 usable fixtures in this league; frozen chronological sample requirements not met.
- Philippines: PBA Governors' Cup: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 1 usable fixtures in this league; frozen chronological sample requirements not met.
- Poland: 1. Liga: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 5 usable fixtures in this league; frozen chronological sample requirements not met.
- Poland: Basket Liga: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 4 usable fixtures in this league; frozen chronological sample requirements not met.
- Poland: Basket Liga Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 1 usable fixtures in this league; frozen chronological sample requirements not met.
- Portugal: LFB Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 5 usable fixtures in this league; frozen chronological sample requirements not met.
- Portugal: LPB: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 5 usable fixtures in this league; frozen chronological sample requirements not met.
- Portugal: Proliga: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 6 usable fixtures in this league; frozen chronological sample requirements not met.
- Romania: Divizia A: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 2 usable fixtures in this league; frozen chronological sample requirements not met.
- Romania: Liga National Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 4 usable fixtures in this league; frozen chronological sample requirements not met.
- Russia: Premier League Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 5 usable fixtures in this league; frozen chronological sample requirements not met.
- Russia: Super League: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 6 usable fixtures in this league; frozen chronological sample requirements not met.
- Russia: VTB United League: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 4 usable fixtures in this league; frozen chronological sample requirements not met.
- Serbia: First League: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 3 usable fixtures in this league; frozen chronological sample requirements not met.
- Slovakia: Extraliga: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 5 usable fixtures in this league; frozen chronological sample requirements not met.
- Slovakia: Extraliga Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 3 usable fixtures in this league; frozen chronological sample requirements not met.
- Slovenia: 1. SKL Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 3 usable fixtures in this league; frozen chronological sample requirements not met.
- Slovenia: Liga OTP banka: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 4 usable fixtures in this league; frozen chronological sample requirements not met.
- South Korea: KBL: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 5 usable fixtures in this league; frozen chronological sample requirements not met.
- Spain: ACB: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 2 usable fixtures in this league; frozen chronological sample requirements not met.
- Spain: Liga Femenina Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 6 usable fixtures in this league; frozen chronological sample requirements not met.
- Spain: Primera FEB: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 6 usable fixtures in this league; frozen chronological sample requirements not met.
- Spain: Segunda FEB: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 6 usable fixtures in this league; frozen chronological sample requirements not met.
- Sweden: Basketettan Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 8 usable fixtures in this league; frozen chronological sample requirements not met.
- Sweden: Basketligan: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 5 usable fixtures in this league; frozen chronological sample requirements not met.
- Sweden: Basketligan Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 7 usable fixtures in this league; frozen chronological sample requirements not met.
- Sweden: Superettan: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 5 usable fixtures in this league; frozen chronological sample requirements not met.
- Switzerland: SB League: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 4 usable fixtures in this league; frozen chronological sample requirements not met.
- Switzerland: SB League Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 5 usable fixtures in this league; frozen chronological sample requirements not met.
- Turkey: KBSL Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 5 usable fixtures in this league; frozen chronological sample requirements not met.
- Turkey: Super Lig: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 4 usable fixtures in this league; frozen chronological sample requirements not met.
- Turkey: TB2L: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 8 usable fixtures in this league; frozen chronological sample requirements not met.
- Turkey: TBL: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 4 usable fixtures in this league; frozen chronological sample requirements not met.
- USA: NBA: insufficient_data; split {'calibration': 2, 'holdout': 8, 'train': 12}. Only 24 usable fixtures in this league; frozen chronological sample requirements not met.
- USA: NCAA: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 1 usable fixtures in this league; frozen chronological sample requirements not met.
- USA: NCAA Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 5 usable fixtures in this league; frozen chronological sample requirements not met.
- Ukraine: FBU Superleague: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 5 usable fixtures in this league; frozen chronological sample requirements not met.
- United Kingdom: SLB Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 5 usable fixtures in this league; frozen chronological sample requirements not met.
- Uruguay: Liga Uruguaya: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 2 usable fixtures in this league; frozen chronological sample requirements not met.
- World: World Tour Dubai 3x3: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Only 4 usable fixtures in this league; frozen chronological sample requirements not met.
### basketball-loss-audit-v1 — research

Dataset hash: `33e52eca0991abf074e642c7bf35ed21ba6fed6a12833cc3d63e188f0fac5231`

- Albania: Superliga: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Argentina: Liga A: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Argentina: Liga Femenina Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Australia NBL: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Australia: NBL: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Australia: WNBL Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Austria: Superliga: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Austria: Superliga Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Austria: Zweite Liga: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Bahrain Premier League: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Bahrain: Premier League: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Belarus: Premier League: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Belarus: Premier League Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Belgium: Top Division Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Bosnia and Herzegovina: Prvenstvo BiH: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Brazil: NBB: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Bulgaria: NBL: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Chile: LNB: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Colombia LBP: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Colombia: LBP: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Croatia: Premijer Liga Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Croatia: Premijer liga: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Croatia: Prva Liga: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Cyprus: Division A: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Czech Republic: 1. Liga: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Czech Republic: 1. Liga Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Czech Republic: NBL: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Denmark: Basketligaen: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Denmark: Kvindebasketligaen Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Europe: AdmiralBet ABA League: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Europe: BNXT League: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Europe: ENBL: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Europe: EuroCup Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Europe: Eurocup: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Europe: Euroleague: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Europe: Euroleague Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Europe: FIBA Europe Cup: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Europe: Latvian-Estonian League: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Finland Korisliiga: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Finland: I Divisioona A: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Finland: I Divisioona Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Finland: Korisliiga: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Finland: Korisliiga Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- France: Elite 2: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- France: Espoirs U21: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- France: LFB Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- France: LNB: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- France: Ligue 2 Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Georgia: Superleague: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Germany BBL: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Germany: BBL: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Germany: DBBL Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Germany: Pro A: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Greece: Basket League: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Greece: Elite League: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Greece: WBL1 Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Hong Kong: A1: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Hungary: NB I. A: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Iceland: Premier League: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Israel: Super League: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Italy Serie A2: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Italy: Lega A: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Italy: Serie A1 Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Italy: Serie A2: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Italy: Serie A2 Women - Gr. A: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Italy: Serie A2 Women - Gr. B: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Japan: B.League One: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Japan: B.League Premier: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Kosovo: Superliga: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Kosovo: Superliga Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Lebanon: Division 1: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Lithuania NKL: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Lithuania: LKL: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Lithuania: Moteru Lyga Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Lithuania: NKL: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Luxembourg: LBBL: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Mexico: LNBP: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- NBA 2027: insufficient_data; split {'calibration': 6, 'holdout': 12, 'train': 16}. Frozen chronological sample requirements not met.
- Netherlands: WBL Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- New Zealand: Tauihi Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Norway: BLNO: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Norway: BLNO Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Paraguay: LNB: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Philippines PBA, Governors Cup: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Philippines: MPBL: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Philippines: PBA Governors' Cup: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Poland: 1. Liga: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Poland: Basket Liga: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Poland: Basket Liga Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Portugal: LFB Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Portugal: LPB: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Portugal: Proliga: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Romania: Divizia A: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Romania: Liga National Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Russia: Premier League Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Russia: Super League: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Russia: VTB United League: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Serbia: First League: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Slovakia: Extraliga: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Slovakia: Extraliga Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Slovenia: 1. SKL Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Slovenia: Liga OTP banka: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- South Korea: KBL: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Spain: ACB: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Spain: Liga Femenina Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Spain: Primera FEB: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Spain: Segunda FEB: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Sweden: Basketettan Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Sweden: Basketligan: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Sweden: Basketligan Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Sweden: Superettan: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Switzerland: SB League: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Switzerland: SB League Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Turkey: KBSL Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Turkey: Super Lig: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Turkey: TB2L: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Turkey: TBL: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- USA: NBA: insufficient_data; split {'calibration': 2, 'holdout': 8, 'train': 12}. Frozen chronological sample requirements not met.
- USA: NCAA: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- USA: NCAA Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Ukraine: FBU Superleague: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- United Kingdom: SLB Women: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- Uruguay: Liga Uruguaya: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.
- World: World Tour Dubai 3x3: insufficient_data; split {'calibration': 0, 'holdout': 0, 'train': 0}. Frozen chronological sample requirements not met.

## Contradictory-selection control and reproducibility

When opposing basketball recommendations would both qualify within one decision snapshot, both are suspended, with CONTRADICTORY_SELECTION recorded. Complementary moneyline sides, over/under at the same line, and signed complementary spreads are covered. Ordinary probability estimates for both sides are not themselves recommendations.

Use `backend/.venv/Scripts/python.exe -m unittest discover -s tests -p test_basketball_losses.py -v` for immutable evidence, actual cash/forecast separation, score validation, conflicting recommendations, chronological embargo, holdout isolation, spread direction and release-gate tests.

Reproduce: `backend/.venv/Scripts/python.exe scripts/basketball_audit.py --validate`. To add genuine records use `--import-json PATH`. Raw imported data remains in SQLite and the private report directory. The React dashboard is `/sports/basketball-losses`.

No new basketball accumulator eligibility is granted unless the complete frozen release criteria pass. No profitable outcome is promised.
