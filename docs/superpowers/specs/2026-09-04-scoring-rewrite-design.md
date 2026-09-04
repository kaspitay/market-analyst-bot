# Scoring & Briefing Rewrite — Design

**Status:** final, after a 9-agent adversarial review (4 lenses + 4 verifiers + synthesis) tested
every proposed rule against the real 53-ticker dataset in `docs/data/market-data.json`. Four
first-draft proposals were overturned with measurements; this doc reflects the corrected design.
A readable, illustrated version of this same design lives at
https://claude.ai/code/artifact/76c9221a-7ea3-4483-aa5e-128b19e775c7 — read that for the narrative
and the "why"; this doc is the version an implementer works from.

## Why

The original scoring let missing data score a neutral 50 (indistinguishable from mediocre), put
analyst price targets at 20% of the "fundamental" score (sentiment, not fundamentals), had no hard
vetoes (ORCL scored 80.9 → Buy with -24.54B FCF and D/E 389), fetched 4 years of financial history
and never scored it, and buried the owner's three wanted technical signals (MACD, volume surge,
150MA) under six he didn't want (RSI/Stochastic/Bollinger/MFI/ADX/OBV) — those six carried 78% of
the technical score's swing, the three he wanted carried 18%. The Telegram briefing averaged 6,533
chars against a 3,900-char instruction the model was structurally unable to honor, and reported
"death cross" as a persistent state re-announced as breaking news twice a day.

## Owner's standing decisions (constraints, not suggestions)

1. **Capex-aware gates.** `OCF < 0` is a hard veto. `OCF > 0` with capex > OCF (self-funded growth
   buildout) is NOT vetoed — it gets a leverage-based test instead (see Gate 2 below).
2. **Thematic concentration** via hand-tagged themes in `config.json`, not derived sector fields
   (Yahoo's `sector` puts IREN, a bitcoin miner, in "Financial Services" next to MA and V).
3. **Monitor mode.** The briefing reports exceptions (state transitions), not a restated snapshot of
   every position twice a day. Silence on a quiet day is correct behavior, not a bug.
4. **Regression fixture is mandatory.** `check_scores.py` (done, Phase 0) gates every phase.
5. **Dynamic portfolio weights.** `config.json` currently stores static percentages that sum to
   97.32% and never change. Store share counts instead; derive live percentages from share count ×
   current price (already fetched, no new API calls). This also unlocks trigger 9 in monitor mode
   (theme exposure crossing a threshold) — impossible with static percentages, since the owner
   edited `config.json` only twice across 162 measured runs.

## Final fundamental score

```
fund_score = (0.20*val + 0.25*prof + 0.20*growth + 0.35*health) / coverage
```
`coverage` = sum of weights of the non-`None` sub-scores (missing inputs renormalize the rest rather
than being scored as 50). Analyst price target is **out of the score**, display-only.

### Health (0.35) — 8-signal F-score

Replaces `compute_quality_score`'s current 7-point checklist, which invents a non-Piotroski
"Positive FCF" criterion and a levels-only D/E test — those two are exactly what punishes
capex-heavy buildouts for free. Canonical Piotroski uses operating cash flow, never FCF, and capex
never enters an F-score at all.

| # | Signal | Computed from |
|---|---|---|
| 1 | ROA > 0 | `netIncome[-1] / totalAssets[-1]` |
| 2 | CFO > 0 | `operatingCashFlow[-1]` |
| 3 | ΔROA > 0 | ROA this year vs prior year |
| 4 | CFO > netIncome | same fiscal year — this subsumes the accrual-quality test |
| 5 | Δ(totalDebt / totalAssets) < 0 | this year vs prior year |
| 6 | `dilutedAverageShares[-1] <= dilutedAverageShares[-2] * 1.01` | 1% dilution tolerance |
| 7 | Δ(grossProfit / totalRevenue) > 0 | this year vs prior year |
| 8 | Δ(totalRevenue / totalAssets) > 0 | this year vs prior year |

`health = 100 * sum(known_true) / count_known`. A signal with a missing input **abstains** — it is
excluded from `count_known`, it does not score as false. All 8 fields come from
`fetch_financial_history`'s per-year records (Phase 1, done): `netIncome`, `totalAssets`,
`operatingCashFlow`, `totalDebt`, `dilutedAverageShares`, `grossProfit`, `totalRevenue`.

Measured effect of the fix: capex-heavy names move **+7.9** normalized points, capex-light names
**-7.6** — NBIS 3/7 → 6/8, IREN 3/7 → 6/8, MU and TSM (the two most capital-intensive real
businesses in the dataset) both reach 8/8. CIFR stays at 2/8 and ASTS at 3/8 — correctly, those are
the real distress cases.

### Veto gates — two cap levels, lowest wins

```python
def veto_gates(fund):
    """Returns (reason, cap) for the tightest gate that fires, or (None, None)."""
    ocf = fund.get("operatingCashflow")
    rev = fund.get("totalRevenue")
    total_debt = fund.get("totalDebt")
    total_cash = fund.get("totalCash")

    # Gate 1: cash burn. ocf is None -> abstain (skip), never silently clear.
    if ocf is None:
        pass
    elif ocf < 0:
        materiality_ok = rev is None or (ocf / rev) < -0.05
        if materiality_ok and fund.get("_ticker") not in OCF_VETO_EXEMPT:
            return ("cash_burn", 39)  # Sell ceiling

    # Gate 2: leverage. This is the capex-aware test decision 1 asked for —
    # netDebt/ocf separates growth-capex (NBIS 0.076) from real leverage
    # (ORCL 4.24) without ever touching a capex figure directly.
    if ocf is not None and ocf > 0 and total_debt is not None and total_cash is not None:
        net_debt = total_debt - total_cash
        if net_debt > 0 and (net_debt / ocf) > 4.0:
            return ("leverage", 59)  # Hold ceiling

    # Gate 3: margin erosion, from financialHistory (Phase 1). Skip on any
    # None in the series rather than raising — grossProfit/operatingIncome
    # are null for 7 of 53 tickers.
    for series_key in ("grossProfit", "operatingIncome"):
        s = [row.get(series_key) for row in (fund.get("_history") or [])]
        r = [row.get("totalRevenue") for row in (fund.get("_history") or [])]
        if len(s) < 3 or any(x is None for x in s) or any(x is None for x in r) or any(x == 0 for x in r):
            continue
        margins = [a / b for a, b in zip(s, r)]
        if margins[-1] - margins[0] <= -0.05 and margins[-1] < margins[-2]:
            return ("margin_erosion", 59)  # Hold ceiling

    return (None, None)
```
`OCF_VETO_EXEMPT = ["NU", "SOFI"]` in `config.json` — both have structurally negative OCF from loan
origination (NU: ROE 0.301, +2.869B net income, +55.9% earnings growth; a bank, not a burner).
No sector-based exemption is possible: Yahoo's `sector` field files IREN and EOS under "Financial
Services" alongside MA and V, so a sector carve-out would exempt exactly the position (IREN,
12.46%) this design most needs gated the moment its OCF turns negative.

