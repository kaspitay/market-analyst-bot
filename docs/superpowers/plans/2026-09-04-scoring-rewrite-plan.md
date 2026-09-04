# Scoring & Briefing Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Tasks 0 and 1 are complete — do not redo them, just
> confirm the verification evidence below still holds (run `python3 check_scores.py`) before
> starting Task 2.

**Goal:** Rewrite `analyzer.py`'s fundamental score (real F-score + capex-aware veto gates),
technical score (lean 150MA/MACD blend), thematic concentration, and portfolio weights, then turn
the twice-daily Telegram briefing into a silent-by-default, exception-triggered monitor.

**Architecture:** Six phases, each gated by `python3 check_scores.py` (must show only explained
diffs — zero before any scoring change lands) plus a phase-specific live-fetch or state assertion.
Single file (`analyzer.py`), no new dependencies, no new modules — see Global Constraints.

**Tech Stack:** Python 3.12, stdlib + `requests` (existing dependency), Yahoo Finance
(`quoteSummary` + `fundamentals-timeseries`), GitHub Actions cron, Telegram Bot API, Gemini
2.5 Flash Lite.

**Spec:** `docs/superpowers/specs/2026-09-04-scoring-rewrite-design.md` — every formula, threshold,
and measured number below is argued there; read it first. A readable illustrated version is also
published at https://claude.ai/code/artifact/76c9221a-7ea3-4483-aa5e-128b19e775c7.

## Global Constraints

- No new dependencies — `requirements.txt` stays at one line (`requests`).
- No new files beyond `check_scores.py` (done) unless a task below says otherwise.
- `check_scores.py` imports `analyzer` with `requests` stubbed via `sys.modules.setdefault` — reuse
  that pattern for any one-off verification script in this plan; don't set up a venv.
- Every phase ends with `python3 check_scores.py` — read the diff, don't skip it.
- Never push to `origin` or move off `main` without explicit sign-off in the current turn (repo has
  no feature-branch convention; this is a personal single-dev repo).
- Docs-in-sync: if a task changes what a field means (e.g., quality score denominator), grep for
  and update every place that displays it, in the same task.
- Model guidance (applied by choice, not a hard rule — see `subagent-driven-development` SKILL.md
  "Model Selection"): Tasks 1, 3, 4 are mechanical — a fast/cheap model is fine. Tasks 2 and 5
  involve subtle correctness with real money behind the recommendation — use the most capable
  available model, even above the session default.

---

### Task 0: Regression fixture — DONE (commit `f58bbc3`)

**Files:** Created `check_scores.py`.

- [x] **Step 1: Write the fixture** — replays `docs/data/market-data.json`'s stored
  `fundamentals`/`price`/`target_mean`/`num_analysts`/`sector` through `compute_quality_score` and
  `compute_fundamental_score`, diffs against stored `quality_score`/`fund_score`/`combined_score`/
  `recommendation`.
- [x] **Step 2: Prove the harness can fail** — `--self-test` mutates `analyzer.THRESHOLDS["buy"]` to
  1, confirms flips are detected, reverts, confirms clean again. Evidence:
  `self-test PASS: clean baseline, 23 flips detected under mutation, clean again after revert.`
- [x] **Step 3: Confirm the clean baseline** — `python3 check_scores.py` → `changed=0 flips=0
  abstained=0` against unmodified `analyzer.py`.
- [x] **Step 4: Commit** — `f58bbc3`.

---

### Task 1: Financial history plumbing — DONE (commit `f58bbc3`)

**Files:** Modified `analyzer.py:645-698` (`fetch_financial_history`).

- [x] **Step 1: Add six Yahoo timeseries types** — `annualCapitalExpenditure`,
  `annualOperatingCashFlow`, `annualFreeCashFlow`, `annualTotalAssets`, `annualTotalDebt`,
  `annualDilutedAverageShares` appended to the `types` list.
