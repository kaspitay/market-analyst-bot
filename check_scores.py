#!/usr/bin/env python3
"""Regression fixture for the scoring functions.

Replays docs/data/market-data.json back through the scoring code and diffs the
result against the scores stored alongside the inputs. Every scoring change
should produce an *intentional* diff here; anything else is a regression.

    python3 check_scores.py              # phase gate: must report 0 diffs
    python3 check_scores.py --self-test  # proves the harness can detect a change

The recommendation-flip count is the number that matters: a 3-point score drift
is noise, a Buy -> Sell flip on an 18% position is not.

analyzer imports requests, which the scoring path never touches (it is pure
arithmetic over dicts), so stub it and this runs on a bare clone with nothing
installed. A real call would raise AttributeError, not pass silently.
"""
import json
import os
import sys
import types

sys.modules.setdefault("requests", types.ModuleType("requests"))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import analyzer  # noqa: E402

DATA = os.path.join(HERE, "docs", "data", "market-data.json")
TOL = 0.05  # stored scores are rounded to 1dp


def replay(tech, ticker, ocf_veto_exempt):
    """Recompute the fundamental half plus the combine, from stored inputs only.

    Mirrors merge_fundamentals exactly, veto cap included — a fixture that skips
    the gates cannot tell you whether the gates still work.

    tech_score is an *input* here, not an output: recomputing it needs 200+ OHLCV
    bars and only 63 are stored per ticker. Phase 3 re-baselines it from a live run.
    """
    fund = tech.get("fundamentals") or {}
    history = tech.get("financialHistory")
    quality, _ = analyzer.compute_quality_score(fund, history)
    fund_score, _ = analyzer.compute_fundamental_score(
        fund,
        tech.get("price"),
        tech.get("target_mean"),
        tech.get("num_analysts"),
        sector=fund.get("sector"),
        financial_history=history,
    )
    tech_score = tech.get("tech_score")
    if fund_score is None or tech_score is None:
        return quality, fund_score, None, "No Data"
    combined = round(tech_score * 0.40 + fund_score * 0.60, 1)
    _, cap = analyzer.veto_gates(fund, history, ticker, ocf_veto_exempt)
    if cap is not None:
        combined = min(combined, cap)
        fund_score = min(fund_score, cap)
    return quality, fund_score, combined, analyzer.score_to_recommendation(combined)


def check(report=True):
    """Returns (rows_changed, recommendation_flips, abstained)."""
    tickers = json.load(open(DATA))["tickers"]
    ocf_veto_exempt = analyzer.load_config().get("ocf_veto_exempt", [])
    changed, flips, abstained = [], [], []

    for tkr in sorted(tickers):
        tech = (tickers[tkr] or {}).get("technicals") or {}
        if not tech:
            continue
        quality, fund, combined, rec = replay(tech, tkr, ocf_veto_exempt)
        was_f, was_c = tech.get("fund_score"), tech.get("combined_score")
        was_q, was_rec = tech.get("quality_score"), tech.get("recommendation")

        if fund is None:
            abstained.append(tkr)

        def moved(new, old):
            if new is None or old is None:
                return new is not old
            return abs(new - old) > TOL

        if moved(fund, was_f) or moved(combined, was_c) or quality != was_q:
            changed.append((tkr, was_q, quality, was_f, fund, was_c, combined))
        if rec != was_rec:
            flips.append((tkr, was_rec, rec))

    if report:
        print(f"replayed {len(tickers)} tickers from {os.path.relpath(DATA, HERE)}")
        if changed:
            print(f"\n{len(changed)} score(s) moved:")
            print(f"  {'tkr':7}{'qual':>10}{'fund':>16}{'combined':>16}")
            for tkr, q0, q1, f0, f1, c0, c1 in changed:
                print(f"  {tkr:7}{f'{q0}->{q1}':>10}{f'{f0}->{f1}':>16}{f'{c0}->{c1}':>16}")
        else:
            print("no score changes")
        if flips:
            print(f"\n{len(flips)} RECOMMENDATION FLIP(S):")
            for tkr, a, b in flips:
                print(f"  {tkr:7}{a} -> {b}")
        if abstained:
            print(f"\n{len(abstained)} abstained (No Data): {', '.join(abstained)}")
        print(f"\nchanged={len(changed)}  flips={len(flips)}  abstained={len(abstained)}")

    return len(changed), len(flips), len(abstained)


def self_test():
    """Prove the harness detects a change. A fixture that only ever reports zero
    proves nothing, so perturb a scoring constant and require it to be caught."""
    changed, flips, _ = check(report=False)
    if changed or flips:
        print(f"FAIL self-test: baseline is already dirty "
              f"(changed={changed} flips={flips}). Fix the replay first.")
        return 1

    original = dict(analyzer.THRESHOLDS)
    analyzer.THRESHOLDS["buy"] = 1  # everything above 1 becomes at least a Buy
    try:
        _, mutated_flips, _ = check(report=False)
    finally:
        analyzer.THRESHOLDS.clear()
        analyzer.THRESHOLDS.update(original)

    if not mutated_flips:
        print("FAIL self-test: mutated THRESHOLDS['buy'] to 1 and the harness "
              "reported no flips. It is not exercising score_to_recommendation.")
        return 1

    restored_changed, restored_flips, _ = check(report=False)
    if restored_changed or restored_flips:
        print("FAIL self-test: harness did not return to a clean baseline.")
        return 1

    print(f"self-test PASS: clean baseline, {mutated_flips} flips detected under "
          f"mutation, clean again after revert.")
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    changed, flips, _ = check()
    sys.exit(1 if (changed or flips) else 0)