Measured gate 1 fires: ASTS, BETA, CIFR, EOSE, OKLO, RIVN.
Measured gate 2 fires (`netDebt/ocf`): ORA 8.47, CEG 4.74, IREN 4.46, VST 4.26, ORCL 4.24.
Measured gate 3 fires: ARM, BABA, DLO, DVN, ORA, ORCL, UNH.

### Applying the cap

```python
cap = veto_cap  # from veto_gates(), or None if no gate fired
if fund_score is None:
    combined, recommendation = None, "No Data"
else:
    combined = round(tech_score * 0.40 + fund_score * 0.60, 1)
    if cap is not None:
        combined = min(combined, cap)
        fund_score = min(fund_score, cap)
```
Capping only `fund_score` is 40% defanged: at `tech >= 55`, `tech*0.40 + min(fund,30)*0.60` still
clears 39 (Sell ceiling) for 13 of 53 tickers today. Capping **both** — `min()` applied to the
fund input and to the combined output — is the strictest of the placements and closes that escape
hatch.

### `forwardEps < 0` — display only, not a gate

Changes zero buckets across all 53 tickers today, and gating on it would cap NBIS and IREN (30.59%
of the portfolio) on an accounting loss during a capex-funded buildout — exactly what decision 1
removed from the veto path. Show it as a narrative line only.

### Other fundamental-score fixes (small, independent of the above)

- `compute_fundamental_score`: `if not fund: return None, []` (was `return 50, []`) — a ticker with
  no fundamentals data must abstain, not read "Hold".
- `_sector_relative_score` tail: replace the flat `return 0` for `ratio > 2.0` with
  `return round(60 / ratio**1.5)` — today 15 of 53 tickers pin at exactly 0 on P/E and 28 of 53 on
  P/S with no ordering (PANW at 10.6x median scores identically to a name at 2.1x).
- `_score_tier`: delete the dead `_score_tier(gm, [(-999, 0)])` branch at `:783` (unreachable —
  `_score_tier(None, ...)` returns 50 before reading any tier) and give every tier a real floor so
  extremes can reach 0 and the best case can reach 100 (currently P/B of 500 scores identically to
  P/B of 9.5).