- [x] **Step 2: Add six fields to the per-year record**, `abs()` capex (Yahoo reports it negative),
  and an OCF fallback (`fcf + capex` when Yahoo omits OCF directly — recovers ASML).
- [x] **Step 3: Verify live** — fetched all 53 tickers for real. Coverage: capex 205/211, OCF
  205/211 (201 raw + 4 recovered by the fallback), FCF 205/211, totalAssets 209/211, totalDebt
  203/211, dilutedAverageShares 210/211. Confirmed the MSFT FY2026 identity:
  `182.935B OCF - 115.948B capex ~= 66.987B FCF`. Gaps found: EOS missing capex/OCF/FCF/debt
  entirely (4 years), ANET missing `totalDebt` (3 years), BETA missing several fields (2 years) —
  real data holes Task 2's abstain logic must handle, not bugs in the fetch.
- [x] **Step 4: Fixture gate** — `python3 check_scores.py` → zero score changes (this task only adds
  fetched fields; it touches no scoring math).
- [x] **Step 5: Commit** — `f58bbc3` (same commit as Task 0).

---

### Task 2: Fundamental score rewrite

**Files:**
- Modify: `analyzer.py:730-748` (`compute_quality_score`)
- Modify: `analyzer.py:753-840` (`compute_fundamental_score`)
- Modify: `analyzer.py:842-897` (`merge_fundamentals`) — thread `financial_history` through
- Modify: `analyzer.py:1281-1286` (`main()`'s fetch loop) — fetch history before scoring
- Modify: `config.json` — add `"ocf_veto_exempt": ["NU", "SOFI"]`

**Interfaces:**
- Consumes: `technicals[ticker]["financialHistory"]` (list of per-year dicts, from Task 1) —
  needs to reach `compute_fundamental_score` and `compute_quality_score`, which currently only
  receive `fund` (the flat fundamentals dict). Both functions gain a `financial_history=None` kwarg.
- Produces: `compute_quality_score(fund, financial_history)` returns `(score_0_to_8_or_None,
  details_list)`. `compute_fundamental_score(...)` returns `(score_or_None, reasons_list)` — `None`
  on abstain (was: always a number, defaulting to 50). `merge_fundamentals` sets
  `technicals["fund_score"]` to that same `None`-or-number, and `technicals["recommendation"]` to
  `"No Data"` when `fund_score` is `None`. Later tasks (3, 5) read `fund_score`/`recommendation` and
  must handle `None`/`"No Data"`.

- [x] **Step 0: Confirm the two blocking decisions are answered.** Answered 2026-09-04:
  1. **No fundamentals veto for NBIS specifically.** It earns its Buy legitimately on the numbers
     (no cash burn, `netDebt/ocf` 0.076, improving margins) — theme-exposure tagging (Task 4,
     ai-datacenter at 44.72%) is the sole check on it, by explicit owner choice. `veto_gates()` gets
     no NBIS-specific carve-out or extra clause; this is the natural result of the gates as
     specified, not a special case to code.
  2. **Leverage gate stays at `netDebt/ocf > 4.0`, no Utilities exemption.** VST and CEG are held
     for the AI-power growth thesis, not yield — a permanent Hold ceiling on leverage is accepted
     as correct for both. `OCF_VETO_EXEMPT` stays `["NU", "SOFI"]` only; do not add ORA/CEG/VST/IREN.
  (Decision 3 — cap level 59 vs 60 — default: ship 59. Not blocking, wasn't re-asked.)

- [ ] **Step 1: Write the verification script (red)**

  Create a throwaway `/tmp/verify_task2.py` (not committed — this is a one-off phase check, not a
  permanent fixture flag):
  ```python
  import sys, types, json
  sys.modules.setdefault("requests", types.ModuleType("requests"))
  sys.path.insert(0, ".")
  import analyzer

  data = json.load(open("docs/data/market-data.json"))["tickers"]
  gated, abstained = [], []
  for tkr, v in sorted(data.items()):
      tech = (v or {}).get("technicals") or {}
      fund = tech.get("fundamentals") or {}
      fh = tech.get("financialHistory")
      score, reasons = analyzer.compute_fundamental_score(
          fund, tech.get("price"), tech.get("target_mean"), tech.get("num_analysts"),
          sector=fund.get("sector"), financial_history=fh,
      )
      if score is None:
          abstained.append(tkr)
      elif score <= 59.05:
          gated.append((tkr, score))

  print(f"gated: {len(gated)}  abstained: {len(abstained)}")
  for t, s in gated: print(f"  {t}: {s}")
  # Expect ORCL near 59 (was 80.9), NBIS NOT in this list, IREN gated on leverage,
  # NU and SOFI NOT gated (exempt), SOFI/EOS in `abstained` not `gated` (ocf is None -> skip, not clear).
  ```
  Run it now, before touching `compute_fundamental_score` — expect it to crash (`financial_history`
  kwarg doesn't exist yet) or reproduce the *old*, wrong numbers (ORCL ~80.9). That confirms the
  script is wired to real code before you change that code.

- [ ] **Step 2: Rewrite `compute_quality_score`** to the 8-signal F-score from the spec doc's
  "Health" section — ROA, CFO, ΔROA, CFO>netIncome, Δleverage, dilution<=1%, Δgross margin,
  Δrevenue/assets — each signal abstaining (excluded from the denominator) on missing data, never
  scoring false. Signature becomes `compute_quality_score(fund, financial_history=None)`; with
  `financial_history` absent or too short (<2 years), every history-dependent signal abstains and
  only signals 1-2 (ROA, CFO — computable from `fund` alone if it had `totalAssets`, which it
  doesn't currently — so in practice with no history, health is fully unscored; keep that
  behavior, don't add a fallback to `fund`'s own fields to fake coverage). Docstring: score is now
  0-8, not 0-7.

- [ ] **Step 3: Update the two `/7` call sites in `compute_fundamental_score`** (`:833, :835, :837`)
  to `/8`, and thread `financial_history` in: change the signature to
  `compute_fundamental_score(fund, price, target_mean, num_analysts, sector=None,
  financial_history=None)`, pass it to the `compute_quality_score` call inside.

- [ ] **Step 4: Add `veto_gates(fund, financial_history, ticker, ocf_veto_exempt)`** per the spec
  doc's exact code (cash burn / leverage / margin erosion, each `None`-input-abstains). Read
  `ocf_veto_exempt` from `config.json` in `main()` and pass it through — do not hardcode the exempt
  list in `analyzer.py`.

- [ ] **Step 5: Apply the cap** inside `compute_fundamental_score` (or immediately after its call in
  `merge_fundamentals` — pick whichever keeps the `min()` closest to both the `fund_score` value and
  the final `combined` value, per the spec's "Applying the cap" section) so a fired gate caps *both*
  `fund_score` and the eventual `combined_score`, not just one.

- [ ] **Step 6: Fix `compute_fundamental_score`'s abstain and tier issues** — `if not fund: return
  None, []` (was `return 50, []`); `_sector_relative_score`'s `> 2.0` tail becomes `round(60 /
  ratio**1.5)`; delete the dead `_score_tier(gm, [(-999, 0)])` branch and add real floors/ceilings
  to `_score_tier`'s tier lists; add the PEG flatter-guard (score >= 75 AND (`earningsGrowth` is
  `None` or <= 0.02 or `pegRatio` outside `[0.05, 20]`) -> clamp to 50).

- [ ] **Step 7: Thread `financial_history` through `merge_fundamentals`** and reorder `main()`'s
  fetch loop so `fetch_financial_history` runs before `merge_fundamentals`:
  ```python
  fund_data = fetch_fundamentals(ticker)
  fh = fetch_financial_history(ticker)
  technicals[ticker] = merge_fundamentals(technicals[ticker], fund_data, fh)
  ```
  Update `merge_fundamentals`'s signature to `(technicals, fund_data, financial_history=None)`,
  store `technicals["financialHistory"] = financial_history` there (replacing the old post-hoc
  assignment), and pass `financial_history` into its `compute_fundamental_score` call. Update
  `technicals["recommendation"]` to `"No Data"` when `fund_score` is `None` instead of calling
  `score_to_recommendation` on `None`.

- [ ] **Step 8: Add `"ocf_veto_exempt": ["NU", "SOFI"]` to `config.json`.**

- [ ] **Step 9: Run the verification script (green)** — re-run `/tmp/verify_task2.py`. Confirm:
  18 of 53 gated, covering ~49.17% of allocation; ORCL at or below 59 (was 80.9); NBIS **not**
  gated; IREN gated on leverage; NU and SOFI **not** gated; SOFI and EOS appear in `abstained`, not
  cleared silently; zero `TypeError` on the 7 tickers with null `grossProfit`/`operatingIncome`.
  Separately assert `netDebt/ocf`: ORCL ~4.24, NBIS ~0.076, TSM negative (net cash).

- [ ] **Step 10: Regression fixture** — `python3 check_scores.py`. This time diffs are *expected and
  correct* (the scoring logic changed on purpose) — read every flip, confirm each one matches a gate
  or F-score change you intended, not something you didn't touch.

- [ ] **Step 11: Commit.**
  ```bash
  git add analyzer.py config.json
  git commit -m "Rewrite fundamental score: real F-score, capex-aware veto gates, abstain on missing data"
  ```

---

### Task 3: Technical score rewrite

**Files:**
- Modify: `analyzer.py:159-292` (delete `compute_stochastic`, `compute_mfi`, `compute_adx`,
  `compute_bollinger_bands`, `compute_obv`, `detect_obv_divergence`)
- Modify: `analyzer.py:295-509` (`fetch_technicals`) — delete their call sites and returned keys,
  add SMA150 + PPO + the new blend, fix `vol_avg_20`
- Modify: `analyzer.py:1078-1079` (prompt) — delete the cross-signal sections

**Interfaces:**
- Consumes: nothing new from earlier tasks.
- Produces: `tech_score` (0-100, was already this shape — Task 2 already consumes it as an opaque
  number, no change needed there). New keys on the technicals dict: `pos52`, `ppo` (replaces
  `macd`/`macd_signal`/`macd_hist`, which are deleted). `signals` list loses the cross entries.

- [ ] **Step 1: Confirm dashboard consumers before deleting anything** —
  `grep -o "\\bt\\.\\(adx\\|mfi\\|obv\\|stoch_k\\|stoch_d\\|bb_upper\\|bb_lower\\|bb_middle\\|macd\\|macd_hist\\|macd_signal\\)\\b" docs/index.html`
  must return nothing (already confirmed in the spec doc — re-confirm before you delete, don't trust
  a stale grep). `rsi`, `sma50`, `sma200`, `high_52w`, `low_52w`, `chart` must stay.

- [ ] **Step 2: Delete `compute_stochastic`, `compute_mfi`, `compute_adx`,
  `compute_bollinger_bands`, `compute_obv`, `detect_obv_divergence`** and their call sites inside
  `fetch_technicals`, plus every key they populate in the returned dict and in `score_reasons`/
  `signals`.

- [ ] **Step 3: Add SMA150 + slope, with the length guard.**
  ```python
  sma150 = round(sum(closes[-150:]) / 150, 2) if len(closes) >= 150 else None
  ma150_series = [sum(closes[i-149:i+1]) / 150 for i in range(149, len(closes))] if len(closes) >= 171 else []
  slope150 = (ma150_series[-1] / ma150_series[-21] - 1) * 100 if len(ma150_series) >= 21 else None
  ```
  The `>= 171` guard on `ma150_series` is required — without it, a ticker with under 171 daily bars
  indexes `ma150_series[-21]` out of range and throws `IndexError` into the bare `except` at `:506`,
  silently returning `None` for the whole ticker (this is what happens to BETA today if the guard is
  missing).

- [ ] **Step 4: Add `pos52`, `trend`, `ppo`, `macd_score`, and the blend** exactly per the spec doc's
  "Final technical score" formulas. `ema12`/`ema26` are already computed (`:326-327` originally —
  check current line numbers after Step 2's deletions). Replace the returned `macd`/`macd_signal`/
  `macd_hist` keys with `ppo`.

- [ ] **Step 5: Fix `vol_avg_20`'s self-inclusion bug** — change `volumes[-20:]` to `volumes[-21:-1]`
  wherever the current 20-day average is computed (it currently includes the day being tested in its
  own baseline).

- [ ] **Step 6: Delete the golden/death-cross scoring term** from `ma_raw` and the corresponding
  `signals.append("golden cross"/"death cross")` lines, and delete the two prompt sections at
  `:1078-1079` ("Death Crosses Identified" / "Golden Crosses Identified") in the same step — leaving
  the prompt's mandate in place after removing the data makes Gemini invent crosses from nothing.

- [ ] **Step 7: Verify live** — run `fetch_technicals` for all 53 tickers against real Yahoo data
  (same pattern as Task 1's Step 3). Confirm every ticker gets a non-`None` `tech_score`, including
  BETA specifically (the guard-required case). Spot-check 3-4 tickers' `pos52`/`ppo`/`tech_score`
  against their actual charts for sanity.

- [ ] **Step 8: Regression fixture** — `python3 check_scores.py`. `tech_score` is a stored **input**
  to the fixture, not recomputed by it (the fixture only replays the fundamental half + combine), so
  this step should show the *same* diffs as Task 2 left it — if it shows new ones, something in this
  task touched the fundamental path by accident.

- [ ] **Step 9: Commit.**
  ```bash
  git add analyzer.py
  git commit -m "Rewrite technical score: 150MA position+slope and PPO-normalized MACD, delete 6 unused indicators"
  ```

---

### Task 4: Themes + dynamic portfolio weights

**Files:**
- Modify: `config.json` — add `"themes": {...}` (5 clusters per spec doc); convert `"portfolio"`
  from `{ticker: pct}` to `{ticker: {"shares": N}}`
- Modify: `config.example.json` — same shape change, for documentation
- Modify: `analyzer.py` — add `compute_weights()`; add `TAG`/`expo` construction; swap
  `portfolio[t]` for `weights[t]` at `:928, :944, :1017, :1024, :1036, :1199` (line numbers will
  have shifted from Tasks 2-3 — re-`grep` for `portfolio\[` before editing, don't trust these
  numbers blindly)
- Modify: `analyzer.py:1004`-ish (`wl_picks` loop) — add the `TAG.get()` annotation

**Interfaces:**
- Consumes: `technicals[ticker]["price"]` (already present, no new fetch).
- Produces: `weights` dict (ticker -> float pct, sums to 100.00), used everywhere the old
  `portfolio[ticker]` percentage was used. `TAG` dict (ticker -> theme name or absent). `expo` dict
  (theme name -> summed weight, plus `"untagged"`).

- [ ] **Step 1: You need real share counts, not a guess.** Before writing code, get the owner's
  actual share count per position (or back-compute from the current stored percentages and a
  known total portfolio value as a documented one-off — flag clearly in the commit message that
  these are back-computed and may drift from reality). Do not silently invent numbers.

- [ ] **Step 2: Convert `config.json`'s `"portfolio"`** from `{"NBIS": 18.13, ...}` to
  `{"NBIS": {"shares": 450}, ...}` using the real (or documented back-computed) share counts. Same
  shape change in `config.example.json` with clearly fake illustrative numbers.

- [ ] **Step 3: Write `compute_weights(portfolio, technicals)`** exactly per the spec doc — six
  lines, no new API calls (reads `technicals[t]["price"]`, already fetched).

- [ ] **Step 4: Call `compute_weights` once in `main()`** right after the fetch loop, store the
  result as `weights`, and swap every numeric read of `portfolio[ticker]`/`portfolio.get(ticker)`
  (grep for `portfolio\[` and `portfolio\.get` — NOT `in portfolio` or `.keys()`, which stay as
  membership checks against the unchanged ticker-keyed dict) to read from `weights` instead.

- [ ] **Step 5: Add `"themes"` to `config.json`** — the 5 clusters from the spec doc
  (`ai-datacenter`, `megacap-platform`, `payments-fintech`, `enterprise-software`,
  `health-insurer`), with `enterprise-software` included and `V` under `payments-fintech` (both
  corrections already folded into the spec doc — don't use an earlier 4-tag draft).

- [ ] **Step 6: Build `TAG`/`expo`** per the spec doc's two-line construction, computed from
  `weights` (not the old static percentages). Add the `expo["untagged"]` line.

- [ ] **Step 7: Annotate `wl_picks`** with `TAG.get(ticker)` (never bare `TAG[ticker]`) when a
  watchlist Buy/Strong Buy belongs to a theme already >= 25% exposed — one clause, e.g.
  `(adds to ai-datacenter, already 44.7%)`. Keep the existing `alloc > 10` (now `weight > 10`)
  single-name check untouched.

- [ ] **Step 8: Verify** — assert the five theme sums match the spec doc's measured numbers
  (ai-datacenter 44.72%, megacap-platform 15.38%, payments-fintech 9.23% + V's addition,
  enterprise-software 7.30%, health-insurer 4.75%); assert no ticker appears in two theme lists;
  assert every tagged ticker exists in the 53-name universe; assert `weights` sums to 100.00; assert
  an untagged `wl_picks` entry emits no clause and doesn't raise.

- [ ] **Step 9: Regression fixture** — `python3 check_scores.py`. Expect **no new diffs** beyond
  what Task 2 introduced — this task changes weight bookkeeping and theme annotations, not any
  score.

- [ ] **Step 10: Commit.**
  ```bash
  git add analyzer.py config.json config.example.json
  git commit -m "Derive portfolio weights from share count x price; add hand-tagged theme exposure"
  ```

---

### Task 5: Monitor mode

**Files:**
- Modify: `analyzer.py:main()` — read `prev` state at top, compute exceptions before the save,
  round-trip `alerts_pending`
- Modify: `analyzer.py:build_prompt` — replace the 9-section mandate with the exception list +
  standing facts; delete RSI-bucket and cross-signal sections (cross sections already deleted in
  Task 3)
- Modify: `.github/workflows/market-analysis.yml` — delete the 18:00 ET cron trigger

**Interfaces:**
- Consumes: `weights`/`expo` (Task 4), `fund_score`/`recommendation`/`None`-abstain (Task 2),
  `tech_score`/`pos52`/`ppo` (Task 3).
- Produces: `docs/data/market-data.json` gains `"alerts_pending"` at the top level (list of
  `{ticker, trigger, detail}`).

- [ ] **Step 1: Read prior state.**
  ```python
  prev = {}
  try:
      prev = json.load(open(DATA_PATH))["tickers"]
  except (OSError, ValueError, KeyError):
      pass
  ```
  At the very top of `main()`, before any fetch call.

- [ ] **Step 2: Implement the 9 triggers** from the spec doc's table, each as an edge against
  `prev[ticker]` (or `prev`-derived theme EMA state for trigger 8), never a level:
  1. Recommendation bucket flip, gated by 3-point hysteresis on `combined_score`.
  2. SMA50/SMA200 sign flip.
  3. New 52-week high/low, 21-day suppression (track last-fired date per ticker in `prev`).
  4. Veto gate newly fires or clears (compare `prev[ticker].get("veto_gate")` to current).
  5. Earnings entering the 0-3 day window, keyed on `(ticker, earnings_date)` so it fires once.
  6. Distribution (`rvol5 >= 1.5 and dir5 <= -2`) newly true — this needs `rvol5`/`dir5` computed
     in `fetch_technicals` (Task 3 didn't add these; add the two-line signed-volume calc here,
     narrative-only, never scored — per the spec doc's explicit rejection of `volume_surge` as a
     scored component).
  7. `DATA_GAP` — ticker was scored last run, `combined_score` is `None` this run. No weight gate.
  8. Theme score <= `EMA(theme_score, alpha=0.1) - 3.0`, only when >=70% of a theme's members have a
     score this run.
  9. Theme exposure (from Task 4's `expo`) crosses 25%, edge with 2-point hysteresis.
  All triggers except 7 and 9 apply only to tickers with `weights[ticker] >= 2.0`.

- [ ] **Step 3: Add the ticker-absent-from-`prev` guard** — if `ticker not in prev`, baseline
  silently (store current state, emit no exception) rather than treating every field as a
  from-scratch change.

- [ ] **Step 4: Reorder the save/send/clear sequence.** Compute the exception list, save it into
  `market-data.json` under `"alerts_pending"` alongside the rest of the data (this save happens
  *before* `send_telegram`, as today). After `send_telegram` returns successfully, re-save with
  `"alerts_pending"` cleared. Next run's trigger set becomes `new_exceptions |
  prev_data.get("alerts_pending", [])`, deduped on `(ticker, trigger)` — this is what stops a failed
  send from silently losing that run's alert forever.

- [ ] **Step 5: Rewrite `build_prompt`** — replace the 9 mandated sections with: market context (kept),
  the exception list (one line per fired trigger), standing facts (theme exposure line, next
  earnings, `wl_picks[:3]` with Task 4's theme annotation), and the quiet-day fallback line when the
  exception list is empty (`"Nothing changed. N positions, <theme> X%. Next earnings: ..."`). Delete
  the RSI-bucket sections and any remaining cross-signal mandate text.

- [ ] **Step 6: Add the Sunday-full-briefing override** — if `date.today().weekday() == 6` (Sunday)
  and it's the morning run, send the full per-position briefing regardless of the exception list.

- [ ] **Step 7: Delete the 18:00 ET cron** from `.github/workflows/market-analysis.yml`, leaving
  only the 7:30 AM ET trigger (and `workflow_dispatch` for manual runs).

- [ ] **Step 8: Verify against real history** — replay the trigger logic over the git history of
  `docs/data/market-data.json` (182 stored versions; 162 runs in the stable window — `git log
  --follow -- docs/data/market-data.json` to enumerate). Confirm: <=1.0 fires/run on average, >=60%
  of runs fully silent, zero exceptions on an empty `prev` (simulate a first run), zero exceptions
  from a two-ticker `config.json` watchlist addition (simulate the Task-4-style edit).

- [ ] **Step 9: Verify the failure path** — force `send_telegram` to raise (e.g. temporarily point
  at an invalid URL in a throwaway run, or monkeypatch in a `/tmp` script) and confirm the next run's
  trigger set still contains that run's exceptions via `alerts_pending`.

- [ ] **Step 10: Regression fixture** — `python3 check_scores.py`. No new diffs expected; this task
  doesn't touch scoring math.

- [ ] **Step 11: Live dispatch** — run one real `pre-market` invocation end-to-end and read the
  actual Telegram output at actual length, not just the simulated trigger count.

- [ ] **Step 12: Commit.**
  ```bash
  git add analyzer.py .github/workflows/market-analysis.yml
  git commit -m "Turn the briefing into an exception-triggered monitor; delete the partial-candle 18:00 run"
  ```

## Completion

After Task 5's commit, use superpowers:finishing-a-development-branch to decide how this lands
(this repo has no feature-branch convention — confirm with the owner whether that skill's
branch-merge flow applies at all, or whether "finished" here just means "five commits on `main`,
not pushed until told to push").
