#!/usr/bin/env python3
"""Regression fixture for the scoring functions.

Replays docs/data/market-data.json back through the scoring code and diffs the
result against the scores stored alongside the inputs. Every scoring change
should produce an *intentional* diff here; anything else is a regression.

    python3 check_scores.py                # phase gate: must report 0 diffs
    python3 check_scores.py --self-test    # proves the harness can detect a change
    python3 check_scores.py --verify-monitor  # regression-checks compute_exceptions

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
from datetime import date, timedelta

sys.modules.setdefault("requests", types.ModuleType("requests"))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import analyzer  # noqa: E402

DATA = os.path.join(HERE, "docs", "data", "market-data.json")
TOL = 0.05  # stored scores are rounded to 1dp


def replay(tech, ticker, ocf_veto_exempt):
    """Recompute the fundamental half plus the combine, from stored inputs only.

    Mirrors merge_fundamentals exactly, veto cap included — a fixture that skips
    the gates cannot tell you whether the gates still work. Returns the veto
    reason too (not just the cap): monitor trigger 4 (VETO) keys entirely on the
    reason string changing, so a fixture that only checked the cap could not
    catch a gate firing with the wrong reason.

    tech_score is an *input* here, not an output: recomputing it needs 200+ OHLCV
    bars and only 63 are stored per ticker. Phase 3 re-baselines it from a live run.
    """
    fund = tech.get("fundamentals") or {}
    history = tech.get("financialHistory")
    fund_score, _, quality, _ = analyzer.compute_fundamental_score(
        fund,
        tech.get("price"),
        tech.get("target_mean"),
        tech.get("num_analysts"),
        sector=fund.get("sector"),
        financial_history=history,
    )
    tech_score = tech.get("tech_score")
    if fund_score is None or tech_score is None:
        return quality, fund_score, None, "No Data", None
    combined = round(tech_score * 0.40 + fund_score * 0.60, 1)
    veto_reason, cap = analyzer.veto_gates(fund, history, ticker, ocf_veto_exempt)
    if cap is not None:
        combined = min(combined, cap)
        fund_score = min(fund_score, cap)
    return quality, fund_score, combined, analyzer.score_to_recommendation(combined), veto_reason


def check(report=True):
    """Returns (rows_changed, recommendation_flips, abstained)."""
    tickers = json.load(open(DATA))["tickers"]
    ocf_veto_exempt = analyzer.load_config().get("ocf_veto_exempt", [])
    changed, flips, abstained = [], [], []

    for tkr in sorted(tickers):
        tech = (tickers[tkr] or {}).get("technicals") or {}
        if not tech:
            continue
        quality, fund, combined, rec, veto_reason = replay(tech, tkr, ocf_veto_exempt)
        was_f, was_c = tech.get("fund_score"), tech.get("combined_score")
        was_q, was_rec = tech.get("quality_score"), tech.get("recommendation")
        was_veto = tech.get("veto_reason")

        if fund is None:
            abstained.append(tkr)

        def moved(new, old):
            if new is None or old is None:
                return new is not old
            return abs(new - old) > TOL

        if moved(fund, was_f) or moved(combined, was_c) or quality != was_q or veto_reason != was_veto:
            changed.append((tkr, was_q, quality, was_f, fund, was_c, combined, was_veto, veto_reason))
        if rec != was_rec:
            flips.append((tkr, was_rec, rec))

    if report:
        print(f"replayed {len(tickers)} tickers from {os.path.relpath(DATA, HERE)}")
        if changed:
            print(f"\n{len(changed)} score(s) moved:")
            print(f"  {'tkr':7}{'qual':>10}{'fund':>16}{'combined':>16}{'veto':>24}")
            for tkr, q0, q1, f0, f1, c0, c1, v0, v1 in changed:
                print(f"  {tkr:7}{f'{q0}->{q1}':>10}{f'{f0}->{f1}':>16}{f'{c0}->{c1}':>16}{f'{v0}->{v1}':>24}")
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


def _prev(tickers_state=None, monitor=None, alerts_pending=None):
    """Build a prev_data blob shaped like market-data.json's top level:
    {"tickers": {t: {"technicals": {...}}}, "monitor": {...}, "alerts_pending": [...]}."""
    tickers_state = tickers_state or {}
    return {
        "tickers": {t: {"technicals": tech} for t, tech in tickers_state.items()},
        "monitor": monitor or {},
        "alerts_pending": alerts_pending or [],
    }


def _fired(alerts, ticker, trigger):
    return any(a["ticker"] == ticker and a["trigger"] == trigger for a in alerts)


def verify_monitor():
    """Regression-checks compute_exceptions, the 9-trigger monitor engine.

    **Invariant under test:** every trigger is an edge against prior state,
    never a level (a current-state predicate that keeps re-firing until the
    condition clears) — the death-cross bug, generalized, and the reason
    monitor mode exists at all. A real instance of exactly this bug class
    (trigger 8 firing as a level for ~24 runs after a big theme-score drop)
    was found once, by hand, during implementation. Nothing else in the repo
    checks for it coming back.

    Two properties, both required to pass:
    1. Round-2 fixed point: replaying the committed market-data.json against
       itself as both `prev` and `cur` (nothing changed since "last run")
       must fire 0 alerts — the single most important regression property for
       an edge-based trigger system.
    2. Per-trigger edge-vs-level: for each of the 9 triggers, a synthetic pair
       where the condition newly becomes true must fire, and a synthetic pair
       where it was ALREADY true last run (no change) must not fire again.
    """
    failures = []

    def expect(label, cond):
        if not cond:
            failures.append(label)

    # --- 1. Round-2 fixed point, against the real committed data + config ---
    data = json.load(open(DATA))
    cur_technicals = {t: (v.get("technicals") or {}) for t, v in data["tickers"].items()}
    weights = {t: v["allocation"] for t, v in data["tickers"].items() if v.get("allocation") is not None}
    themes = analyzer.load_config().get("themes", {})
    TAG = {t: name for name, tickers in themes.items() for t in tickers}
    expo = {name: sum(weights.get(t, 0) for t in tickers) for name, tickers in themes.items()}
    expo["untagged"] = sum(v for t, v in weights.items() if t not in TAG)
    today = date.fromisoformat(data["updated"][:10])
    prev_real = {"tickers": data["tickers"], "monitor": data.get("monitor") or {},
                 "alerts_pending": data.get("alerts_pending") or []}
    alerts, _ = analyzer.compute_exceptions(prev_real, cur_technicals, weights, themes, expo, today)
    expect(f"round-2 fixed point: replaying {os.path.relpath(DATA, HERE)} against "
           f"itself must fire 0 alerts, got {len(alerts)}: {alerts[:3]}", len(alerts) == 0)

    T0 = date(2026, 1, 15)
    WEIGHT = analyzer.MIN_WEIGHT + 1.0  # above the per-ticker trigger gate

    def run(old, cur, weight=WEIGHT):
        """One ticker ('T'), fresh monitor state each call."""
        alerts, _ = analyzer.compute_exceptions(_prev({"T": old}), {"T": cur},
                                                 {"T": weight}, {}, {}, T0)
        return {a["trigger"] for a in alerts if a["ticker"] == "T"}

    # 1. REC_FLIP — bucket flip beyond REC_HYSTERESIS combined points.
    lo, hi = analyzer._bucket_edges(55.0)
    c1 = hi + analyzer.REC_HYSTERESIS + 0.1
    expect("REC_FLIP edge: bucket flip beyond hysteresis must fire",
           "REC_FLIP" in run({"combined_score": 55.0}, {"combined_score": c1}))
    expect("REC_FLIP level: unchanged score must not fire",
           "REC_FLIP" not in run({"combined_score": c1}, {"combined_score": c1}))

    # 2. MA_CROSS — SMA50/SMA200 sign flip.
    expect("MA_CROSS edge: sign flip must fire",
           "MA_CROSS" in run({"sma50": 90, "sma200": 100}, {"sma50": 110, "sma200": 100}))
    expect("MA_CROSS level: same side both runs must not fire",
           "MA_CROSS" not in run({"sma50": 110, "sma200": 100}, {"sma50": 112, "sma200": 100}))

    # 4. VETO — gate reason newly fires or clears. Unconditional, no weight
    #    gate (RIVN 1.64%, ASTS 1.40% both fired real cash-burn gates and went
    #    unreported under the old per-ticker gate).
    expect("VETO edge: gate newly firing must fire",
           "VETO" in run({"combined_score": 50, "veto_reason": None},
                          {"combined_score": 50, "veto_reason": "cash_burn"}))
    expect("VETO level: same reason both runs must not fire",
           "VETO" not in run({"combined_score": 50, "veto_reason": "cash_burn"},
                              {"combined_score": 50, "veto_reason": "cash_burn"}))
    expect("VETO below weight gate: sub-MIN_WEIGHT ticker must still fire",
           "VETO" in run({"combined_score": 50, "veto_reason": None},
                          {"combined_score": 50, "veto_reason": "cash_burn"},
                          weight=0.5))

    # 6. DISTRIBUTION — rvol5 >= DISTRIB_RVOL and dir5 <= DISTRIB_DIR, newly true.
    d_rvol, d_dir = analyzer.DISTRIB_RVOL + 0.5, analyzer.DISTRIB_DIR - 1.0
    expect("DISTRIBUTION edge: newly true must fire",
           "DISTRIBUTION" in run({"rvol5": 1.0, "dir5": 0.0}, {"rvol5": d_rvol, "dir5": d_dir}))
    expect("DISTRIBUTION level: already true both runs must not fire",
           "DISTRIBUTION" not in run({"rvol5": d_rvol, "dir5": d_dir},
                                     {"rvol5": d_rvol + 0.1, "dir5": d_dir - 0.1}))

    # 7. DATA_GAP — scored last run, None this run. Unconditional, no weight gate.
    expect("DATA_GAP edge: score->None must fire",
           "DATA_GAP" in run({"combined_score": 50}, {"combined_score": None}))
    expect("DATA_GAP level: None both runs must not fire",
           "DATA_GAP" not in run({"combined_score": None}, {"combined_score": None}))

    # 3. 52W_HIGH — new extreme vs the *prior run's* range, HI_LO_SUPPRESS-day debounce.
    # Needs monitor state carried between two calls, so it doesn't use run().
    prev1 = _prev({"T": {"high_52w": 100, "low_52w": 50}})
    alerts1, mon1 = analyzer.compute_exceptions(
        prev1, {"T": {"price": 105, "high_52w": 105, "low_52w": 50}}, {"T": WEIGHT}, {}, {}, T0)
    expect("52W_HIGH edge: price through prior high must fire", _fired(alerts1, "T", "52W_HIGH"))
    prev2 = _prev({"T": {"high_52w": 105, "low_52w": 50}}, monitor=mon1)
    alerts2, _ = analyzer.compute_exceptions(
        prev2, {"T": {"price": 106, "high_52w": 106, "low_52w": 50}}, {"T": WEIGHT}, {}, {}, T0)
    expect(f"52W_HIGH level: a further marginal high within {analyzer.HI_LO_SUPPRESS}d "
           "must not re-fire (the daily-restatement debounce)", not _fired(alerts2, "T", "52W_HIGH"))

    # 5. EARNINGS — entering the 0-3 day window, keyed on (ticker, date).
    soon = (T0 + timedelta(days=2)).isoformat()
    prev1 = _prev({"T": {"combined_score": 50}})
    alerts1, mon1 = analyzer.compute_exceptions(
        prev1, {"T": {"next_earnings": {"date": soon}}}, {"T": WEIGHT}, {}, {}, T0)
    expect("EARNINGS edge: date entering the window must fire", _fired(alerts1, "T", "EARNINGS"))
    prev2 = _prev({"T": {"combined_score": 50}}, monitor=mon1)
    alerts2, _ = analyzer.compute_exceptions(
        prev2, {"T": {"next_earnings": {"date": soon}}}, {"T": WEIGHT}, {}, {}, T0)
    expect("EARNINGS level: same (ticker, date) must not re-fire", not _fired(alerts2, "T", "EARNINGS"))

    # 8. THEME_DROP — mean score <= EMA - THEME_DROP; re-baselines on fire. This is
    #    the trigger that actually regressed to a level once (see docstring).
    themes8 = {"Theme1": ["A", "B"]}
    base_ema = 70.0
    dropped = base_ema - analyzer.THEME_DROP - 1.0
    cur8 = {"A": {"combined_score": dropped}, "B": {"combined_score": dropped}}
    prev1 = {"tickers": {}, "monitor": {"theme_ema": {"Theme1": base_ema}}, "alerts_pending": []}
    alerts1, mon1 = analyzer.compute_exceptions(prev1, cur8, {}, themes8, {}, T0)
    expect("THEME_DROP edge: mean dropping below EMA must fire", _fired(alerts1, "Theme1", "THEME_DROP"))
    prev2 = {"tickers": {}, "monitor": mon1, "alerts_pending": []}
    alerts2, _ = analyzer.compute_exceptions(prev2, cur8, {}, themes8, {}, T0)
    expect("THEME_DROP level: same mean the next run must not re-fire",
           not _fired(alerts2, "Theme1", "THEME_DROP"))

    # 9. THEME_EXPO — exposure crossing EXPO_LIMIT, EXPO_HYSTERESIS band on the way back.
    themes9 = {"Theme1": ["A", "B"]}
    over = analyzer.EXPO_LIMIT + 1.0
    prev1 = {"tickers": {}, "monitor": {"theme_expo_hi": {"Theme1": False}}, "alerts_pending": []}
    alerts1, mon1 = analyzer.compute_exceptions(prev1, {}, {}, themes9, {"Theme1": over}, T0)
    expect("THEME_EXPO edge: crossing the limit must fire", _fired(alerts1, "Theme1", "THEME_EXPO"))
    prev2 = {"tickers": {}, "monitor": mon1, "alerts_pending": []}
    alerts2, _ = analyzer.compute_exceptions(prev2, {}, {}, themes9, {"Theme1": over}, T0)
    expect("THEME_EXPO level: still over the limit the next run must not re-fire",
           not _fired(alerts2, "Theme1", "THEME_EXPO"))

    if failures:
        print(f"FAIL verify-monitor: {len(failures)} check(s) failed:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(f"verify-monitor PASS: round-2 fixed point holds against the real data "
          f"({len(cur_technicals)} tickers), and all 9 triggers fire on an edge "
          f"and stay silent on a repeated level.")
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    if "--verify-monitor" in sys.argv:
        sys.exit(verify_monitor())
    changed, flips, _ = check()
    sys.exit(1 if (changed or flips) else 0)
