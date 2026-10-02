# Ratings grid and Telegram v2 — Design (sub-project #1a)

**Status:** design approved section by section by the owner on 2026-10-02; written spec awaiting review.
Roadmap: Obsidian `Market Analyst Bot/Plan.md` (task 1a). Builds on
`2026-09-04-scoring-rewrite-design.md`, whose owner decisions all still stand (capex-aware gates,
NBIS un-vetoable, VST/CEG capped at Hold, hand-tagged themes, monitor mode, `check_scores.py` gate).

## Goal

Fundamental and technical are rated separately and never blended; a third view names an action from
the pair. Telegram is built from code templates, so it cannot lose or invent a number.

Out of scope: position sizing and price levels (#3, #4), evidence/backtest (#1b), macro and news (#2),
add/drop tickers (#5), watchlist alerts.

## Decisions (owner, 2026-10-02)

1. **3×3 grid, edges 40/60, 3-point band on each axis.**
2. **Nine action tags** (below). Alerts fire on a tag change, so Hold↔Hold is silent.
3. **Approach A:** the blended `combined_score` and `recommendation` stay in `market-data.json`
   (dashboard, `THEME_DROP`, `DATA_GAP` and the `check_scores.py` gate keep reading them), but no
   screen or message shows the blended Buy/Sell label next to the action.
4. **The model leaves the Telegram path.** No Gemini call; the news paragraph returns in #2 behind a
   materiality filter. No icons, so there is no legend to maintain.
5. **`reverses_if` is in scores, not prices.** Price levels wait for #4.

## Measured (25 usable daily runs, 7 Sep – 2 Oct; 1 Oct outage run excluded; 27 portfolio names)

| Variant | Cell changes | Tickers that never change |
|---|---|---|
| 3×3, no damping | 43 | 13/27 |
| 3×3, 3-pt band | 20 (18 tag changes, on 10 tickers) | 15/27 |
| 3×3, 5-pt band | 12 | 18/27 |
| 2×2, 3-pt band | 8 | 21/27 |

Today's portfolio cells: Strong/Up 7, Strong/Neutral 2, Strong/Down 5, Neutral/Up 1, Neutral/Neutral 3,
Neutral/Down 5, Weak/Down 4; Weak/Up and Weak/Neutral are empty. 14 of 27 names are in Downtrend.
The veto override (below) did not change the counts. Caveats: one market regime, 25 runs, scores
from the post-rewrite engine only. Re-measure on real stored labels after a month.

## Ratings (`ratings.py`, pure functions, no I/O)

Edges `(40, 60)`, band `3`. A label is sticky: it changes only when the score clears the edge by the band.

| | Enters | Stays until |
|---|---|---|
| Strong / Uptrend | score ≥ 63 | score < 57 |
| Weak / Downtrend | score < 37 | score ≥ 43 |
| Neutral | otherwise | |

With no previous label the plain edges apply (≥60, <40). A `None` score gives a `None` label and the
action `No Data`.

**Veto override.** A veto cap bypasses damping: cap 39 forces fundamentals to Weak, cap 59 forces at
most Neutral. Without it, a name already Strong at 62 and then capped at 59 would stay Strong, since
59 is above the 57 exit.

```python
def rate(tech_score, fund_score, veto_cap, prev):   # prev: {"fund_label","tech_label"} or {}
    # -> {"fund_label", "tech_label", "action"}
```

| | Uptrend | Neutral | Downtrend |
|---|---|---|---|
| **Strong** | Buy | Accumulate | Starter |
| **Neutral** | Hold | Hold | Don't add |
| **Weak** | Momentum only | Avoid | Sell |

Detail line per tag: Buy "add"; Accumulate "scale in"; Starter "small, wait for trend repair";
Hold "trend intact" (Neutral/Up only); Don't add "consider trimming"; Momentum only "tight stop".
Sort rank (dashboard): Buy, Accumulate, Starter, Hold, Momentum only, Don't add, Avoid, Sell.

**`drivers`** — up to 3 per axis, all rendered from raw fields by a fixed phrase per signal:
- Fundamental: the four sub-scores (valuation, profitability, growth, health) ranked by distance from
  50, each as one phrase citing its raw inputs (health: `health 8/8`; profitability: gross/operating
  margin and ROE; growth: revenue and earnings growth; valuation: P/E vs sector median). A firing
  veto gate is always listed first.
- Technical: the three terms of the score — 52-week range position, 150-day average slope, PPO.
  Volume stays the `DISTRIBUTION` alert.

**`reverses_if`** — one line: of the exit edges on both axes (fundamentals excluded while a veto holds),
the one nearest the current score, the tag it lands on, and today's value. Example:
`technical score above 63 (now 58) -> Buy`. Omitted when no axis can move.

## Data shape (`market-data.json`, per ticker `technicals`)

Added: `fund_label`, `tech_label`, `action`, `drivers` (`{fund: [...], tech: [...]}`), `reverses_if`,
`slope150`, `fund_subscores`. `slope150` is currently only inside a reason string; it is stored so the
template cites a number. `compute_fundamental_score` also returns its four sub-scores as a dict (stored as
`fund_subscores`, plus `health_known`, the number of measurable F-score signals); its two callers
(`merge_fundamentals`, `check_scores.replay`) are updated. Nothing is removed.

Computed after `merge_fundamentals`, in `main()`, with `prev` read from `prev_data` (the same state
mechanism `compute_exceptions` uses).

## Alerts (`compute_exceptions`)

`ACTION_CHANGE` replaces `REC_FLIP` (trigger 1): both `old.action` and `cur.action` are set and differ,
weight ≥ `MIN_WEIGHT`. No hysteresis here; the labels are already damped. `_bucket_edges` and
`REC_HYSTERESIS` are deleted with `REC_FLIP`. A ticker whose previous run has no `action` baselines
silently, so the first run after merge fires no `ACTION_CHANGE`. All other triggers are unchanged.
`VETO` and `ACTION_CHANGE` for the same ticker render as one alert, with the veto as the first reason.

## Telegram (`messages.py`, templates only)

Templates emit plain text with `<b>` headers and do not `html.escape`: `send_telegram` already escapes
`& < >`, so escaping here would double-escape. They avoid `<` and `>` in their own text so the dashboard,
which renders briefings as innerHTML, shows them intact. A missing field drops its clause and is never
replaced.

Action change:
```
NBIS  Buy -> Accumulate  (18.1% of book)
why: technical Uptrend -> Neutral: 52-week range position 55%, 150-day avg falling 0.4%/21d, PPO -0.4%; fundamentals Strong: health strong (8/8), profitability strong (margins 61%/38%, ROE 24%), valuation mixed (P/E 31.2)
reverses if: technical score 63 or above (now 52) -> Buy
```
The alert dict stored in `alerts_pending` carries `from`, `to`, `from_fund` and `from_tech`, so a replayed
alert still reads correctly.
Every other trigger renders `{ticker}  {TRIGGER}: {detail}` using the existing code-built `detail`.
Systemic outage stays the single `FUNDAMENTALS_OUTAGE` line; isolated gaps collapse to one
`DATA_GAP: A, B, C` line. The quiet-day line is unchanged.

**Sunday digest** (≤ 4,096 characters; the code budget is 4,000, `send_telegram`'s own split point), in this priority order: changes since the last run; the portfolio
grouped by tag with weights; the watchlist grouped by tag (tickers only); market line (S&P, Nasdaq, VIX,
Fear & Greed from `indicators`); earnings in the next 14 days; next three calendar events; theme
exposure. If over budget, watchlist groups collapse to counts first, then the calendar goes.

**Removed:** `analyze`, `build_prompt`, the call sites that only fed the prompt (grep before deleting),
and `GEMINI_API_KEY` in `.github/workflows/market-analysis.yml` (the repo secret is the owner's to delete).
`save_briefing_history` stays, now storing plain text.

## Dashboard (`docs/index.html`)

The Action column shows the action tag plus the two label chips; the detail panel shows `drivers` and
`reverses_if`; the blended score becomes a secondary line without the Buy/Sell tag; the column sorts
by action rank. Update the glossary text, `README.md` and `docs/USAGE-GUIDE.md`.

## Verification (`check_scores.py`)

- The existing gate must report **0 diffs** (the blend is untouched). Any diff is a regression.
- `--verify-ratings`: edges, entry/exit band, veto override, `None` handling, the nine-cell table,
  `reverses_if` choice, and that Hold↔Hold does not fire `ACTION_CHANGE`.
- `--verify-messages`: golden files for the action alert, the merged veto alert, the data-gap line and
  the Sunday digest; the digest rendered from the saved 56-ticker data is ≤ 4,096 characters, and a
  3× inflated fixture proves the budget guard.
- Plan-time check: the dashboard's briefings panel renders plain-text content.

## Rollout

Branch `feat/ratings-grid-telegram-v2` off `main`. Local commits only; the owner opens and merges the PR.
After the first scheduled run, confirm `action` is present on all 56 tickers and no alert storm was sent.
