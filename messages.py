"""Telegram text, built from templates. No model and no free text: every number
comes from the data, and a missing field drops its clause instead of being filled.

Output is plain text plus <b> headers. send_telegram() escapes & < > itself, so
nothing here may html.escape (it would be escaped twice), and nothing here may emit
a bare '<' (it would open a tag in Telegram and in the dashboard's innerHTML).
"""
from ratings import ACTION_ORDER, gloss


def _action_block(a, veto, t, weight, detail=2):
    """detail 2: head + why + reverses if; 1: head + why; 0: head only (what a busy day sheds to)."""
    head = f"{a['ticker']}  {a['from']} -> {a['to']}"
    if gloss(a["to"], t.get("tech_label")):
        head += f" ({gloss(a['to'], t.get('tech_label'))})"
    if weight is not None:
        head += f"  ({weight:.1f}% of book)"
    why = [f"veto {veto['detail']}"] if veto else []
    drivers = t.get("drivers") or {}
    for axis, before, now, ds in (
        ("technical", a.get("from_tech"), t.get("tech_label"), drivers.get("tech")),
        ("fundamentals", a.get("from_fund"), t.get("fund_label"), drivers.get("fund")),
    ):
        if now is None:
            continue
        part = f"{axis} {before} -> {now}" if before and before != now else f"{axis} {now}"
        why.append(part + (": " + ", ".join(ds) if ds else ""))
    lines = [head]
    if why and detail >= 1:
        lines.append("why: " + "; ".join(why))
    if t.get("reverses_if") and detail >= 2:
        lines.append("reverses if: " + t["reverses_if"])
    return "\n".join(lines)


def _changes(alerts, technicals, weights, detail=2):
    """(blocks, lines): one block per ACTION_CHANGE (a VETO on the same ticker folds into it),
    then one line per other alert, with isolated DATA_GAPs collapsed into a single line."""
    by_ticker = {}
    for a in alerts:
        by_ticker.setdefault(a["ticker"], {})[a["trigger"]] = a
    acted = [t for t, d in by_ticker.items() if "ACTION_CHANGE" in d]
    blocks = [_action_block(by_ticker[t]["ACTION_CHANGE"], by_ticker[t].get("VETO"),
                            technicals.get(t) or {}, weights.get(t), detail)
              for t in sorted(acted, key=lambda t: -weights.get(t, 0))]
    lines, gaps = [], []
    for a in alerts:
        if a["trigger"] == "ACTION_CHANGE" or (a["trigger"] == "VETO" and a["ticker"] in acted):
            continue
        if a["trigger"] == "DATA_GAP":
            gaps.append(a["ticker"])
        else:
            lines.append(f"{a['ticker']}  {a['trigger']}: {a['detail']}")
    if gaps:
        lines.append("DATA_GAP: " + ", ".join(gaps))
    return blocks, lines


def _join(blocks, lines):
    return "\n\n".join(blocks + (["\n".join(lines)] if lines else []))


def alerts_message(alerts, technicals, weights, today, budget=4000):
    """One message. This one has a single <b> header, so send_telegram cannot split it and
    a message over the limit is rejected: a busy day sheds the reverses-if lines, then the
    why lines, and keeps every head line (the part that is the signal)."""
    for detail in (2, 1, 0):
        blocks, lines = _changes(alerts, technicals, weights, detail)
        n = len(blocks) + len(lines)
        text = f"<b>MONITOR</b> {today.isoformat()}  {n} update{'s' if n != 1 else ''}\n\n" + _join(blocks, lines)
        if len(text) <= budget:
            break
    return text


def _book(tickers, technicals, weights, counts_only=False):
    groups = {}
    for t in tickers:
        groups.setdefault((technicals.get(t) or {}).get("action") or "No Data", []).append(t)
    rows = []
    for tag in ACTION_ORDER:
        names = sorted(groups.get(tag, []), key=lambda t: -(weights or {}).get(t, 0))
        if not names:
            continue
        if counts_only:
            rows.append(f"{tag} {len(names)}")
        elif weights:
            rows.append(f"{tag} ({len(names)}): " + ", ".join(f"{t} {weights.get(t, 0):.1f}%" for t in names))
        else:
            rows.append(f"{tag} ({len(names)}): " + ", ".join(names))
    return ", ".join(rows) if counts_only else "\n".join(rows)


def _market(ind):
    ind = ind or {}
    parts = []
    for key, name in (("sp500", "S&P 500"), ("nasdaq", "NASDAQ")):
        i = (ind.get("indices") or {}).get(key)
        if i and i.get("price") is not None:
            s = f"{name} {i['price']:,.0f}"
            if i.get("changesPercentage") is not None:
                s += f" ({i['changesPercentage']:+.2f}%)"
            parts.append(s)
    if (ind.get("vix") or {}).get("price") is not None:
        parts.append(f"VIX {ind['vix']['price']:.1f}")
    if (ind.get("fear_greed") or {}).get("now") is not None:
        parts.append(f"Fear & Greed {ind['fear_greed']['now']}")
    return " | ".join(parts)


def sunday_digest(alerts, technicals, weights, portfolio, watchlist, indicators, earnings,
                  expo, today, expo_limit, budget=4000):
    """The whole Sunday message. Over budget, it sheds in order: watchlist names -> counts,
    the calendar, the market line, the reverses-if lines, the why lines. 4000 is
    send_telegram's own split point."""
    calendar = [c for c in (indicators or {}).get("calendar") or [] if c.get("date", "") >= today.isoformat()][:3]
    themes = sorted(((n, v) for n, v in expo.items() if n != "untagged" and v > 0), key=lambda x: -x[1])

    def build(stage):
        blocks, lines = _changes(alerts, technicals, weights, detail=max(0, 2 - max(0, stage - 3)))
        s = [("WEEKLY BOOK", None, today.isoformat()),
             ("CHANGES", _join(blocks, lines) or "none since the last run", None),
             (f"PORTFOLIO ({len(portfolio)})", _book(portfolio, technicals, weights), None),
             (f"WATCHLIST ({len(watchlist)})", _book(watchlist, technicals, None, counts_only=stage >= 1), None)]
        if stage < 3 and _market(indicators):
            s.append(("MARKET", _market(indicators), None))
        s.append(("EARNINGS (14d)", ", ".join(f"{e['symbol']} {e['date'][5:]}" for e in earnings) or "none", None))
        if stage < 2 and calendar:
            s.append(("CALENDAR", "\n".join(f"{c['date'][5:]} {c['title']}" for c in calendar), None))
        if themes:
            s.append(("THEMES", ", ".join(f"{n} {v:.1f}%" + (" (over limit)" if v >= expo_limit else "")
                                          for n, v in themes), None))
        return "\n\n".join(f"<b>{t}</b>" + (f" {tail}" if tail else "") + (f"\n{body}" if body else "")
                           for t, body, tail in s)

    for stage in range(6):
        text = build(stage)
        if len(text) <= budget:
            break
    return text