- PEG flatter-guard: if the PEG sub-score would score >= 75 **and** (`earningsGrowth` is `None`, or
  <= 0.02, or `pegRatio` outside `[0.05, 20]`), clamp the sub-score to 50. Catches NICE (PEG 0.62
  against `earningsGrowth` of -0.617 — a shrinking-earnings value trap, not a bargain) without the
  symmetric version's problem of handing out undeserved upgrades to UBER/IBM/ELV.

## Final technical score

```python
pos52 = (price - low_52w) / (high_52w - low_52w) * 100
slope = (ma150[-1] / ma150[-21] - 1) * 100
trend = clamp(pos52 + (8 if slope > 1 else -8 if slope < -1 else 0), 0, 100)

ppo = (ema12[-1] - ema26[-1]) / ema26[-1] * 100
macd_score = 50 + 40 * min(1, abs(ppo) / 3.6) * (1 if ppo > 0 else -1)

tech_score = 0.70 * trend + 0.30 * macd_score
```
Eight lines. `high_52w`/`low_52w` are already computed (`:359-360`) and already displayed on the
dashboard as `rangePct` (`docs/index.html:536`) — the best-performing technical variable in this
owner's universe was already on screen, unscored. Correlates +0.856 with a fuller 6-cell
position×slope matrix that was tried and rejected: that matrix crosses its own boundaries 262
times/year across the book (67% reverting within 10 days — the death-cross bug with a bigger
hammer), scores `above/falling` as neutral when it's measurably the worst of the six states, and
has two of six cells empty under a strict-sign slope test.

`3.6` (not `2.0`) is the PPO magnitude normalizer — the real |PPO| distribution across the 53
tickers has p75 at 3.62; at a 2.0 normalizer, 58% of the book saturates at the extreme. Sign +
magnitude beats a sign×histogram-slope matrix decisively (rank IC +0.1032 vs +0.0322) — the
histogram-slope dimension alone measured IC of -0.0024 (zero) and flips state 48.7 times/ticker/yr,
61% reverting within 10 days.

**Deleted entirely** (0 dashboard consumers — `grep` confirms `adx`, `mfi`, `obv`, `stoch_k`,
`stoch_d`, `bb_upper`, `bb_lower`, `bb_middle`, `macd`, `macd_hist`, `macd_signal` all have 0
references in `docs/index.html`, vs `rsi` 24, `sma50`/`sma200` 12 each):
`compute_stochastic`, `compute_mfi`, `compute_adx`, `compute_bollinger_bands`, `compute_obv`,
`detect_obv_divergence`, and every call site, returned key, and signal string they feed. Also
delete the golden/death-cross **scoring** term (fully implied by 150MA position + slope, and lags
it by ~50 sessions) and the two "Death/Golden Crosses Identified" prompt sections (`:1078-1079`) —
removing the data without removing the prompt's mandate makes Gemini invent crosses from nothing.

**Kept, unscored** (dashboard/prompt still read these by name): `rsi`, `sma50`, `sma200`,
`high_52w`, `low_52w`, `chart`.

**Guard required:** SMA150 needs `if len(closes) >= 171` before indexing `ma150[-21]` — without it,
any ticker with under 171 daily bars (BETA has 209 total bars but its SMA150 series only has ~59
values) throws `IndexError` straight into the bare `except` at `:506` and silently returns `None`
for the whole ticker.

## Thematic concentration

```python
TAG = {t: name for name, tickers in cfg["themes"].items() for t in tickers}
expo = {name: sum(weights.get(t, 0) for t in tickers) for name, tickers in cfg["themes"].items()}
expo["untagged"] = sum(v for t, v in weights.items() if t not in TAG)
```
`config.json`, flat, one theme per ticker, portfolio and watchlist names in the same list, no
weights, no nesting:
```json
"themes": {
  "ai-datacenter": ["NBIS","IREN","CIFR","ORCL","NVDA","ASML","VST","OKLO","CEG",
                    "AVGO","ANET","DELL","TSM","AMD","MU","VRT","ARM","ORA","EOSE","PLTR"],
  "megacap-platform": ["AMZN","GOOG","MSFT"],
  "payments-fintech": ["DLO","MA","PGY","SOFI","V"],
  "enterprise-software": ["ZETA","CRM","NOW","IBM","INTU","NICE"],
  "health-insurer": ["UNH","ELV"]
}
```
Verified off the committed config: ai-datacenter 44.72%, megacap-platform 15.38%, payments-fintech
9.23%, enterprise-software 7.30%, health-insurer 4.75%, untagged 23.24%. `enterprise-software` and
moving V into `payments-fintech` were both found by an offline market-residual correlation audit
(MA/V residual +0.863, the strongest pair in the whole matrix) — the original 4-tag draft parked a
real 7.30% cluster entirely in "untagged".

