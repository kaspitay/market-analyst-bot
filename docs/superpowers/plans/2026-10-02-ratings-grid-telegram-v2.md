# Ratings Grid and Telegram v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rate fundamentals and technicals separately, name an action from the pair on a 3×3 grid, alert on action changes, and build every Telegram message from code templates instead of the model.

**Architecture:** Two new pure modules, `ratings.py` (sticky labels, action table, driver lines, "what would change this") and `messages.py` (alert and Sunday-digest templates). `analyzer.py` calls `ratings` from `merge_fundamentals`, swaps the `REC_FLIP` trigger for `ACTION_CHANGE`, and sends `messages` output instead of a Gemini-written briefing. The blended `combined_score` and `recommendation` stay in `market-data.json` untouched. All tests are `check_scores.py` modes, the repo's existing test runner.

**Tech Stack:** Python 3.12 stdlib only (no new dependency), `check_scores.py` as the test runner, plain HTML/JS in `docs/index.html`.

**Spec:** `docs/superpowers/specs/2026-10-02-ratings-grid-telegram-v2-design.md` (read it first; this plan implements it).

## Global Constraints

- Work in the worktree `.claude/worktrees/ratings-grid` on branch `feat/ratings-grid-telegram-v2` (cut from `main` at `ed0c8ff`). Run every command from that directory.
- **Never push, open a PR, or merge.** Commit locally only. The owner opens and merges the PR in the browser.
- Stage named files only (never `git add -A`), read `git diff --cached --stat` before each commit.
- Every commit message ends with these two trailer lines, after a blank line:
  `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01QptdVhUYfeyc8YZR1PhJ69`
