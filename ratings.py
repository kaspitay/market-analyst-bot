"""Fundamental/technical labels, the 3x3 action grid, and the lines that explain them.

Pure functions, no I/O. Spec: docs/superpowers/specs/2026-10-02-ratings-grid-telegram-v2-design.md
"""

EDGES = (40, 60)   # Weak/Neutral and Neutral/Strong boundaries, on the 0-100 scores
BAND = 3           # a label moves only after the score clears its edge by this much

FUND_LABELS = ("Weak", "Neutral", "Strong")
TECH_LABELS = ("Downtrend", "Neutral", "Uptrend")

# (fund index, tech index) -> action tag
ACTIONS = {
    (2, 2): "Buy", (2, 1): "Accumulate", (2, 0): "Starter",
    (1, 2): "Hold", (1, 1): "Hold", (1, 0): "Don't add",
    (0, 2): "Momentum only", (0, 1): "Avoid", (0, 0): "Sell",
}
ACTION_ORDER = ["Buy", "Accumulate", "Starter", "Hold", "Momentum only", "Don't add", "Avoid", "Sell", "No Data"]
# One-phrase gloss per tag (spec: "Detail line per tag"). Hold is glossed only in its
# Neutral/Uptrend cell; Avoid and Sell carry none.
GLOSS = {"Buy": "add", "Accumulate": "scale in", "Starter": "small, wait for trend repair",
         "Don't add": "consider trimming", "Momentum only": "tight stop"}
VETO_TEXT = {"cash_burn": "cash-burn gate", "leverage": "leverage gate", "margin_erosion": "margin-erosion gate"}


def _step(prev, score):
    """Sticky label index (0/1/2) for `score`. `prev` is last run's index or None."""
    lo, hi = EDGES
    if prev == 2:
        return 2 if score >= hi - BAND else (0 if score < lo - BAND else 1)
    if prev == 0:
        return 0 if score < lo + BAND else (2 if score >= hi + BAND else 1)
    if prev == 1:
        return 2 if score >= hi + BAND else (0 if score < lo - BAND else 1)
    return 0 if score < lo else (2 if score >= hi else 1)


def _idx(labels, name):
    return labels.index(name) if name in labels else None


def rate(tech_score, fund_score, veto_cap, prev):
    """Returns {"fund_label", "tech_label", "action"}. `prev` holds last run's labels (or {}).

    A veto cap bypasses the band: without it a name already Strong at 62 and then
    capped at 59 would stay Strong, because 59 is above the 57 exit.
    """
    f = t = None
    if fund_score is not None:
        f = _step(_idx(FUND_LABELS, prev.get("fund_label")), fund_score)
        if veto_cap is not None:
            f = min(f, 0 if veto_cap < EDGES[0] else 1)
    if tech_score is not None:
        t = _step(_idx(TECH_LABELS, prev.get("tech_label")), tech_score)
    return {
        "fund_label": None if f is None else FUND_LABELS[f],
        "tech_label": None if t is None else TECH_LABELS[t],
        "action": "No Data" if f is None or t is None else ACTIONS[(f, t)],
    }


def gloss(action, tech_label):
    """The one-phrase gloss for an action tag, or None."""
    if action == "Hold":
        return "trend intact" if tech_label == "Uptrend" else None
    return GLOSS.get(action)


def tech_drivers(t):
    """The three terms of the technical score, as numbers."""
    out = []
    if t.get("pos52") is not None:
        out.append(f"52-week range position {t['pos52']:.0f}%")
    if t.get("slope150") is not None:
        out.append(f"150-day avg {'rising' if t['slope150'] > 0 else 'falling'} {abs(t['slope150']):.1f}%/21d")
    if t.get("ppo") is not None:
        out.append(f"PPO {t['ppo']:+.1f}%")
    return out


def _fund_phrase(name, fund, sub, quality):
    if name == "health":
        return f"{quality}/{sub.get('health_known')}" if quality is not None else None
    if name == "profitability":
        parts = []
        gm, om = fund.get("grossMargins"), fund.get("operatingMargins")
        if gm is not None and om is not None:
            parts.append(f"margins {gm:.0%}/{om:.0%}")
        if fund.get("returnOnEquity") is not None:
            parts.append(f"ROE {fund['returnOnEquity']:.0%}")
        return ", ".join(parts) or None
    if name == "growth":
        parts = []
        if fund.get("revenueGrowth") is not None:
            parts.append(f"revenue {fund['revenueGrowth']:+.0%}")
        if fund.get("earningsGrowth") is not None:
            parts.append(f"earnings {fund['earningsGrowth']:+.0%}")
        return ", ".join(parts) or None
    if fund.get("trailingPE") is not None and fund["trailingPE"] > 0:
        return f"P/E {fund['trailingPE']:.1f}"
    if fund.get("priceToSales") is not None:
        return f"P/S {fund['priceToSales']:.1f}"
    return None


def fund_drivers(fund, sub, quality, veto_reason, limit=3):
    """A firing veto gate first, then the sub-scores furthest from 50, each citing its raw inputs."""
    out = [VETO_TEXT.get(veto_reason, veto_reason)] if veto_reason else []
    fund, sub = fund or {}, sub or {}
    names = [n for n in ("health", "profitability", "growth", "valuation") if sub.get(n) is not None]
    for name in sorted(names, key=lambda n: -abs(sub[n] - 50)):
        if len(out) >= limit:
            break
        tone = "strong" if sub[name] >= EDGES[1] else "weak" if sub[name] < EDGES[0] else "mixed"
        detail = _fund_phrase(name, fund, sub, quality)
        out.append(f"{name} {tone} ({detail})" if detail else f"{name} {tone}")
    return out


def reverses_if(tech_score, fund_score, labels, veto_cap):
    """The nearest score edge that would change the action tag, in words, or None."""
    f, t = _idx(FUND_LABELS, labels["fund_label"]), _idx(TECH_LABELS, labels["tech_label"])
    if f is None or t is None:
        return None
    lo, hi = EDGES
    axes = [("technical", tech_score, t, lambda i: ACTIONS[(f, i)])]
    if veto_cap is None:                      # a veto holds fundamentals down; no edge to watch
        axes.append(("fundamental", fund_score, f, lambda i: ACTIONS[(i, t)]))
    found = []
    for name, s, i, tag in axes:
        if i == 2:
            found.append((s - (hi - BAND), name, f"below {hi - BAND}", s, tag(1)))
        if i == 0:
            found.append(((lo + BAND) - s, name, f"{lo + BAND} or above", s, tag(1)))
        if i == 1:
            found.append(((hi + BAND) - s, name, f"{hi + BAND} or above", s, tag(2)))
            found.append((s - (lo - BAND), name, f"below {lo - BAND}", s, tag(0)))
    found = [x for x in found if x[4] != labels["action"]]
    if not found:
        return None
    _, name, cond, s, tag = min(found, key=lambda x: x[0])
    return f"{name} score {cond} (now {s:.0f}) -> {tag}"