Warn when a theme's exposure >= 25%. Annotate an over-exposed watchlist Buy/Strong Buy with
`TAG.get(ticker)` (never bare `TAG[ticker]` — it `KeyError`s on 11 of 25 watchlist names, including
INTU, the #2 pick in the universe). Keep the existing single-name `alloc > 10` check — NBIS alone at
18% is a real flag a themes-only replacement would silently drop. Do not attempt correlation
clustering in production: tested against 63-day chart data, single-link clustering at r>=0.50 makes
NBIS — the largest position — a singleton, because 63 bars of a calm regime measures average
co-movement while concentration risk lives in the tail (on the AI basket's worst day in the window,
all seven fell together for a basket move of -8.88% against a market of -5.02%).

## Dynamic portfolio weights

`config.json`'s portfolio percentages are static and currently sum to 97.32% — every allocation
figure in every briefing is already wrong and drifts further with each price move.

```json
"portfolio": {
  "NBIS": {"shares": 450},
  "IREN": {"shares": 800}
}
```
```python
def compute_weights(portfolio, technicals):
    """Live allocation % from share counts and current prices — no new API calls,
    price is already fetched into technicals[ticker]['price']."""
    vals = {t: h["shares"] * ((technicals.get(t) or {}).get("price") or 0)
            for t, h in portfolio.items()}
    total = sum(vals.values())
    return {t: round(v / total * 100, 2) for t, v in vals.items()} if total else {}
```
Call once after the fetch loop in `main()`. Six call sites swap `portfolio[t]` for `weights[t]`:
`:928, :944, :1017, :1024, :1036, :1199`. Membership checks (`in portfolio`, `.keys()`) are
untouched — it stays a dict keyed by ticker. This also makes theme exposure a valid monitor-mode
trigger (see below) — with static percentages it cannot fire on anything but a config edit, since
the allocation vector took exactly 2 distinct values across 162 measured runs.

## Monitor mode

**Invariant — every trigger is an edge against prior state, never a level.** Compare
`prev[ticker][field]` to `cur[...]`, or key a `seen` set on event identity and discard it when the
condition clears. No trigger may test a current-state predicate. (This is the death-cross bug,
generalized — stating it as an invariant is what stops the bug class returning one trigger at a
time. Measured: "earnings within 7 days" as a level fires 6.02 tickers/run, non-silent on 85% of
runs; as an edge, 0.47/run, silent on 82%.)

### Trigger list — portfolio only, `weight >= 2.0`

| # | Trigger | Debounce | Measured |
|---|---|---|---|
| 1 | Recommendation bucket flip | 3-point hysteresis band on `combined_score` | 21/162 runs |
| 2 | SMA50/SMA200 sign flip | edge, none needed | 0.09/run |
| 3 | New 52-week high or low | 21-day suppression | 0.23/briefing |
| 4 | Veto gate newly fires or clears | edge on stored gate list | ~0.05/run |
| 5 | Earnings entering the 0-3 day window | keyed on `(ticker, earnings_date)` | 39/162 runs |
| 6 | Distribution: `rvol5 >= 1.5 and dir5 <= -2` newly true | edge | <3/run |
| 7 | `DATA_GAP` — was scored last run, now `None` | unconditional, no weight gate | 0.02/run |
| 8 | Theme score <= `EMA(theme_score, alpha=0.1) - 3.0` | needs >=70% of members scored | 3/162 runs |
| 9 | Theme exposure (dynamic weights) crosses 25% | edge, hysteresis 2pt | new — untested live |

Trigger 9 only exists once dynamic weights (above) ship — static percentages can't drift, so this
trigger was rejected by the original review and is added back here once Phase 4 lands.

**3-point hysteresis is the single highest-leverage line in this design.** Raw bucket flips run
526/162 runs (3.27/ticker/run); median |delta-combined| at the flip is 2.40, and 40% of flips move
under 2 points. 1pt band -> 264 fires, 2pt -> 138, **3pt -> 84**, 5pt -> 36 (over-damped). Of all 526
flips measured, zero were on the sell side.

**A ticker absent from `prev` is baselined silently and never reported** — the watchlist grew
35 -> 42 -> 46 -> 53 during the measurement window; without this rule those edits alone emit 18
false exceptions.

### Rejected as triggers (measured, not assumed)

- **Price moves, any threshold** — `|1d| >= 5%` fires 9.21x/day (0% of days silent); even `>=12%`
  is 0.61/day. 48% of all alert volume for information the owner's broker app already showed him.
  Also incorrect on the data: run-to-run intervals vary 6h-3 days, so a raw diff isn't a daily
  return.
- **tech_score band change** — 642 changes in 120 days, 57% flapping; the new continuous score
  makes this *worse* (672), proving score bands are the wrong trigger shape regardless of the
  scoring fix.
- **MA150 side change (raw)** — 504/yr. A confirmed version (2% + 5 closes) costs 8 lines of
  debounce to say roughly what the SMA50/200 flip already says for free.

### State mechanism

```python
prev = {}
try:
    prev = json.load(open(DATA_PATH))["tickers"]
except (OSError, ValueError, KeyError):
    pass
```
At the top of `main()`, before any fetch — `save_market_data` is the only writer, so the on-disk
file is always the prior run's output. Empty `prev` (first run, corrupt file) baselines every
ticker and fires zero exceptions, with no special-casing needed.

**Not `git show HEAD:...`** — verified byte-identical to the working-tree file after a clean
`actions/checkout@v4` (both 322,495 bytes); it buys nothing while adding a subprocess and a
shallow-clone failure mode.

**The real state bug:** `main()` currently calls `save_market_data` *before* `send_telegram`. Once
state advances, a failed send loses that run's alert permanently — the next run diffs against the
new baseline and sees no change. Fix: compute the exception list before the save, store it under
`"alerts_pending"`, and only clear that key after `send_telegram` returns successfully. Next run's
trigger set is `new | prev.get("alerts_pending", [])`, deduped on `(ticker, trigger)`.

### Quiet-day output and cadence

```
Nothing changed. 28 positions, ai-datacenter 44.7%. Next earnings: NBIS 8/12.
```
~120 chars against a measured average of 6,533 (n=181; 98% exceeded the 3,900-char instruction the
model was told to follow). Sunday morning always sends a full briefing regardless of exceptions, so
the owner sees the whole book at least weekly. Total silence is rejected — a dead bot is
indistinguishable from a calm market, and this one was already dead 30 days without being noticed.

**Delete the 18:00 ET cron.** It scores every ticker against a partial, still-forming daily candle
— that partial bar moves `combined_score` about as much as an entire overnight session does (mean
|delta| 0.96 within-day vs 0.97 across-day) and *more* at the median (0.60 vs 0.20), manufacturing
state changes that monitor mode would then faithfully report as news. Costs 0 lines; the 7:30 AM
cron's stated keep-alive purpose already satisfies itself with one run/day.

## Decisions (resolved 2026-09-04)

1. **NBIS becomes un-vetoable on fundamentals, by design — accepted.** At 18.13% of the portfolio
   (the largest position), with the price target removed and `forwardEps` demoted to narrative,
   NBIS passes every gate: OCF +2.84B (no burn), `netDebt/ocf` 0.076 (no leverage — 56x better than
   ORCL's 4.24), gross margin +179pp (no erosion), F-score 6/8. It reads Buy. **Owner's call: no
   fundamentals veto for NBIS specifically — theme-exposure tagging (ai-datacenter, 44.72%) is the
   sole check on it.** The gates are implemented exactly as specified with no NBIS-specific
   carve-out; this is the natural, intended result of applying them, not a special case.
   Sharper context that motivated asking: **IREN's `netDebt/ocf` is 4.46 — worse than ORCL's
   4.24** — so the leverage gate caps IREN at Hold while NBIS sails through; on the balance sheet,
   IREN is ORCL, not NBIS.
2. **The leverage gate (4.0) is ~60% a Utilities screen — kept as-is, no exemption.** It fires on
   ORA 8.47, CEG 4.74, IREN 4.46, VST 4.26, ORCL 4.24 — three of five are Utilities. Raising the
   threshold to 5.0 would drop it to firing on only ORA/CEG and **let ORCL escape**, defeating the
   gate's purpose. No sector-relative version is computable (Utilities n=4, Energy n=1 in this
   universe — either noise or tautology). **Owner's call: VST and CEG are held for the AI-power
   growth thesis, not yield — keep the gate at 4.0.** VST and CEG are permanently capped at Hold on
   leverage; this is treated as correct, not a false positive to work around.
3. **Cap level: 59** (not 60) for gates 2 and 3 — 59 demotes to Hold (real bucket changes: UNH
   70.8 -> 59, BABA 79.9 -> 59); 60 would be a no-op against the existing Buy threshold. Default
   accepted, not re-litigated. Cheap to change later if the first month of alerts argues for it.