- Label edges `(40, 60)`, band `3`: Strong/Uptrend enters at ≥63 and is kept until <57; Weak/Downtrend enters at <37 and is kept until ≥43.
- Nine action tags, exactly: Buy, Accumulate, Starter, Hold, Hold, Don't add, Momentum only, Avoid, Sell (grid below). No Data when either score is missing.
- A veto cap bypasses the band: cap 39 forces fundamentals Weak, cap 59 forces at most Neutral.
- The model leaves the Telegram path: no Gemini call. No icons in new messages. Templates never `html.escape` (`send_telegram` already escapes) and never emit a bare `<`.
- Sunday digest ≤ 4,096 characters per the spec; the code budget is **4,000** (`send_telegram`'s own split point).
- `combined_score`, `recommendation`, `THEME_DROP`, `DATA_GAP` and the `check_scores.py` gate keep working unchanged: `python3 check_scores.py` must keep reporting `changed=0  flips=0`.
- Personal project: no Jira. Do not touch `config.json`, `docs/data/*` or `.github/workflows` beyond what a task names.

Grid (fund rows × tech columns):

| | Uptrend | Neutral | Downtrend |
|---|---|---|---|
| **Strong** | Buy | Accumulate | Starter |
| **Neutral** | Hold | Hold | Don't add |
| **Weak** | Momentum only | Avoid | Sell |

## Review Focus

Failure modes the spec implies that a user would hit, most likely first. Each has a test in the task named.

1. **First run after merge** has no previous `action` on any ticker; it must fire zero `ACTION_CHANGE` (no alert storm). Task 3 test; Task 6 dry run.
2. **A name already Strong whose veto cap lands at 59** must drop to Neutral, not stay Strong because 59 is above the 57 exit. Task 1 test.
3. **A ticker with no technicals or no fundamentals** (fetch failed, or absent from the dict) must land under "No Data" in the digest and never raise. Task 4 test.
4. **A replayed alert from `alerts_pending`** (an old-format `REC_FLIP`, or an `ACTION_CHANGE` whose ticker has no technicals today) must still render. Task 4 test.
5. **A digest over budget** must shed sections rather than overflow Telegram's limit. Task 4 test.

## File Structure

| File | Responsibility |
|---|---|
| `ratings.py` (new) | `rate`, `tech_drivers`, `fund_drivers`, `reverses_if`; constants `EDGES`, `BAND`, `ACTIONS`, `ACTION_ORDER`. Pure, no I/O. |
| `messages.py` (new) | `alerts_message`, `sunday_digest`. Pure string building from data. |
| `analyzer.py` | Store `slope150`; return sub-scores; compute labels in `merge_fundamentals`; `ACTION_CHANGE`; `briefing_text`; delete `build_prompt`, `analyze`, `fetch_market_news`, `_bucket_edges`, `REC_HYSTERESIS`. |
| `check_scores.py` | `--verify-ratings`, `--verify-messages`, `--update-golden`; update `--verify-monitor` and `--verify-workflow`. |
| `golden/alerts.txt`, `golden/sunday.txt` (new) | Expected rendered text, reviewed by a human. |
| `docs/index.html` | Action column, label chips, drivers and "reverses if" in the detail panel. |
| `README.md`, `docs/USAGE-GUIDE.md` | Describe the grid, drop the Gemini sections. |
| `.github/workflows/market-analysis.yml` | Drop `GEMINI_API_KEY`. |

---

### Task 1: `ratings.py` and `--verify-ratings`

**Files:**
- Create: `ratings.py`
- Modify: `check_scores.py` (imports, new `verify_ratings`, `__main__`, module docstring)

**Interfaces:**
- Produces (Tasks 2-4 rely on these exact names):
  - `ratings.rate(tech_score, fund_score, veto_cap, prev) -> {"fund_label", "tech_label", "action"}`; `prev` is a dict holding last run's `fund_label`/`tech_label`, or `{}`.
  - `ratings.tech_drivers(t) -> list[str]` where `t` has `pos52`, `slope150`, `ppo`.
  - `ratings.fund_drivers(fund, sub, quality, veto_reason, limit=3) -> list[str]`.
  - `ratings.reverses_if(tech_score, fund_score, labels, veto_cap) -> str | None`; `labels` is a dict with `fund_label`, `tech_label`, `action`.
  - `ratings.ACTIONS` (dict `(fund_idx, tech_idx) -> tag`), `ratings.ACTION_ORDER` (list, ends with `"No Data"`), `ratings.FUND_LABELS`, `ratings.TECH_LABELS`.

- [ ] **Step 1: Write the failing test**

In `check_scores.py`, add `import ratings  # noqa: E402` on the line after `import analyzer  # noqa: E402`. Add `import json` is already there. Insert this function immediately before the line `if __name__ == "__main__":`:

```python
def verify_ratings():
    """ratings.py: sticky 3-point labels, the veto override, the nine-cell table, and the
    lines that explain a rating. Pure arithmetic over dicts, so no network and no fixtures."""
    failures = []

    def expect(label, cond):
        if not cond:
            failures.append(label)

    def rate(tech, fund, prev=None, cap=None):
        return ratings.rate(tech, fund, cap, prev or {})

    strong_up = {"fund_label": "Strong", "tech_label": "Uptrend"}
    neutral = {"fund_label": "Neutral", "tech_label": "Neutral"}
    weak_down = {"fund_label": "Weak", "tech_label": "Downtrend"}

    # 1. The nine cells with no history: plain 40/60 edges.
    level = {"Weak": 20, "Neutral": 50, "Strong": 80}
    trend = {"Downtrend": 20, "Neutral": 50, "Uptrend": 80}
    cells = {("Strong", "Uptrend"): "Buy", ("Strong", "Neutral"): "Accumulate",
             ("Strong", "Downtrend"): "Starter", ("Neutral", "Uptrend"): "Hold",
             ("Neutral", "Neutral"): "Hold", ("Neutral", "Downtrend"): "Don't add",
             ("Weak", "Uptrend"): "Momentum only", ("Weak", "Neutral"): "Avoid",
             ("Weak", "Downtrend"): "Sell"}
    for (f, t), tag in cells.items():
        got = rate(trend[t], level[f])
        expect(f"{f}/{t} must read {tag}; got {got}",
               got == {"fund_label": f, "tech_label": t, "action": tag})
    expect("with no history exactly 60 is Strong, exactly 40 is Neutral, 39.9 is Weak",
           rate(60, 60)["fund_label"] == "Strong" and rate(40, 40)["tech_label"] == "Neutral"
           and rate(39.9, 39.9)["fund_label"] == "Weak")

    # 2. Labels are sticky: each edge needs the 3-point band, in both directions.
    expect("Neutral stays Neutral at 62.9", rate(62.9, 62.9, neutral)["fund_label"] == "Neutral")
    expect("Neutral becomes Strong at 63", rate(63, 63, neutral)["fund_label"] == "Strong")
    expect("Strong stays Strong at 57", rate(57, 57, strong_up)["fund_label"] == "Strong")
    expect("Strong becomes Neutral at 56.9", rate(56.9, 56.9, strong_up)["fund_label"] == "Neutral")
    expect("Neutral stays Neutral at 37", rate(37, 37, neutral)["tech_label"] == "Neutral")
    below = rate(36.9, 36.9, neutral)
    expect("Neutral becomes Weak / Downtrend below 37",
           below["fund_label"] == "Weak" and below["tech_label"] == "Downtrend")
    expect("Weak stays Weak at 42.9", rate(42.9, 42.9, weak_down)["fund_label"] == "Weak")
    expect("Weak becomes Neutral at 43", rate(43, 43, weak_down)["fund_label"] == "Neutral")
    expect("Strong can fall straight to Weak", rate(30, 30, strong_up)["action"] == "Sell")
    expect("Weak can jump straight to Strong", rate(70, 70, weak_down)["action"] == "Buy")

    # 3. A veto cap bypasses the band (59 is above the 57 exit, so without this a
    #    name already Strong would stay Strong under a Hold-ceiling gate).
    expect("cap 59 pulls a Strong name to Neutral",
           rate(80, 59, strong_up, cap=59)["fund_label"] == "Neutral")
    expect("cap 39 pulls a Neutral name to Weak",
           rate(80, 39, neutral, cap=39)["fund_label"] == "Weak")
    expect("a cap above the label changes nothing", rate(80, 30, None, cap=59)["fund_label"] == "Weak")
    expect("a cap leaves the technical label alone",
           rate(80, 59, strong_up, cap=59)["tech_label"] == "Uptrend")

    # 4. A missing score abstains instead of reading as neutral.
    expect("no fundamentals -> No Data, technical label kept",
           rate(70, None) == {"fund_label": None, "tech_label": "Uptrend", "action": "No Data"})
    expect("no technicals -> No Data",
           rate(None, 70) == {"fund_label": "Strong", "tech_label": None, "action": "No Data"})
    expect("neither score -> No Data", rate(None, None)["action"] == "No Data")

    # 5. reverses_if names the nearest edge that changes the tag.
    def why(tech, fund, prev=None, cap=None):
        return ratings.reverses_if(tech, fund, rate(tech, fund, prev, cap), cap)
    expect("Accumulate: technical back to 63 gives Buy",
           why(52, 70, strong_up) == "technical score 63 or above (now 52) -> Buy")
    expect("Sell: technical back to 43 gives Avoid (a tie goes to the technical axis)",
           why(30, 30) == "technical score 43 or above (now 30) -> Avoid")
    expect("Hold skips edges that would keep the same tag",
           why(50, 50) == "technical score below 37 (now 50) -> Don't add")
    expect("a veto removes the fundamental axis", why(70, 59, cap=59) is None)
    expect("No Data has nothing to reverse", why(70, None) is None)

    # 6. Drivers cite raw fields and drop what is missing.
    expect("tech drivers cite the three score terms",
           ratings.tech_drivers({"pos52": 55.0, "slope150": -0.4, "ppo": -0.4})
           == ["52-week range position 55%", "150-day avg falling 0.4%/21d", "PPO -0.4%"])
    expect("tech drivers skip missing terms", ratings.tech_drivers({"ppo": 1.26}) == ["PPO +1.3%"])
    sub = {"valuation": 40, "profitability": 80, "growth": 55, "health": 100, "health_known": 8}
    fund = {"grossMargins": 0.61, "operatingMargins": 0.38, "returnOnEquity": 0.24, "trailingPE": 31.2}
    expect("fund drivers: the three sub-scores furthest from 50, each citing its inputs",
           ratings.fund_drivers(fund, sub, 8, None)
           == ["health strong (8/8)", "profitability strong (margins 61%/38%, ROE 24%)",
               "valuation mixed (P/E 31.2)"])
    expect("a firing gate comes first and counts toward the limit of 3",
           ratings.fund_drivers({}, {"valuation": 10, "growth": 50}, None, "cash_burn")
           == ["cash-burn gate", "valuation weak", "growth mixed"])

    # 7. Every stored ticker rates without raising, and abstains only where a score is missing.
    tickers = json.load(open(DATA))["tickers"]
    exempt = analyzer.load_config().get("ocf_veto_exempt", [])
    rated = abstained = 0
    for tkr, v in tickers.items():
        tech = (v or {}).get("technicals") or {}
        fund = tech.get("fundamentals") or {}
        _, cap = analyzer.veto_gates(fund, tech.get("financialHistory"), tkr, exempt)
        got = ratings.rate(tech.get("tech_score"), tech.get("fund_score"), cap, {})
        lacks = tech.get("tech_score") is None or tech.get("fund_score") is None
        abstained += lacks
        rated += not lacks
        expect(f"{tkr}: action is No Data exactly when a score is missing; got {got}",
               (got["action"] == "No Data") == lacks)
        ratings.tech_drivers(tech)
        ratings.fund_drivers(fund, tech.get("fund_subscores"), tech.get("quality_score"),
                             tech.get("veto_reason"))

    if failures:
        print(f"FAIL verify-ratings: {len(failures)} check(s) failed:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"verify-ratings PASS: nine cells, sticky 3-point labels, veto override, driver lines; "
          f"{rated} stored tickers rated, {abstained} abstained.")
    return 0
```

In the `if __name__ == "__main__":` block add, above the `--verify-monitor` line:

```python
    if "--verify-ratings" in sys.argv:
        sys.exit(verify_ratings())
```

In the module docstring's flag list add the line `    python3 check_scores.py --verify-ratings  # labels, grid, veto override, driver lines`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 check_scores.py --verify-ratings`
Expected: FAIL with `ModuleNotFoundError: No module named 'ratings'`.

- [ ] **Step 3: Write minimal implementation**

Create `ratings.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 check_scores.py --verify-ratings`
Expected: `verify-ratings PASS: nine cells, sticky 3-point labels, veto override, driver lines; N stored tickers rated, M abstained.` and exit code 0.

- [ ] **Step 5: Commit**

```bash
git add ratings.py check_scores.py
git diff --cached --stat
git commit -m "feat: add ratings module (sticky labels, 3x3 action grid, drivers)"
```

(Add the two trailer lines from Global Constraints after a blank line. Same for every commit below.)

---

### Task 2: Wire ratings into the pipeline

**Files:**
- Modify: `analyzer.py` (import, `fetch_technicals` return dict, `compute_fundamental_score`, `merge_fundamentals`, `main`)
- Modify: `check_scores.py` (`replay` unpack, `verify_ratings` section 8)

**Interfaces:**
- Consumes: Task 1's `ratings.rate`, `ratings.tech_drivers`, `ratings.fund_drivers`, `ratings.reverses_if`.
- Produces:
  - `compute_fundamental_score(...)` now returns a **5-tuple** `(score, reasons, quality, quality_details, subscores)`. `subscores` is `{"valuation", "profitability", "growth", "health", "health_known"}` (integers or `None`; `{}` when there are no fundamentals).
  - `merge_fundamentals(technicals, fund_data, financial_history=None, ticker=None, ocf_veto_exempt=None, prev=None)`; `prev` is the previous run's `technicals` dict for that ticker (or `None`). It adds to `technicals`: `fund_subscores`, `fund_label`, `tech_label`, `action`, `drivers` (`{"tech": [...], "fund": [...]}`), `reverses_if`.
  - `fetch_technicals` returns an extra key `slope150` (float rounded to 2 dp, or `None`).

- [ ] **Step 1: Write the failing test**

In `verify_ratings`, insert this block immediately before `if failures:` (it is section 8):

```python
    # 8. merge_fundamentals wires it up: fields stored, last run's labels honoured, blend untouched.
    fund_data = {"fundamentals": {"sector": "Technology", "trailingPE": 25.0, "priceToSales": 5.0,
                                  "pegRatio": 1.2, "priceToBook": 4.0, "grossMargins": 0.6,
                                  "operatingMargins": 0.3, "returnOnEquity": 0.25, "revenueGrowth": 0.2,
                                  "earningsGrowth": 0.2, "currentYearGrowth": 0.2,
                                  "operatingCashflow": 1e9, "totalRevenue": 5e9, "totalDebt": 1e8,
                                  "totalCash": 5e8},
                 "price_target": {}, "earnings": {}}

    def merged(score, prev=None):
        t = {"score": score, "price": 100.0, "pos52": 55.0, "slope150": -0.4, "ppo": -0.4,
             "score_reasons": []}
        return analyzer.merge_fundamentals(t, fund_data, None, "T", [], prev=prev)

    m = merged(41.0)
    expect("merge: stores labels, action, drivers, reverses_if and the sub-scores",
           all(k in m for k in ("fund_label", "tech_label", "action", "drivers", "reverses_if",
                                "fund_subscores"))
           and m["action"] == ratings.rate(m["tech_score"], m["fund_score"], None, {})["action"]
           and m["drivers"]["tech"] == ["52-week range position 55%", "150-day avg falling 0.4%/21d",
                                        "PPO -0.4%"]
           and m["fund_subscores"]["profitability"] is not None)
    expect("merge: with no history a 41 reads Neutral technically", m["tech_label"] == "Neutral")
    expect("merge: last run's Downtrend holds a 41 (it needs 43 to leave)",
           merged(41.0, {"fund_label": "Strong", "tech_label": "Downtrend"})["tech_label"] == "Downtrend")
    expect("merge: the blended score and recommendation are untouched",
           m["combined_score"] is not None
           and m["recommendation"] in ("Strong Buy", "Buy", "Hold", "Sell", "Strong Sell"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 check_scores.py --verify-ratings`
Expected: FAIL with `TypeError: merge_fundamentals() got an unexpected keyword argument 'prev'`.

- [ ] **Step 3: Write minimal implementation**

1. `analyzer.py`: add `import ratings` on the line after `import requests`.

2. `fetch_technicals`: in the returned dict, change
```python
            "ppo": ppo, "pos52": round(pos52, 1) if pos52 is not None else None,
```
to
```python
            "ppo": ppo, "pos52": round(pos52, 1) if pos52 is not None else None,
            "slope150": round(slope150, 2) if slope150 is not None else None,
```

3. `compute_fundamental_score`:
   - In the docstring, replace `Returns (score, reasons, quality, quality_details) — quality is the raw` with `Returns (score, reasons, quality, quality_details, subscores) — quality is the raw`, and replace `Returns (None, [], None, []) when` with `Returns (None, [], None, [], {}) when`.
   - Change `        return None, [], None, []` to `        return None, [], None, [], {}`.
   - Replace the last line `    return round(fund_total, 1), reasons, quality, quality_details` with:
```python
    # The four legs, for the explain line. health_known is the F-score denominator.
    subscores = {"valuation": round(val_score), "profitability": round(prof_score),
                 "growth": round(grow_score), "health": health_score, "health_known": len(known)}
    return round(fund_total, 1), reasons, quality, quality_details, subscores
```

4. `merge_fundamentals`:
   - Signature: `def merge_fundamentals(technicals, fund_data, financial_history=None, ticker=None, ocf_veto_exempt=None, prev=None):`
   - Replace `fund_score, fund_reasons, quality, quality_details = compute_fundamental_score(` with `fund_score, fund_reasons, quality, quality_details, subscores = compute_fundamental_score(`.
   - After the line `technicals["quality_details"] = quality_details` add `technicals["fund_subscores"] = subscores`.
   - Immediately before the comment `    # Merge reasons`, insert:
```python
    # Separate fundamental and technical labels and the action they name. fund_score is
    # already capped above; the cap also bypasses the label band (see ratings.rate).
    technicals.update(ratings.rate(tech_score, fund_score, veto_cap, prev or {}))
    technicals["drivers"] = {
        "tech": ratings.tech_drivers(technicals),
        "fund": ratings.fund_drivers(fund, subscores, quality, veto_reason),
    }
    technicals["reverses_if"] = ratings.reverses_if(tech_score, fund_score, technicals, veto_cap)

```

5. `main()`: replace
```python
        technicals[ticker] = merge_fundamentals(
            technicals[ticker], fund_data, fh, ticker, ocf_veto_exempt
        )
```
with
```python
        technicals[ticker] = merge_fundamentals(
            technicals[ticker], fund_data, fh, ticker, ocf_veto_exempt,
            prev=((prev_data.get("tickers") or {}).get(ticker) or {}).get("technicals"),
        )
```

6. `check_scores.py` `replay`: change `fund_score, _, quality, _ = analyzer.compute_fundamental_score(` to `fund_score, _, quality, _, _ = analyzer.compute_fundamental_score(`.

- [ ] **Step 4: Run tests to verify they pass**

Run each; all must pass:
```bash
python3 check_scores.py --verify-ratings
python3 check_scores.py
python3 check_scores.py --self-test
python3 check_scores.py --verify-monitor
```
Expected: `verify-ratings PASS`; the plain gate ends `changed=0  flips=0  abstained=<n>` with exit 0 (the blend is untouched); `self-test PASS`; `verify-monitor PASS`.

- [ ] **Step 5: Commit**

```bash
git add analyzer.py check_scores.py
git diff --cached --stat
git commit -m "feat: compute fund/tech labels, action, drivers and reverses_if per ticker"
```

---

### Task 3: `ACTION_CHANGE` replaces `REC_FLIP`

**Files:**
- Modify: `analyzer.py` (`fire`, trigger 1 in `compute_exceptions`; delete `_bucket_edges`, `REC_HYSTERESIS`)
- Modify: `check_scores.py` (`verify_monitor` trigger-1 block)

**Interfaces:**
- Consumes: the per-ticker `action`, `fund_label`, `tech_label` stored by Task 2.
- Produces: alerts of the form `{"ticker", "trigger": "ACTION_CHANGE", "detail": "Buy -> Starter", "from": "Buy", "to": "Starter", "from_fund": "Strong", "from_tech": "Uptrend"}` (Task 4 renders these; they are persisted in `alerts_pending`).

- [ ] **Step 1: Write the failing test**

In `verify_monitor`, replace this whole block:

```python
    # 1. REC_FLIP — bucket flip beyond REC_HYSTERESIS combined points.
    lo, hi = analyzer._bucket_edges(55.0)
    c1 = hi + analyzer.REC_HYSTERESIS + 0.1
    expect("REC_FLIP edge: bucket flip beyond hysteresis must fire",
           "REC_FLIP" in run({"combined_score": 55.0}, {"combined_score": c1}))
    expect("REC_FLIP level: unchanged score must not fire",
           "REC_FLIP" not in run({"combined_score": c1}, {"combined_score": c1}))
```

with:

```python
    # 1. ACTION_CHANGE — the action tag changed. The labels are damped upstream
    #    (ratings.rate), so there is no band here.
    expect("ACTION_CHANGE edge: a tag change must fire",
           "ACTION_CHANGE" in run({"action": "Buy"}, {"action": "Accumulate"}))
    expect("ACTION_CHANGE level: the same tag must not fire",
           "ACTION_CHANGE" not in run({"action": "Buy"}, {"action": "Buy"}))
    expect("ACTION_CHANGE: Hold in two different cells is silent (same tag)",
           "ACTION_CHANGE" not in run({"action": "Hold", "tech_label": "Uptrend"},
                                      {"action": "Hold", "tech_label": "Neutral"}))
    expect("ACTION_CHANGE: no previous action (first run after the grid ships) baselines silently",
           "ACTION_CHANGE" not in run({"combined_score": 50}, {"action": "Buy"}))
    expect("ACTION_CHANGE: to or from No Data is DATA_GAP's job, not this trigger's",
           "ACTION_CHANGE" not in run({"action": "Buy"}, {"action": "No Data"})
           and "ACTION_CHANGE" not in run({"action": "No Data"}, {"action": "Buy"}))
    expect("ACTION_CHANGE below the weight gate must not fire",
           "ACTION_CHANGE" not in run({"action": "Buy"}, {"action": "Sell"}, weight=0.5))
    got, _ = analyzer.compute_exceptions(
        _prev({"T": {"action": "Buy", "fund_label": "Strong", "tech_label": "Uptrend"}}),
        {"T": {"action": "Starter", "fund_label": "Strong", "tech_label": "Downtrend"}},
        {"T": WEIGHT}, {}, {}, T0)
    carried = next((a for a in got if a["trigger"] == "ACTION_CHANGE"), {})
    expect(f"ACTION_CHANGE carries what a replayed alert needs; got {carried}",
           carried.get("from") == "Buy" and carried.get("to") == "Starter"
           and carried.get("from_fund") == "Strong" and carried.get("from_tech") == "Uptrend"
           and carried.get("detail") == "Buy -> Starter")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 check_scores.py --verify-monitor`
Expected: FAIL listing the ACTION_CHANGE checks (the edge check and the carried-fields check) — the trigger does not exist yet.

- [ ] **Step 3: Write minimal implementation**

In `analyzer.py`:

1. Delete the line `REC_HYSTERESIS = 3.0    # 1: a bucket flip under 3 combined points is noise (526 raw -> 84)`.
2. Delete the whole `_bucket_edges` function (the 6 lines from `def _bucket_edges(score):` through its `return (...)`, plus the blank lines after it).
3. In `compute_exceptions`, change `fire`:
```python
    def fire(name, trigger, detail, **extra):
        alerts.append({"ticker": name, "trigger": trigger, "detail": detail, **extra})
```
4. Replace the whole trigger-1 block (from the comment line `        # 1. Recommendation bucket flip.` down to and including the `fire(ticker, "REC_FLIP", ...)` call, which ends with `(combined {c0} -> {c1})")`) with:
```python
        # 1. Action tag change. ratings.rate already damped each label by 3 points, so
        #    there is no band here. Hold in two different cells is the same tag and
        #    stays silent; a missing action (first run after the grid shipped, or No
        #    Data) baselines silently — DATA_GAP owns the No Data case.
        a0, a1 = old.get("action"), cur.get("action")
        if a0 not in (None, "No Data") and a1 not in (None, "No Data") and a0 != a1:
            fire(ticker, "ACTION_CHANGE", f"{a0} -> {a1}",
                 **{"from": a0, "to": a1, "from_fund": old.get("fund_label"),
                    "from_tech": old.get("tech_label")})
```
   Leave the `# 2. SMA50/SMA200 sign flip.` block and everything after it unchanged. `c0` and `c1` are still used by triggers 7 and 4 above it.

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 check_scores.py --verify-monitor
python3 check_scores.py
python3 check_scores.py --verify-ratings
python3 -m py_compile analyzer.py check_scores.py
grep -n "_bucket_edges\|REC_HYSTERESIS\|REC_FLIP" analyzer.py check_scores.py
```
Expected: both verifiers PASS (the round-2 fixed point still fires 0 alerts: the stored data has no `action`, so every ticker baselines); the gate reports `changed=0  flips=0`; the grep prints nothing.

- [ ] **Step 5: Commit**

```bash
git add analyzer.py check_scores.py
git diff --cached --stat
git commit -m "feat: alert on action-tag changes instead of blended bucket flips"
```

---

### Task 4: `messages.py`, golden files, `--verify-messages`

**Files:**
- Create: `messages.py`, `golden/alerts.txt`, `golden/sunday.txt`
- Modify: `check_scores.py` (imports, fixtures, `verify_messages`, `__main__`, docstring), `analyzer.py` (`outage_line` loses its emoji)

**Interfaces:**
- Consumes: Task 3's alert dicts; Task 2's per-ticker fields (`action`, `fund_label`, `tech_label`, `drivers`, `reverses_if`); `ratings.ACTION_ORDER`.
- Produces:
  - `messages.alerts_message(alerts, technicals, weights, today) -> str` where `alerts` is a list of alert dicts, `technicals` a dict `ticker -> technicals dict`, `weights` a dict `ticker -> percent`, `today` a `datetime.date`.
  - `messages.sunday_digest(alerts, technicals, weights, portfolio, watchlist, indicators, earnings, expo, today, expo_limit, budget=4000) -> str`; `portfolio` and `watchlist` are lists of tickers, `indicators` and `earnings` are the dicts/lists `main()` already builds, `expo` is `theme -> percent`.

- [ ] **Step 1: Write the failing test**

In `check_scores.py` add `import messages  # noqa: E402` after the `import ratings` line. Insert the following immediately before `def verify_ratings():`:

```python
GOLDEN = os.path.join(HERE, "golden")

# Two fundamental profiles for the message fixtures. The numbers are arbitrary but
# internally consistent: the sub-scores are what compute_fundamental_score would return.
SOUND = {"fundamentals": {"grossMargins": 0.61, "operatingMargins": 0.38, "returnOnEquity": 0.24,
                          "revenueGrowth": 0.12, "earningsGrowth": 0.20, "trailingPE": 31.2},
         "fund_subscores": {"valuation": 40, "profitability": 80, "growth": 55, "health": 100,
                            "health_known": 8},
         "quality_score": 8}
BURNING = {"fundamentals": {"grossMargins": 0.25, "operatingMargins": -0.05, "returnOnEquity": -0.10,
                            "revenueGrowth": 0.35, "priceToSales": 12.0},
           "fund_subscores": {"valuation": 20, "profitability": 30, "growth": 60, "health": 25,
                              "health_known": 8},
           "quality_score": 2}


def _tk(tech, fund, prev, profile, veto=None, cap=None, pos52=55.0, slope150=-0.4, ppo=-0.4):
    """A ticker's technicals as merge_fundamentals leaves them."""
    t = {"tech_score": tech, "fund_score": fund, "pos52": pos52, "slope150": slope150, "ppo": ppo,
         "veto_reason": veto, **profile}
    labels = ratings.rate(tech, fund, cap, prev)
    t.update(labels)
    t["drivers"] = {"tech": ratings.tech_drivers(t),
                    "fund": ratings.fund_drivers(t["fundamentals"], t["fund_subscores"],
                                                 t["quality_score"], veto)}
    t["reverses_if"] = ratings.reverses_if(tech, fund, labels, cap)
    return t


def _with_labels(tech, ticker, exempt):
    """A stored ticker's technicals plus the labels a run would add (no history)."""
    tech = dict(tech)
    _, cap = analyzer.veto_gates(tech.get("fundamentals") or {}, tech.get("financialHistory"),
                                 ticker, exempt)
    tech.update(ratings.rate(tech.get("tech_score"), tech.get("fund_score"), cap, {}))
    return tech


def verify_messages(update=False):
    """messages.py: golden text for the alerts and the Sunday digest, the 4,000-character
    budget on the real book, and the inputs that must not crash a render.

    `update=True` (--update-golden) rewrites the golden files instead of comparing; read
    the diff before committing them."""
    failures = []
    today = date(2026, 10, 4)

    def expect(label, cond):
        if not cond:
            failures.append(label)

    def golden(name, text):
        path = os.path.join(GOLDEN, name)
        if update:
            os.makedirs(GOLDEN, exist_ok=True)
            open(path, "w").write(text + "\n")
        elif not os.path.exists(path) or open(path).read() != text + "\n":
            failures.append(f"golden/{name} differs from the rendered text; read the diff, "
                            f"then rerun with --update-golden")

    def plain(text):
        """What is left after the <b> headers: no bare '<' may reach Telegram or innerHTML."""
        return re.sub(r"</?b>", "", text)

    strong_up = {"fund_label": "Strong", "tech_label": "Uptrend"}
    neutral = {"fund_label": "Neutral", "tech_label": "Neutral"}
    T = {"NBIS": _tk(52, 70, strong_up, SOUND),
         "IREN": _tk(30, 30, neutral, BURNING, "cash_burn", 39, pos52=18.0, slope150=-6.1, ppo=-2.3),
         "MSFT": _tk(66, 72, {}, SOUND), "KO": _tk(50, 50, {}, SOUND),
         "INTU": _tk(40, 80, {}, SOUND), "ANET": _tk(80, 80, {}, SOUND),
         "PLTR": _tk(80, 80, {}, SOUND), "BABA": _tk(50, 50, {}, SOUND),
         "OKLO": {"tech_score": None, "fund_score": None, **ratings.rate(None, None, None, {})}}
    W = {"NBIS": 18.1, "IREN": 10.0, "MSFT": 4.0, "KO": 1.0}
    alerts = [
        {"ticker": "NBIS", "trigger": "ACTION_CHANGE", "detail": "Buy -> Accumulate",
         "from": "Buy", "to": "Accumulate", "from_fund": "Strong", "from_tech": "Uptrend"},
        {"ticker": "IREN", "trigger": "ACTION_CHANGE", "detail": "Hold -> Sell",
         "from": "Hold", "to": "Sell", "from_fund": "Neutral", "from_tech": "Neutral"},
        {"ticker": "IREN", "trigger": "VETO", "detail": "none -> cash_burn"},
        {"ticker": "NVDA", "trigger": "52W_HIGH", "detail": "$236.79 through prior 52w high $235.74"},
        {"ticker": "AMD", "trigger": "DATA_GAP", "detail": "scored 61.0 last run, no score this run"},
        {"ticker": "MU", "trigger": "DATA_GAP", "detail": "scored 55.0 last run, no score this run"},
    ]
    ind = {"indices": {"sp500": {"price": 7742.48, "changesPercentage": 0.99172},
                       "nasdaq": {"price": 27310.45, "changesPercentage": 1.63315}},
           "vix": {"price": 15.71}, "fear_greed": {"now": 32},
           "calendar": [{"title": "CPI (Inflation) Report", "date": "2026-10-14"},
                        {"title": "Fed Meeting No. 7 (Day 1)", "date": "2026-10-27"},
                        {"title": "already past", "date": "2026-09-01"}]}
    earn = [{"symbol": "UNH", "date": "2026-10-13"}, {"symbol": "ASML", "date": "2026-10-14"}]
    expo = {"ai-datacenter": 28.1, "megacap-platform": 4.0, "untagged": 1.0}
    port = ["NBIS", "IREN", "MSFT", "KO"]
    wl = ["INTU", "ANET", "PLTR", "BABA", "OKLO"]

    # 1. Golden text.
    alert_text = messages.alerts_message(alerts, T, W, today)
    golden("alerts.txt", alert_text)
    digest_text = messages.sunday_digest(alerts[:1], T, W, port, wl, ind, earn, expo, today, 25.0)
    golden("sunday.txt", digest_text)
    for name, text in (("alerts", alert_text), ("digest", digest_text)):
        expect(f"{name}: no bare '<' after the <b> headers", "<" not in plain(text))
        expect(f"{name}: not pre-escaped (send_telegram escapes)", "&amp;" not in text)

    # 2. Inputs that must not crash a render: a replayed old-format alert, an ACTION_CHANGE whose
    #    ticker has no technicals today, and tickers absent from the technicals dict.
    old = [{"ticker": "X", "trigger": "REC_FLIP", "detail": "Hold -> Buy (combined 55 -> 66)"},
           {"ticker": "Y", "trigger": "ACTION_CHANGE", "detail": "Buy -> Sell", "from": "Buy", "to": "Sell"}]
    text = messages.alerts_message(old, {}, {}, today)
    expect(f"a replayed old alert and a technicals-less ACTION_CHANGE must still render; got {text!r}",
           "X  REC_FLIP: Hold -> Buy (combined 55 -> 66)" in text and "Y  Buy -> Sell" in text)
    text = messages.sunday_digest([], {}, {}, ["AAA"], ["BBB"], {}, [], {}, today, 25.0)
    expect(f"tickers with no technicals must land under No Data; got {text!r}",
           "No Data (1): AAA" in text and "No Data (1): BBB" in text)

    # 3. The real book fits one Telegram message, and lists every portfolio ticker.
    data = json.load(open(DATA))
    exempt = analyzer.load_config().get("ocf_veto_exempt", [])
    real_T = {t: _with_labels((v or {}).get("technicals") or {}, t, exempt)
              for t, v in data["tickers"].items()}
    real_W = {t: v["allocation"] for t, v in data["tickers"].items() if v.get("allocation") is not None}
    real_port = [t for t, v in data["tickers"].items() if v["type"] == "portfolio"]
    real_wl = [t for t, v in data["tickers"].items() if v["type"] == "watchlist"]
    real_expo = {n: sum(real_W.get(t, 0) for t in m)
                 for n, m in analyzer.load_config().get("themes", {}).items()}
    real = messages.sunday_digest([], real_T, real_W, real_port, real_wl, data["indicators"],
                                  data["earnings"], real_expo, date.fromisoformat(data["updated"][:10]),
                                  analyzer.EXPO_LIMIT)
    expect(f"the real Sunday digest must fit one message (4,000 chars); got {len(real)}", len(real) <= 4000)
    expect("the real digest lists every portfolio ticker", all(t in real for t in real_port))

    # 4. Over budget the digest sheds sections instead of overflowing.
    wide = [f"W{i:03d}" for i in range(60)]
    wide_T = dict(T)
    wide_T.update({w: {"action": "Buy"} for w in wide})
    args = ([], wide_T, W, port, wide, ind, earn, expo, today, 25.0)
    full_len = len(messages.sunday_digest(*args))
    tight = messages.sunday_digest(*args, budget=full_len - 1)
    expect(f"over budget: shorter than the full digest and within budget; got {len(tight)} vs {full_len}",
           len(tight) <= full_len - 1)
    expect("over budget: the watchlist collapses to counts, the portfolio and changes survive",
           "Buy 60" in tight and "W059" not in tight and "PORTFOLIO (4)" in tight and "CHANGES" in tight)

    if failures:
        print(f"FAIL verify-messages: {len(failures)} check(s) failed:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"verify-messages PASS: golden alerts and digest match, real digest is {len(real)} chars "
          f"for {len(real_port) + len(real_wl)} tickers, over-budget digests shed sections.")
    return 0


```

In `__main__` add above the `--verify-monitor` line:

```python
    if "--verify-messages" in sys.argv or "--update-golden" in sys.argv:
        sys.exit(verify_messages(update="--update-golden" in sys.argv))
```

and in the module docstring flag list add
`    python3 check_scores.py --verify-messages # golden alert/digest text, digest length`
`    python3 check_scores.py --update-golden   # rewrite golden/*.txt (read the diff first)`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 check_scores.py --verify-messages`
Expected: FAIL with `ModuleNotFoundError: No module named 'messages'`.

- [ ] **Step 3: Write minimal implementation**

Create `messages.py`:

```python
"""Telegram text, built from templates. No model and no free text: every number
comes from the data, and a missing field drops its clause instead of being filled.

Output is plain text plus <b> headers. send_telegram() escapes & < > itself, so
nothing here may html.escape (it would be escaped twice), and nothing here may emit
a bare '<' (it would open a tag in Telegram and in the dashboard's innerHTML).
"""
from ratings import ACTION_ORDER


def _action_block(a, veto, t, weight):
    head = f"{a['ticker']}  {a['from']} -> {a['to']}"
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
    if why:
        lines.append("why: " + "; ".join(why))
    if t.get("reverses_if"):
        lines.append("reverses if: " + t["reverses_if"])
    return "\n".join(lines)


def _changes(alerts, technicals, weights):
    """(blocks, lines): one block per ACTION_CHANGE (a VETO on the same ticker folds into it),
    then one line per other alert, with isolated DATA_GAPs collapsed into a single line."""
    by_ticker = {}
    for a in alerts:
        by_ticker.setdefault(a["ticker"], {})[a["trigger"]] = a
    acted = [t for t, d in by_ticker.items() if "ACTION_CHANGE" in d]
    blocks = [_action_block(by_ticker[t]["ACTION_CHANGE"], by_ticker[t].get("VETO"),
                            technicals.get(t) or {}, weights.get(t))
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


def alerts_message(alerts, technicals, weights, today):
    blocks, lines = _changes(alerts, technicals, weights)
    n = len(blocks) + len(lines)
    return f"<b>MONITOR</b> {today.isoformat()}  {n} update{'s' if n != 1 else ''}\n\n" + _join(blocks, lines)


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
    the calendar, the market line. 4000 is send_telegram's own split point."""
    blocks, lines = _changes(alerts, technicals, weights)
    calendar = [c for c in (indicators or {}).get("calendar") or [] if c.get("date", "") >= today.isoformat()][:3]
    themes = sorted(((n, v) for n, v in expo.items() if n != "untagged" and v > 0), key=lambda x: -x[1])

    def build(stage):
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

    for stage in range(4):
        text = build(stage)
        if len(text) <= budget:
            break
    return text
```

In `analyzer.py` change `outage_line` to drop its emoji (new messages carry no icons):

```python
    return f"<b>DATA SOURCE</b> {alert['detail']}."
```
(replace the existing `return f"<b>⚠️ DATA SOURCE</b> {alert['detail']}."`).

- [ ] **Step 4: Generate and review the golden files, then run the tests**

```bash
python3 check_scores.py --update-golden
cat golden/alerts.txt golden/sunday.txt
```
Read both files. They must match the text below exactly (it is the fixture's deterministic output); if they differ, fix the code, not the golden text.

`golden/alerts.txt`:
```
<b>MONITOR</b> 2026-10-04  4 updates

NBIS  Buy -> Accumulate  (18.1% of book)
why: technical Uptrend -> Neutral: 52-week range position 55%, 150-day avg falling 0.4%/21d, PPO -0.4%; fundamentals Strong: health strong (8/8), profitability strong (margins 61%/38%, ROE 24%), valuation mixed (P/E 31.2)
reverses if: technical score 63 or above (now 52) -> Buy

IREN  Hold -> Sell  (10.0% of book)
why: veto none -> cash_burn; technical Neutral -> Downtrend: 52-week range position 18%, 150-day avg falling 6.1%/21d, PPO -2.3%; fundamentals Neutral -> Weak: cash-burn gate, valuation weak (P/S 12.0), health weak (2/8)
reverses if: technical score 43 or above (now 30) -> Avoid

NVDA  52W_HIGH: $236.79 through prior 52w high $235.74
DATA_GAP: AMD, MU
```

`golden/sunday.txt`:
```
<b>WEEKLY BOOK</b> 2026-10-04

<b>CHANGES</b>
NBIS  Buy -> Accumulate  (18.1% of book)
why: technical Uptrend -> Neutral: 52-week range position 55%, 150-day avg falling 0.4%/21d, PPO -0.4%; fundamentals Strong: health strong (8/8), profitability strong (margins 61%/38%, ROE 24%), valuation mixed (P/E 31.2)
reverses if: technical score 63 or above (now 52) -> Buy

<b>PORTFOLIO (4)</b>
Buy (1): MSFT 4.0%
Accumulate (1): NBIS 18.1%
Hold (1): KO 1.0%
Sell (1): IREN 10.0%

<b>WATCHLIST (5)</b>
Buy (2): ANET, PLTR
Accumulate (1): INTU
Hold (1): BABA
No Data (1): OKLO

<b>MARKET</b>
S&P 500 7,742 (+0.99%) | NASDAQ 27,310 (+1.63%) | VIX 15.7 | Fear & Greed 32

<b>EARNINGS (14d)</b>
UNH 10-13, ASML 10-14

<b>CALENDAR</b>
10-14 CPI (Inflation) Report
10-27 Fed Meeting No. 7 (Day 1)

<b>THEMES</b>
ai-datacenter 28.1% (over limit), megacap-platform 4.0%
```

Then run:
```bash
python3 check_scores.py --verify-messages
python3 check_scores.py --verify-outage
python3 check_scores.py --verify-ratings
```
Expected: all PASS (`verify-messages PASS: ... real digest is ~1100 chars ...`).

- [ ] **Step 5: Commit**

```bash
git add messages.py golden/alerts.txt golden/sunday.txt check_scores.py analyzer.py
git diff --cached --stat
git commit -m "feat: template-built alert and Sunday digest messages with golden tests"
```

---

### Task 5: Send template messages; remove the model

**Files:**
- Modify: `analyzer.py` (`import messages`, new `briefing_text`, `main`; delete `build_prompt`, `analyze`, `fetch_market_news`)
- Modify: `.github/workflows/market-analysis.yml` (drop `GEMINI_API_KEY`)
- Modify: `check_scores.py` (`verify_messages` section 5, `verify_workflow`)

**Interfaces:**
- Consumes: `messages.alerts_message`, `messages.sunday_digest`, existing `outage_line`, `quiet_line`.
- Produces: `analyzer.briefing_text(alerts, full, technicals, weights, portfolio, watchlist, indicators, earnings, expo, today) -> str`; `portfolio` is the config dict (ticker -> holding), `watchlist` the list of tickers.

- [ ] **Step 1: Write the failing test**

In `verify_messages`, insert before `if failures:` (section 5):

```python
    # 5. Which message a run sends. The model is out of the loop entirely.
    def bt(alerts_, full):
        return analyzer.briefing_text(alerts_, full, T, W, {"NBIS": {}, "IREN": {}}, ["INTU"],
                                      ind, earn, expo, today)
    outage = [{"ticker": "YAHOO", "trigger": "FUNDAMENTALS_OUTAGE",
               "detail": "fundamentals unavailable for 56/56 tickers, kept last scores for 56"}]
    expect("outage-only run sends the one-line data-source message",
           bt(outage, False).startswith("<b>DATA SOURCE</b>") and "\n" not in bt(outage, False))
    expect("Sunday sends the digest even when only an outage fired",
           bt(outage, True).startswith("<b>WEEKLY BOOK</b>"))
    expect("a normal day with alerts sends the monitor message",
           bt(alerts[:1], False).startswith("<b>MONITOR</b>"))
    expect("a quiet day sends the quiet line", bt([], False).startswith("Nothing changed."))
```

In `verify_workflow`, after the `if "date -u +%H" in text:` block add:

```python
    if "GEMINI" in text:
        failures.append("the workflow still passes GEMINI_API_KEY; the Telegram text is built "
                        "from templates and no step calls a model")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 check_scores.py --verify-messages
python3 check_scores.py --verify-workflow
```
Expected: `--verify-messages` FAIL with `AttributeError: module 'analyzer' has no attribute 'briefing_text'`; `--verify-workflow` FAIL mentioning `GEMINI_API_KEY`.

- [ ] **Step 3: Write minimal implementation**

1. `analyzer.py`: add `import messages` on the line after `import ratings`.

2. Delete the prompt and the model call, and the market-news fetch that only fed the prompt:
```bash
python3 - <<'EOF'
s = open("analyzer.py").read()
a, b = s.index("def build_prompt("), s.index("def sanitize_telegram_html(")
s = s[:a] + s[b:]                       # removes build_prompt and analyze
a, b = s.index("def fetch_market_news("), s.index("def fetch_fear_greed_data(")
s = s[:a] + s[b:]
open("analyzer.py", "w").write(s)
EOF
```

3. Add `briefing_text` directly after `outage_line`:

```python
def briefing_text(alerts, full, technicals, weights, portfolio, watchlist, indicators,
                  earnings, expo, today):
    """The whole Telegram message for a run. Built from templates: a model rewrote 56
    exceptions as 55 lines and 27 positions as 58."""
    if [a["trigger"] for a in alerts] == ["FUNDAMENTALS_OUTAGE"] and not full:
        return outage_line(alerts[0])
    if full:
        return messages.sunday_digest(alerts, technicals, weights, list(portfolio), list(watchlist),
                                      indicators, earnings, expo, today, EXPO_LIMIT)
    if alerts:
        return messages.alerts_message(alerts, technicals, weights, today)
    return quiet_line(portfolio, expo, earnings)
```

4. `main()`:
   - Delete the market-news block, i.e. these lines:
```python
    # Fetch market data. Market news only feeds the briefing prompt, so a Finnhub
    # outage must not abort the run before the dashboard data is fetched.
    try:
        market_news = fetch_market_news(finnhub_key)
    except Exception as e:
        print(f"Warning: failed to fetch market news: {e}")
        market_news = []
```
   (keep `fg_data = fetch_fear_greed_data()` and `vix = fetch_vix()` that follow it; put the comment `# Fetch market data.` back above `fg_data` if you like).
   - Replace the block from `    if [a["trigger"] for a in alerts] == ["FUNDAMENTALS_OUTAGE"] and not full:` through `        analysis = quiet_line(portfolio, expo, earnings)` with:
```python
    analysis = briefing_text(alerts, full, technicals, weights, portfolio, watchlist,
                             indicators, earnings, expo, today)
```
   - In the comment above `save_market_data(`, change "just because Gemini or Telegram is down" to "just because Telegram is down".

5. `.github/workflows/market-analysis.yml`: delete the line `          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m py_compile analyzer.py check_scores.py messages.py ratings.py
grep -n "build_prompt\|analyze(\|market_news\|GEMINI\|Gemini\|REC_FLIP" analyzer.py .github/workflows/market-analysis.yml
python3 check_scores.py --verify-messages
python3 check_scores.py --verify-workflow
python3 check_scores.py --verify-outage
python3 check_scores.py --verify-monitor
python3 check_scores.py
```
Expected: compile succeeds; the grep prints nothing; every verifier PASS; the gate reports `changed=0  flips=0`.

- [ ] **Step 5: Commit**

```bash
git add analyzer.py check_scores.py .github/workflows/market-analysis.yml
git diff --cached --stat
git commit -m "feat: send template-built Telegram messages; drop the Gemini step"
```

---

### Task 6: Dashboard, docs, and end-to-end proof

**Files:**
- Modify: `docs/index.html`, `README.md`, `docs/USAGE-GUIDE.md`

**Interfaces:**
- Consumes: the per-ticker fields `action`, `fund_label`, `tech_label`, `drivers`, `reverses_if` in `market-data.json`.

- [ ] **Step 1: Dashboard changes (`docs/index.html`)**

Apply each edit with the Edit tool (the old strings are unique):

1. CSS: after the line starting `    .rec-strong-sell {` add:
```css
    .chips { font-size: 10px; color: #888; margin-top: 3px; white-space: nowrap; }
    .explain { font-size: 12px; color: #bbb; line-height: 1.6; margin: 6px 0 10px; }
    .explain b { color: #fff; }
```
2. Header: replace
`<th onclick="sortTable(8)">Action <span class="help" title="Algorithm-based recommendation from technical indicators">?</span></th>`
with
`<th onclick="sortTable(8)">Action <span class="help" title="Fundamentals and technicals are rated separately, then combined into one action. Click a row for the reasons.">?</span></th>`
3. JS: insert immediately before `function rsiColor(rsi) {`:
```js
const ACTION_RANK = ['Buy', 'Accumulate', 'Starter', 'Hold', 'Momentum only', "Don't add", 'Avoid', 'Sell'];

// Higher is better, like the score this replaces as the sort key. Data written before
// the grid existed has no action; those rows sort by their blended score as before.
function actionScore(t) {
  const i = ACTION_RANK.indexOf(t.action);
  return i < 0 ? -1 : ACTION_RANK.length - i;
}

function actionTag(t) {
  if (!t.action) return recTag(t.recommendation) + scoreBar(t.combined_score ?? t.score);
  const cls = { 'Buy': 'rec-strong-buy', 'Accumulate': 'rec-buy', 'Starter': 'rec-buy', 'Hold': 'rec-hold',
    'Momentum only': 'rec-hold', "Don't add": 'rec-sell', 'Avoid': 'rec-sell', 'Sell': 'rec-strong-sell' }[t.action] || 'rec-hold';
  const chips = [t.fund_label && `Fund ${t.fund_label}`, t.tech_label && `Tech ${t.tech_label}`].filter(Boolean).join(' · ');
  return `<span class="rec-tag ${cls}">${t.action}</span>${chips ? `<div class="chips">${chips}</div>` : ''}`;
}

```
4. Sort: replace
`case 8: va = ta.combined_score || ta.score || 0; vb = tb.combined_score || tb.score || 0; break;`
with
`case 8: va = actionScore(ta) * 1000 + (ta.combined_score ?? ta.score ?? 0); vb = actionScore(tb) * 1000 + (tb.combined_score ?? tb.score ?? 0); break;`
5. Row: replace `<td>${recTag(t.recommendation)}${scoreBar(t.combined_score ?? t.score)}</td>` with `<td>${actionTag(t)}</td>`.
6. Detail data: after the line `    const combSc = t.combined_score ?? t.score ?? 0;` add:
```js
    const dr = t.drivers || {};
    const explain = t.action ? `<div class="explain">
        <div><b>Fundamentals ${t.fund_label ?? '—'}:</b> ${(dr.fund || []).join(', ') || '—'}</div>
        <div><b>Technical ${t.tech_label ?? '—'}:</b> ${(dr.tech || []).join(', ') || '—'}</div>
        ${t.reverses_if ? `<div><b>Reverses if:</b> ${t.reverses_if}</div>` : ''}
      </div>` : '';
```
7. Detail scores row: replace
`<span class="score-item" style="font-size:13px">Combined: <b style="color:${scoreColor(combSc)}">${combSc}</b>/100 ${recTag(t.recommendation)}</span>`
with
```
<span class="score-item" style="font-size:13px">Action: ${actionTag(t)}</span>
        <span class="score-item">Blend: <b style="color:${scoreColor(combSc)}">${combSc}</b>/100</span>
```
and replace the comment line `      <!-- TAB: CHART & INDICATORS -->` with `      ${explain}\n      <!-- TAB: CHART & INDICATORS -->`.

- [ ] **Step 2: Verify the dashboard**

Syntax check the inline scripts (skip with a note if `node` is not installed):
```bash
D=$(mktemp -d)
python3 - "$D" <<'EOF'
import re, sys
html = open("docs/index.html").read()
open(sys.argv[1] + "/inline.js", "w").write("\n".join(re.findall(r"<script>(.*?)</script>", html, re.S)))
EOF
node --check "$D/inline.js" && echo "inline JS parses"
```
Then preview with new-format data. This builds a scratch copy of `docs/` with the fields a bot run would add, and serves it:
```bash
P=$(mktemp -d); cp -r docs "$P/docs"
python3 - "$P" <<'EOF'
import json, sys, types
sys.modules.setdefault("requests", types.ModuleType("requests"))
sys.path.insert(0, ".")
import analyzer, ratings
p = sys.argv[1] + "/docs/data/market-data.json"
d = json.load(open(p)); ex = analyzer.load_config().get("ocf_veto_exempt", [])
for t, v in d["tickers"].items():
    tc = v.get("technicals") or {}
    if not tc: continue
    fund = tc.get("fundamentals") or {}
    reason, cap = analyzer.veto_gates(fund, tc.get("financialHistory"), t, ex)
    r = ratings.rate(tc.get("tech_score"), tc.get("fund_score"), cap, {})
    tc.update(r)
    tc["drivers"] = {"tech": ratings.tech_drivers(tc), "fund": ratings.fund_drivers(fund, None, tc.get("quality_score"), reason)}
    tc["reverses_if"] = ratings.reverses_if(tc.get("tech_score"), tc.get("fund_score"), r, cap)
json.dump(d, open(p, "w"))
EOF
(cd "$P/docs" && python3 -m http.server 8123 >/dev/null 2>&1 &)
```
Open `http://localhost:8123/` and check: the Action column shows a coloured tag with `Fund … · Tech …` chips under it; clicking the header sorts Buy first / Sell last (and back); expanding NBIS shows the Action tag, a `Blend` number with no Buy/Sell tag, and the Fundamentals / Technical / Reverses-if lines. Then open the unpatched `docs/` the same way (`python3 -m http.server 8124 -d docs`) and confirm it still renders the old tag + score bar. Stop both servers (`pkill -f "http.server 812"`).

- [ ] **Step 3: Docs (`README.md`, `docs/USAGE-GUIDE.md`)**

`README.md` — apply with the Edit tool:
1. `a recommendation bucket\nflip, a veto gate firing or clearing,` → `an action change\n(for example Buy to Accumulate), a veto gate firing or clearing,`
2. `The AI (Gemini) reviews the algorithm's pre-computed decisions and exceptions, adding news context and flagging disagreements.` → `Messages are built from code templates, so every number in them comes straight from the data. No model writes the Telegram text.`
3. After the line `Recommendation thresholds: Strong Buy (72+), Buy (60+), Hold (40+), Sell (28+), Strong Sell (<28).` add:
```markdown

### Ratings Grid
Fundamentals and technicals are rated **separately** and never blended into the action:
**Strong / Neutral / Weak** fundamentals (score ≥60 / 40-60 / <40) and **Uptrend / Neutral / Downtrend**
technicals, combined into one action:

| | Uptrend | Neutral | Downtrend |
|---|---|---|---|
| **Strong** | Buy | Accumulate | Starter |
| **Neutral** | Hold | Hold | Don't add |
| **Weak** | Momentum only | Avoid | Sell |

A label only moves once its score clears the edge by 3 points, so a name sitting on 60 does not flip every
day. A veto gate bypasses that: cash burn forces Weak, leverage or margin erosion forces at most Neutral.
Each ticker carries its drivers and a "reverses if" line (the nearest score edge that would change the
action). The blended score above is kept as a secondary number.
```
4. Architecture diagram: delete the three lines
```
                    |
                    v
                Gemini AI (reviews algorithm output)
```
(keep the `|` / `v` pair that precedes `docs/data/market-data.json`).
5. Data sources: change `Company news, market news, earnings calendar` → `Company news, earnings calendar`; delete the `[Gemini API]` table row.
6. Delete the bullet `- **Google Gemini:** Go to https://aistudio.google.com/apikey, create key`; in "Fork and Deploy" change ``Add secrets: `GEMINI_API_KEY`, `FINNHUB_API_KEY`,`` → ``Add secrets: `FINNHUB_API_KEY`,``; delete the line `export GEMINI_API_KEY="your-key"`.
7. Delete the whole `## Upgrading the AI Model` section (from that heading up to, not including, `## Customization`):
```bash
python3 - <<'EOF'
s = open("README.md").read()
a, b = s.index("## Upgrading the AI Model"), s.index("## Customization")
open("README.md", "w").write(s[:a] + s[b:])
EOF
```
8. Replace `### Change Analysis Style\n\nEdit \`build_prompt()\` in \`analyzer.py\`. The prompt controls the Telegram briefing structure, sections, and character limit.` with `### Change Message Format\n\nEdit the templates in \`messages.py\`. \`golden/\` pins the exact text: after a change run \`python3 check_scores.py --verify-messages\`, read the diff, then \`--update-golden\`.`
9. `Telegram, Gemini Flash Lite. **$0/month.**` → `Telegram. **$0/month.**`

`docs/USAGE-GUIDE.md` — four exact replacements (each old line occurs once):
- `2. Open the **dashboard** — look at the main table sorted by **Action** (click the column header) to see what the algorithm recommends` → the same line followed by a new line `   Each row shows the action plus its **Fund** and **Tech** labels (fundamentals and technicals are rated separately).`
- `3. Click tickers flagged as **Strong Buy** or **Strong Sell** to understand why` → `3. Click tickers marked **Buy** or **Sell** to see the drivers and the "Reverses if" line (what would change the action)`
- `- **Action column** shows the combined recommendation (Strong Buy / Buy / Hold / Sell / Strong Sell) with a colored score bar` → `- **Action column** shows one of Buy / Accumulate / Starter / Hold / Momentum only / Don't add / Avoid / Sell, with the fundamental and technical labels under it. The blended score is in the detail panel as "Blend".`
- `- The algorithm scores them the same way — when a watchlist stock hits "Strong Buy" with solid fundamentals, that's your entry signal` → `- The algorithm rates them the same way — when a watchlist stock reaches **Buy** or **Accumulate** (Strong fundamentals with an Uptrend or Neutral technical), that's your entry signal`

- [ ] **Step 4: Full verification**

```bash
python3 -m py_compile analyzer.py check_scores.py messages.py ratings.py
for m in "" --self-test --verify-monitor --verify-ratings --verify-messages --verify-workflow --verify-outage; do echo "== check_scores.py $m"; python3 check_scores.py $m | tail -2; done
grep -rn "Gemini\|GEMINI\|build_prompt" README.md docs/USAGE-GUIDE.md analyzer.py .github/workflows/ || echo "no Gemini left"
git status --short
```
Expected: compile OK; every mode passes (the plain gate ends `changed=0  flips=0`); `no Gemini left`; `git status` lists only the files this task changed.

- [ ] **Step 5: End-to-end dry run through `main()` (offline, nothing sent, repo data untouched)**

This runs the real `main()` twice with the network fetches replaced by the stored data, a temporary data file, and `send_telegram` printing instead of sending:
```bash
FINNHUB_API_KEY=x TELEGRAM_BOT_TOKEN=x TELEGRAM_CHAT_ID=x python3 - <<'PYEOF'
import json, os, shutil, tempfile
import analyzer
stored = json.load(open(analyzer.DATA_PATH))["tickers"]
tmp = tempfile.mkdtemp(); p = os.path.join(tmp, "market-data.json")
shutil.copy(analyzer.DATA_PATH, p); analyzer.DATA_PATH = p

def tech(t):
    tc = dict(stored[t]["technicals"])
    for k in ("fundamentals", "financialHistory", "tech_score", "fund_score", "combined_score", "recommendation",
              "fund_score_reasons", "quality_score", "quality_details", "veto_reason", "tech_recommendation",
              "next_earnings", "fund_asof", "action", "fund_label", "tech_label", "drivers", "reverses_if",
              "fund_subscores"):
        tc.pop(k, None)
    tc["recommendation"] = analyzer.score_to_recommendation(tc["score"]); tc["score_reasons"] = []
    return tc

analyzer.fetch_technicals = tech
analyzer.fetch_fundamentals = lambda t: {
    "fundamentals": stored[t]["technicals"].get("fundamentals") or {},
    "price_target": {k: stored[t]["technicals"].get(k) for k in analyzer.PT_KEYS},
    "earnings": stored[t]["technicals"].get("next_earnings") or {}}
analyzer.fetch_financial_history = lambda t: stored[t]["technicals"].get("financialHistory")
analyzer.fetch_company_news = lambda t, k: []
analyzer.fetch_fear_greed_data = lambda: None
analyzer.fetch_vix = lambda: None
analyzer.init_yahoo_auth = lambda: None
analyzer.time.sleep = lambda s: None
analyzer.send_telegram = lambda text, *a: print("---- WOULD SEND ----\n" + text + "\n--------------------")
analyzer.save_briefing_history = lambda *a: None

analyzer.main()                                   # run 1: the previous file has no actions
d = json.load(open(p))
print("tickers with an action:", sum(1 for v in d["tickers"].values() if (v.get("technicals") or {}).get("action")), "of", len(d["tickers"]))
d["tickers"]["NBIS"]["technicals"]["action"] = "Sell"       # pretend last run said Sell
d["tickers"]["NBIS"]["technicals"]["fund_label"] = "Weak"
json.dump(d, open(p, "w"))
analyzer.main()                                   # run 2: NBIS should read Sell -> <today's action>
PYEOF
```
Expected: run 1 prints the quiet line (`Nothing changed. 27 positions, ...`; on a Sunday the digest instead) and **no** `ACTION_CHANGE` line, then `tickers with an action: 56 of 56`; run 2 prints `EXCEPTION NBIS [ACTION_CHANGE]: Sell -> <action>` and a `<b>MONITOR</b>` message with a `NBIS  Sell -> <action>` block carrying `why:` and `reverses if:` lines. If either run raises, stop and fix before anything else. `git status` must show no change under `docs/data/`.

The live proof is the owner's step after pushing: a manual workflow run on the branch should send the quiet line (the first run baselines silently) and commit `action` on all 56 tickers.

- [ ] **Step 6: Commit**

```bash
git add docs/index.html README.md docs/USAGE-GUIDE.md
git diff --cached --stat
git commit -m "feat: show the action grid on the dashboard; document it and drop the Gemini docs"
git log --oneline -8
```

Then stop. Do not push. Report to the owner: the commits, the verification output, and a plain PR title and description (what changed, how it was tested, that the first scheduled run baselines silently and that the `GEMINI_API_KEY` repo secret can now be deleted).
