"""Reproduce the basketball audit without browsing or placing a wager.

Run with backend/.venv/Scripts/python.exe scripts/basketball_audit.py --validate
An optional --import-json path imports original settled bookmaker evidence.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
import db
from basketball_losses import dashboard, import_evidence, investigate
from basketball_validation import validate


def display(value, percent=False):
    return 'unavailable' if value is None else (f'{value*100:.2f}%' if percent else f'{value:.4f}')


def write_report(report, destination):
    audit=report['forecast_audit'] or {}
    lines=['# Basketball loss investigation', '', 'Evidence-based audit of original stored forecasts and separately supplied SportyBet settlements.', '',
           '## Actual betting records', '',
           'These are user-supplied bookmaker records, not independently authenticated account transactions. The sample is not the full betting history.', '']
    for cash in report['actual_cash']:
        lines.append(f"- {cash['currency']}: {cash['selections']} tickets; stake {cash['stake']:.2f}; return {cash['total_return']:.2f}; net {cash['profit']:.2f}; ROI {display(cash['roi'],True)}.")
    actual=report['actual_selections']
    lines+=['',f"Basketball selection occurrences: {actual['binary_selections']}; win rate {display(actual['win_rate'],True)}; confirmed original probability links: {actual['scored']}.",
            'Repeated bets are counted as separate actual exposures. Duplicate screenshot views of the same bet ID are counted once.',
            'Accumulator cash returns remain at ticket level. League/market/odds/model ROI uses singles only. Unknown placement, settlement timestamps and currency remain unknown.', '',
            'The supplied football legs are still pending in these screenshots. This sample cannot establish a basketball-versus-football ROI comparison.', '',
            '## Established defects and evidence limits', '',
            '- The High Odds/Sure Odds path could read research predictions without the central active-model qualification check. The earlier patch closed generation/booking/code-storage bypasses.',
            '- The Jena and Pinheiros stored pre-match home forecasts were approximately 93.45% and 93.77%, based on only 4–6 prior team games. Their screenshot selections lost. This demonstrates overconfidence in these examples, not the expected performance of every basketball market.',
            '- The Koblenz screenshot set contains both home and away bets. The stored forecast favoured away (about 76.56%). No matching stored generated ticket establishes why the 4.40 home pick was shown. A reversed selection cannot be asserted as a proven root cause.',
            '- Team scoring rates multiply sparse venue attack/defence estimates. Margin and total uncertainty shared the same independent-score variance; opponent quality and common pace effects were incompletely represented. New candidates fit margin/total residual distributions separately.',
            '- Legacy training fitted temperature on baseline probabilities even when a different scoring family was selected; Monte Carlo selection and holdout/inference also differed. Basketball calibration now uses the selected family consistently, including the same simulation draw count and conditional no-tie winner distribution. Existing stored probabilities are unchanged. New candidate artifacts use one consistent scoring/calibration recipe and remain isolated from legacy promotion.',
            '- Global calibration does not establish validity for each league, price range, alternate line, or overtime definition. Historical quotes with unknown market periods cannot establish release evidence.',
            '- Missing injuries, lineups, possessions and player availability are coverage limitations. No injury effects or causal explanations have been fabricated.',
            '- Local scheduled/pending fixture states did not establish actual bookmaker settlement. The evidence ledger preserves user-supplied settlements without overwriting official-result tables.', '',
            '## Underperforming forecast cohorts', '',
            'Original probabilities stored before kickoff; latest per fixture/market/model; official final score required. These metrics are not actual-bet cash returns.', '',
            '| Model | League | Market family | Scored selections | Brier | Log loss | ECE |',
            '|---|---|---|---:|---:|---:|---:|']
    for r in audit.get('watchlist',audit.get('breaches',[])):
        lines.append(f"| {r['model_id']} | {r['competition']} ({r['distinct_fixtures']} distinct fixtures; {r.get('evidence_strength','threshold_breach')}) | {r['market_family']} | {r['scored']} | {display(r['brier_score'])} | {display(r['log_loss'])} | {display(r['calibration_error'],True)} |")
    if not audit.get('watchlist',audit.get('breaches')): lines.append('| No adequately sampled cohort breaches detected | | | | | | |')
    lines+=['','All market sides are evaluated for probability scoring; their combined win rate is not recommendation accuracy. Multiple lines and opposite sides do not increase the independent fixture count. Small cohorts are descriptive watchlist entries, not statistically established failure rates.']
    lines+=['', '## Frozen correction and release criteria', '',
            'Per-league ridge models predict margin and total using cutoff-safe scoring-strength, rest, congestion and form features. Optional player/pace inputs are used only if present throughout training. Residual variance is estimated from training only. A fixed temperature grid uses calibration only. All simultaneous kickoffs remain in one split, with an embargo on labels settled after the next split begins.', '',
            'Moneyline, source-backed half-point totals and half-point spreads have separate calibration and approvals. Integer/push-capable lines stay unsupported by this binary correction model. No historical line or quote period is generated.', '',
            'Qualification and candidate data require at least ten prior completed matches for each team. Sparse-history forecasts continue for research, but cannot supply accumulator proposals.', '',
            'Requirements: 100 training fixtures, 40 calibration observations, 60 league holdout fixtures, at least 30 distinct holdout fixtures per released market with explicit pre-match including-overtime quotes; holdout ECE ≤ 0.08; Brier improvement ≥ 0.005; ECE improvement ≥ 0.01; improved log loss; paired fixture bootstrap 95% Brier-difference upper bound below zero.', '',
            'Repeated validation of the same dataset reuses the frozen artifact. A new dataset has a new hash. These historical comparisons are research evidence, not a claim that a previously examined dataset is a never-seen future test.', '',
            'Automatic quarantine: at least 30 distinct settled forecast fixtures in a model/league/family cohort and ECE > 0.15, Brier > 0.25, or log loss > 0.80. Legacy models without exact loss-audit approvals are held regardless of aggregate calibration. Data collection, forecasts and evaluation continue.', '',
            '## Candidate results', '']
    for candidate in report['candidates']:
        lines += [f"### {candidate['recipe_version']} — {candidate['status']}", '',f"Dataset hash: `{candidate['dataset_hash']}`",'']
        for league in json.loads(candidate['metrics_json']).get('leagues',[]):
            lines.append(f"- {league['competition']}: {league['status']}; split {league['counts']}. {league.get('reason','')}")
            for market in league.get('markets',[]):
                a,b=market['candidate'],market['legacy_baseline']
                lines.append(f"  - {market['family']}: {market['status']}; Brier {display(b['brier_score'])} → {display(a['brier_score'])}; ECE {display(b['calibration_error'],True)} → {display(a['calibration_error'],True)}; paired 95% delta interval {market['brier_delta_95pct']}. Blockers: {'; '.join(market['release_blockers']) or 'none'}")
    lines+=['','## Contradictory-selection control and reproducibility','',
            'When opposing basketball recommendations would both qualify within one decision snapshot, both are suspended, with CONTRADICTORY_SELECTION recorded. Complementary moneyline sides, over/under at the same line, and signed complementary spreads are covered. Ordinary probability estimates for both sides are not themselves recommendations.', '',
            'Use `backend/.venv/Scripts/python.exe -m unittest discover -s tests -p test_basketball_losses.py -v` for immutable evidence, actual cash/forecast separation, score validation, conflicting recommendations, chronological embargo, holdout isolation, spread direction and release-gate tests.', '',
            'Reproduce: `backend/.venv/Scripts/python.exe scripts/basketball_audit.py --validate`. To add genuine records use `--import-json PATH`. Raw imported data remains in SQLite and the private report directory. The React dashboard is `/sports/basketball-losses`.', '',
            'No new basketball accumulator eligibility is granted unless the complete frozen release criteria pass. No profitable outcome is promised.']
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text('\n'.join(lines)+'\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--import-json',type=Path);parser.add_argument('--validate',action='store_true')
    parser.add_argument('--output',type=Path,default=ROOT/'reports'/'basketball-loss-investigation.md')
    args=parser.parse_args();db.initialize()
    with db.connect(busy_timeout_ms=60000) as c:
        imported=import_evidence(c,json.loads(args.import_json.read_text(encoding='utf-8-sig')),'forensic-audit') if args.import_json else None
        audit=investigate(c,'forensic-audit')
        candidate=validate(c,'forensic-audit') if args.validate else None
        report=dashboard(c)
    write_report(report,args.output)
    private=ROOT/'reports'/'private';private.mkdir(parents=True,exist_ok=True)
    (private/'basketball-loss-evidence.json').write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({'import':imported,'forecast_count':audit['forecast_count'],'breaches':len(audit['breaches']),
                      'candidate':{k:v for k,v in (candidate or {}).items() if k!='metrics'},
                      'actual_cash':report['actual_cash'],'report':str(args.output)}))


if __name__=='__main__':main()
