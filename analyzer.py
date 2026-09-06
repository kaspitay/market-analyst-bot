import os
import sys
import json
import math
import subprocess
import time
import requests
from datetime import date, datetime, timedelta


# --- Sector median P/E and P/S (long-run S&P 500 averages) ---
SECTOR_MEDIANS = {
    "Technology":             {"pe": 30, "ps": 6.0},
    "Healthcare":             {"pe": 22, "ps": 4.0},
    "Financial Services":     {"pe": 14, "ps": 3.0},
    "Consumer Cyclical":      {"pe": 22, "ps": 1.5},
    "Communication Services": {"pe": 18, "ps": 3.5},
    "Industrials":            {"pe": 22, "ps": 2.5},
    "Consumer Defensive":     {"pe": 22, "ps": 2.0},
    "Energy":                 {"pe": 12, "ps": 1.5},
    "Utilities":              {"pe": 18, "ps": 2.5},
    "Real Estate":            {"pe": 35, "ps": 6.0},
    "Basic Materials":        {"pe": 14, "ps": 1.5},
}
DEFAULT_MEDIANS = {"pe": 20, "ps": 3.0}


def _num(v):
    # Yahoo emits the string "Infinity" for ratios whose denominator collapses
    # (trailingPE at ~zero earnings); it must read as missing, not crash the
    # comparisons and f-string formats downstream.
    return v if isinstance(v, (int, float)) and math.isfinite(v) else None


def _sector_relative_score(value, sector_median):
    """Score 0-100 based on ratio to sector median.
    At 0.5x median -> 100, at median -> 60, decaying toward 0 above it."""
    if value is None or value <= 0 or sector_median is None or sector_median <= 0:
        return None
    ratio = value / sector_median
    if ratio <= 0.5:
        return 100
    elif ratio <= 1.0:
        return round(100 - (ratio - 0.5) * 80)   # 100 -> 60
    else:
        # One decay over the whole expensive half. A flat 0 above 2x median left
        # 15 of 53 tickers tied on P/E and 28 on P/S with no ordering; the old
        # linear 60->0 ramp below 2x then inverted against it (1.99x scored 1,
        # 2.01x scored 21). This is continuous at ratio 1.0, where it equals 60.
        return round(60 / ratio ** 1.5)


# --- Score thresholds for recommendations ---
THRESHOLDS = {"strong_buy": 72, "buy": 60, "hold": 40, "sell": 28}

DATA_PATH = os.path.join(os.path.dirname(__file__), "docs", "data", "market-data.json")

# --- Monitor mode: debounce constants (see the spec's "Monitor mode" table) ---
MIN_WEIGHT = 2.0        # per-ticker triggers are portfolio-only, >= 2% of the book
REC_HYSTERESIS = 3.0    # 1: a bucket flip under 3 combined points is noise (526 raw -> 84)
HI_LO_SUPPRESS = 21     # 3: days before the same ticker may report a new 52w extreme again
DISTRIB_RVOL = 1.5      # 6: 5-day volume vs the prior 20-day average
DISTRIB_DIR = -2.0      # 6: net signed volume, in average-days, over those 5 sessions
THEME_EMA_ALPHA = 0.1   # 8
THEME_DROP = 3.0        # 8: points below the theme's own EMA
THEME_COVERAGE = 0.7    # 8: fraction of a theme's members that must be scored
EXPO_LIMIT = 25.0       # 9: theme exposure warning level
EXPO_HYSTERESIS = 2.0   # 9


def _bucket_edges(score):
    """[low, high) score range of the recommendation bucket `score` falls in."""
    edges = sorted(THRESHOLDS.values())
    return (max((e for e in edges if e <= score), default=float("-inf")),
            min((e for e in edges if e > score), default=float("inf")))


def score_to_recommendation(score):
    if score >= THRESHOLDS["strong_buy"]: return "Strong Buy"
    if score >= THRESHOLDS["buy"]: return "Buy"
    if score >= THRESHOLDS["hold"]: return "Hold"
    if score >= THRESHOLDS["sell"]: return "Sell"
    return "Strong Sell"


def load_config():
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    with open(config_path) as f:
        return json.load(f)


def curl_json(url):
    result = subprocess.run(
        ["curl", "-s", url, "-H", "User-Agent: Mozilla/5.0"],
        capture_output=True, text=True, timeout=15,
    )
    return json.loads(result.stdout)


def fetch_company_news(ticker, api_key):
    # A same-day UTC window means the 07:30 ET run only sees 20:00 ET yesterday
    # onward, missing the whole prior trading day (and all weekend on Mondays).
    url = "https://finnhub.io/api/v1/company-news"
    params = {
        "symbol": ticker,
        "from": (date.today() - timedelta(days=4)).isoformat(),
        "to": date.today().isoformat(),
        "token": api_key,
    }
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    # Sort newest-first ourselves rather than relying on Finnhub's ordering.
    articles = sorted(resp.json(), key=lambda a: a.get("datetime") or 0, reverse=True)[:3]
    return [{"headline": a["headline"], "summary": a["summary"]} for a in articles]


def fetch_market_news(api_key):
    url = "https://finnhub.io/api/v1/news"
    params = {"category": "general", "token": api_key}
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    articles = resp.json()[:10]
    return [{"headline": a["headline"], "summary": a["summary"]} for a in articles]


def fetch_fear_greed_data():
    """Fetch Fear & Greed index, market indices, and economic calendar from feargreedmeter.com."""
    try:
        result = subprocess.run(
            ["curl", "-s", "https://feargreedmeter.com/", "-H", "User-Agent: Mozilla/5.0"],
            capture_output=True, text=True, timeout=15,
        )
        html = result.stdout
        start = html.find('id="__NEXT_DATA__"')
        if start == -1:
            return None
        json_start = html.find(">", start) + 1
        json_end = html.find("</script>", json_start)
        data = json.loads(html[json_start:json_end])
        page_data = data["props"]["pageProps"]["data"]

        fgi = page_data["fgi"]["latest"]
        indices = {i["symbol"]: i for i in page_data.get("indexData", [])}
        calendar = page_data.get("calendar", [])

        return {
            "fear_greed": {
                "now": fgi["now"],
                "previous_close": fgi["previous_close"],
                "one_week_ago": fgi["one_week_ago"],
                "one_month_ago": fgi["one_month_ago"],
            },
            "indices": {
                "sp500": indices.get("^GSPC"),
                "dow": indices.get("^DJI"),
                "nasdaq": indices.get("^IXIC"),
            },
            "calendar": [
                {"title": e["title"], "date": e["date"], "description": e.get("description", "")}
                for e in calendar[:5]
            ],
        }
    except Exception as e:
        print(f"Warning: failed to fetch fear/greed data: {e}")
        return None


def fetch_vix():
    try:
        data = curl_json("https://query2.finance.yahoo.com/v8/finance/chart/%5EVIX?interval=1d&range=1d")
        meta = data["chart"]["result"][0]["meta"]
        price = meta["regularMarketPrice"]
        prev = meta["chartPreviousClose"]
        change = price - prev
        pct = (change / prev) * 100 if prev else 0
        return {"price": round(price, 2), "change": round(change, 2), "pct": round(pct, 2)}
    except Exception as e:
        print(f"Warning: failed to fetch VIX: {e}")
        return None


def compute_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def compute_ema(data, span):
    if len(data) < span:
        return []
    alpha = 2 / (span + 1)
    ema = [sum(data[:span]) / span]
    for i in range(span, len(data)):
        ema.append(alpha * data[i] + (1 - alpha) * ema[-1])
    return ema


def fetch_technicals(ticker):
    """Fetch OHLCV from Yahoo Finance, compute comprehensive technical indicators and composite score."""
    try:
        data = curl_json(
            f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1y"
        )
        result = data["chart"]["result"][0]
        meta = result["meta"]
        timestamps = result.get("timestamp", [])
        q = result["indicators"]["quote"][0]

        # Clean OHLCV data — build aligned arrays skipping None values
        closes, highs, lows, opens, volumes = [], [], [], [], []
        valid_timestamps = []
        for i in range(len(timestamps)):
            if q["close"][i] is not None and q["high"][i] is not None and q["low"][i] is not None:
                closes.append(q["close"][i])
                highs.append(q["high"][i])
                lows.append(q["low"][i])
                opens.append(q["open"][i] or q["close"][i])
                volumes.append(q["volume"][i] or 0)
                valid_timestamps.append(timestamps[i])

        current = meta["regularMarketPrice"]
        prev_close = closes[-2] if len(closes) > 1 else current
        change_pct = round((current - prev_close) / prev_close * 100, 2) if prev_close else 0

        # --- INDICATORS ---
        rsi = compute_rsi(closes)
        sma50 = round(sum(closes[-50:]) / 50, 2) if len(closes) >= 50 else None
        sma200 = round(sum(closes[-200:]) / 200, 2) if len(closes) >= 200 else None

        # PPO (12, 26) — normalized MACD, replaces macd/macd_signal/macd_hist
        ema12 = compute_ema(closes, 12)
        ema26 = compute_ema(closes, 26)
        ppo = round((ema12[-1] - ema26[-1]) / ema26[-1] * 100, 4) if ema12 and ema26 and ema26[-1] else None

        # 150-day MA slope (150MA position/slope is the "trend" input below)
        ma150_series = [sum(closes[i-149:i+1]) / 150 for i in range(149, len(closes))] if len(closes) >= 171 else []
        slope150 = (ma150_series[-1] / ma150_series[-21] - 1) * 100 if len(ma150_series) >= 21 else None

        # 52-week high/low
        high_52w = round(max(closes[-252:]), 2) if closes else None
        low_52w = round(min(closes[-252:]), 2) if closes else None

        # Volume average, 20 days prior to the day being tested (excludes that day itself)
        vol_avg_20 = sum(volumes[-21:-1]) / 20 if len(volumes) >= 21 else None

        # Distribution detector — narrative only, never scored (the spec rejects
        # volume_surge as a scored component). rvol5: last 5 sessions' volume against
        # the 20 before them. dir5: net signed volume over those 5 sessions (up days
        # positive, down days negative), expressed in average-days.
        vol_base = sum(volumes[-25:-5]) / 20 if len(volumes) >= 25 else None
        rvol5 = round(sum(volumes[-5:]) / 5 / vol_base, 2) if vol_base else None
        dir5 = round(sum(v if c > p else -v for c, p, v in
                         zip(closes[-5:], closes[-6:-1], volumes[-5:])) / vol_base, 2) if vol_base else None

        # --- SIGNALS ---
        signals = []
        if sma50 and current > sma50: signals.append("above SMA50")
        elif sma50: signals.append("below SMA50")
        if sma200 and current > sma200: signals.append("above SMA200")
        elif sma200: signals.append("below SMA200")
        if rsi and rsi > 70: signals.append("overbought")
        elif rsi and rsi < 30: signals.append("oversold")
        if ppo is not None:
            if ppo > 0: signals.append("MACD bullish")
            else: signals.append("MACD bearish")
        if vol_avg_20 and len(volumes) > 0 and len(opens) > 0:
            if closes[-1] > opens[-1] and volumes[-1] > vol_avg_20:
                signals.append("bullish volume confirmation")
            elif closes[-1] < opens[-1] and volumes[-1] > vol_avg_20:
                signals.append("bearish volume confirmation")

        # --- YTD ---
        ytd_pct = None
        current_year = date.today().year
        for i, ts in enumerate(valid_timestamps):
            if ts:
                from datetime import datetime as dt, timezone as tz
                d = dt.fromtimestamp(ts, tz=tz.utc)
                if d.year == current_year:
                    ytd_pct = round((current - closes[i]) / closes[i] * 100, 2)
                    break

        # --- COMPOSITE SCORE (0-100) ---
        # 150MA position+slope (70%) blended with PPO-normalized MACD magnitude (30%).
        # See docs/superpowers/specs/2026-09-04-scoring-rewrite-design.md "Final technical score".
        score_reasons = []

        pos52 = (current - low_52w) / (high_52w - low_52w) * 100 if high_52w and low_52w and high_52w != low_52w else None
        trend = pos52 if pos52 is not None else 50
        if pos52 is not None:
            score_reasons.append(f"52w range position {pos52:.0f}% (trend)")
            if slope150 is not None and slope150 > 1:
                trend += 8; score_reasons.append(f"150MA rising {slope150:.1f}%/21d (+trend)")
            elif slope150 is not None and slope150 < -1:
                trend -= 8; score_reasons.append(f"150MA falling {slope150:.1f}%/21d (-trend)")
        trend = max(0, min(100, trend))

        macd_score = 50
        if ppo is not None:
            macd_score = 50 + 40 * min(1, abs(ppo) / 3.6) * (1 if ppo > 0 else -1)
            score_reasons.append(f"PPO {ppo:.2f}% — {'bullish' if ppo > 0 else 'bearish'} (macd)")

        composite = round(0.70 * trend + 0.30 * macd_score, 1)

        recommendation = score_to_recommendation(composite)

        # 3-month price history for charts
        chart_data = []
        for i in range(max(0, len(valid_timestamps) - 63), len(valid_timestamps)):
            chart_data.append({"t": valid_timestamps[i], "c": round(closes[i], 2), "v": volumes[i]})

        return {
            "price": current, "prev_close": prev_close, "change_pct": change_pct, "ytd_pct": ytd_pct,
            "rsi": rsi, "sma50": sma50, "sma200": sma200,
            "ppo": ppo, "pos52": round(pos52, 1) if pos52 is not None else None,
            "high_52w": high_52w, "low_52w": low_52w,
            "rvol5": rvol5, "dir5": dir5,
            "signals": signals,
            "score": composite, "recommendation": recommendation, "score_reasons": score_reasons,
            "chart": chart_data,
        }
    except Exception as e:
        print(f"Warning: failed to fetch technicals for {ticker}: {e}")
        return None


_yf_cookie_file = None
_yf_crumb = None


def init_yahoo_auth():
    """Get Yahoo Finance cookie + crumb for authenticated endpoints."""
    global _yf_cookie_file, _yf_crumb
    import tempfile
    try:
        _yf_cookie_file = tempfile.mktemp(suffix=".txt")
        subprocess.run(
            ["curl", "-s", "-c", _yf_cookie_file, "https://fc.yahoo.com/",
             "-H", "User-Agent: Mozilla/5.0"],
            capture_output=True, text=True, timeout=15,
        )
        result = subprocess.run(
            ["curl", "-s", "-b", _yf_cookie_file,
             "https://query2.finance.yahoo.com/v1/test/getcrumb",
             "-H", "User-Agent: Mozilla/5.0"],
            capture_output=True, text=True, timeout=15,
        )
        _yf_crumb = result.stdout.strip()
        print(f"Yahoo Finance auth: crumb={'OK' if _yf_crumb else 'FAILED'}")
    except Exception as e:
        print(f"Warning: Yahoo Finance auth failed ({e}), continuing without price targets")
        _yf_crumb = None


def fetch_fundamentals(ticker):
    """Fetch fundamentals + price targets from Yahoo Finance quoteSummary."""
    global _yf_cookie_file, _yf_crumb
    if not _yf_crumb:
        return None
    try:
        modules = "financialData,defaultKeyStatistics,summaryDetail,earningsTrend,summaryProfile,calendarEvents"
        result = subprocess.run(
            ["curl", "-s", "-b", _yf_cookie_file,
             f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}?modules={modules}&crumb={_yf_crumb}",
             "-H", "User-Agent: Mozilla/5.0"],
            capture_output=True, text=True, timeout=15,
        )
        data = json.loads(result.stdout)
        q = data["quoteSummary"]["result"][0]
        fd = q.get("financialData", {})
        dks = q.get("defaultKeyStatistics", {})
        sd = q.get("summaryDetail", {})
        et = q.get("earningsTrend", {})
        sp = q.get("summaryProfile", {})
        ce = q.get("calendarEvents", {}).get("earnings", {})

        def g(obj, key):
            v = obj.get(key, {})
            return _num(v.get("raw")) if isinstance(v, dict) else None

        # Earnings trend growth rates
        trends = et.get("trend", [])
        cy_growth = None
        ny_growth = None
        try:
            if len(trends) > 2:
                cy_growth = _num(trends[2].get("growth", {}).get("raw"))
            if len(trends) > 3:
                ny_growth = _num(trends[3].get("growth", {}).get("raw"))
        except Exception:
            pass

        price = g(fd, "currentPrice")
        market_cap = g(sd, "marketCap")
        total_revenue = g(fd, "totalRevenue")
        shares = round(market_cap / price) if market_cap and price and price > 0 else None
        rev_per_share = round(total_revenue / shares, 2) if total_revenue and shares else None

        # Extract next earnings date from calendarEvents
        earnings_dates = ce.get("earningsDate", [])
        next_earnings_date = None
        if earnings_dates:
            raw_ts = earnings_dates[0].get("fmt") or earnings_dates[0].get("raw")
            if isinstance(raw_ts, str):
                next_earnings_date = raw_ts
            elif isinstance(raw_ts, (int, float)):
                next_earnings_date = date.fromtimestamp(raw_ts).isoformat()
        earnings_eps_est = g(ce, "earningsAverage")

        return {
            "earnings": {
                "date": next_earnings_date,
                "eps_est": earnings_eps_est,
            },
            "price_target": {
                "target_high": g(fd, "targetHighPrice"),
                "target_low": g(fd, "targetLowPrice"),
                "target_mean": g(fd, "targetMeanPrice"),
                "target_median": g(fd, "targetMedianPrice"),
                "num_analysts": g(fd, "numberOfAnalystOpinions"),
            },
            "fundamentals": {
                # Valuation
                "trailingPE": g(sd, "trailingPE"),
                "forwardPE": g(dks, "forwardPE"),
                "pegRatio": g(dks, "pegRatio"),
                "priceToSales": g(sd, "priceToSalesTrailing12Months"),
                "priceToBook": g(dks, "priceToBook"),
                "enterpriseToEbitda": g(dks, "enterpriseToEbitda"),
                # Profitability
                "grossMargins": g(fd, "grossMargins"),
                "operatingMargins": g(fd, "operatingMargins"),
                "profitMargins": g(fd, "profitMargins"),
                "returnOnEquity": g(fd, "returnOnEquity"),
                "returnOnAssets": g(fd, "returnOnAssets"),
                # Growth
                "revenueGrowth": g(fd, "revenueGrowth"),
                "earningsGrowth": g(fd, "earningsGrowth"),
                "earningsQuarterlyGrowth": g(dks, "earningsQuarterlyGrowth"),
                "currentYearGrowth": cy_growth,
                "nextYearGrowth": ny_growth,
                # Health
                "debtToEquity": g(fd, "debtToEquity"),
                "currentRatio": g(fd, "currentRatio"),
                "freeCashflow": g(fd, "freeCashflow"),
                "totalCash": g(fd, "totalCash"),
                "totalDebt": g(fd, "totalDebt"),
                "operatingCashflow": g(fd, "operatingCashflow"),
                "ebitda": g(fd, "ebitda"),
                "totalRevenue": total_revenue,
                # EPS & other
                "trailingEps": g(dks, "trailingEps"),
                "forwardEps": g(dks, "forwardEps"),
                "beta": g(dks, "beta"),
                "enterpriseValue": g(dks, "enterpriseValue"),
                "marketCap": market_cap,
                "sharesOutstanding": shares,
                "revenuePerShare": rev_per_share,
                "sector": sp.get("sector"),
            },
        }
    except Exception as e:
        print(f"Warning: failed to fetch fundamentals for {ticker}: {e}")
        return None


def fetch_financial_history(ticker):
    """Fetch annual financial history from Yahoo Finance timeseries API."""
    global _yf_cookie_file, _yf_crumb
    if not _yf_crumb:
        return None
    try:
        types = ",".join([
            "annualTotalRevenue", "annualGrossProfit", "annualOperatingIncome",
            "annualNetIncome", "annualDilutedEPS", "annualStockholdersEquity",
            "annualCapitalExpenditure", "annualOperatingCashFlow", "annualFreeCashFlow",
            "annualTotalAssets", "annualTotalDebt", "annualDilutedAverageShares",
        ])
        now = int(time.time())
        period1 = now - (5 * 365 * 86400)
        url = (
            f"https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{ticker}"
            f"?type={types}&period1={period1}&period2={now}&padTimeSeries=true&crumb={_yf_crumb}"
        )
        result = subprocess.run(
            ["curl", "-s", "-b", _yf_cookie_file, url, "-H", "User-Agent: Mozilla/5.0"],
            capture_output=True, text=True, timeout=15,
        )
        data = json.loads(result.stdout)
        series_list = data.get("timeseries", {}).get("result", [])

        # Build a dict of {type_name: {date: value}}
        by_type = {}
        for series in series_list:
            typ = series.get("meta", {}).get("type", [""])[0]
            short = typ.replace("annual", "")
            short = short[0].lower() + short[1:]  # camelCase
            points = series.get(typ, [])
            by_type[short] = {}
            for p in points:
                if p and "reportedValue" in p:
                    by_type[short][p["asOfDate"]] = _num(p["reportedValue"].get("raw"))

        # Collect all dates, build per-year records
        all_dates = sorted(set(d for m in by_type.values() for d in m))
        history = []
        for dt in all_dates:
            year = dt[:4]
            # Yahoo reports capex as negative (cash outflow); the F-score and
            # capex/revenue ratios want its magnitude.
            capex = by_type.get("capitalExpenditure", {}).get(dt)
            capex = abs(capex) if capex is not None else None
            fcf = by_type.get("freeCashFlow", {}).get(dt)
            ocf = by_type.get("operatingCashFlow", {}).get(dt)
            if ocf is None and fcf is not None and capex is not None:
                ocf = fcf + capex  # ASML-style gap: OCF absent, FCF and capex present
            history.append({
                "year": year,
                "totalRevenue": by_type.get("totalRevenue", {}).get(dt),
                "grossProfit": by_type.get("grossProfit", {}).get(dt),
                "operatingIncome": by_type.get("operatingIncome", {}).get(dt),
                "netIncome": by_type.get("netIncome", {}).get(dt),
                "eps": by_type.get("dilutedEPS", {}).get(dt),
                "stockholderEquity": by_type.get("stockholdersEquity", {}).get(dt),
                "capitalExpenditure": capex,
                "operatingCashFlow": ocf,
                "freeCashFlow": fcf,
                "totalAssets": by_type.get("totalAssets", {}).get(dt),
                "totalDebt": by_type.get("totalDebt", {}).get(dt),
                "dilutedAverageShares": by_type.get("dilutedAverageShares", {}).get(dt),
            })
        return history if history else None
    except Exception as e:
        print(f"Warning: failed to fetch financial history for {ticker}: {e}")
        return None


def _score_tier(val, tiers):
    """Score a value against [(threshold, score), ...] tiers. Returns score for first matching tier."""
    if val is None:
        return 50
    if val < 0:
        # A negative PEG or P/B means collapsing earnings or negative book equity,
        # not a bargain. Without this it satisfies the first (best) tier.
        return 25
    for threshold, score in tiers:
        if val <= threshold:
            return score
    return tiers[-1][1] if tiers else 50


def _f_score_signals(financial_history):
    """The 8 Piotroski F-score signals as [(label, True | False | None), ...].

    None means an input was missing, so the signal *abstains*: callers must drop
    it from the denominator rather than score it as a failure. The old checklist
    scored a non-Piotroski "positive FCF" and a levels-only D/E test, which
    together punished every self-funded capex buildout for free; canonical
    Piotroski uses operating cash flow and never looks at capex.
    """
    rows = financial_history or []
    cur = rows[-1] if rows else {}
    prev = rows[-2] if len(rows) >= 2 else {}

    def ratio(row, num_key, den_key):
        num, den = row.get(num_key), row.get(den_key)
        return None if num is None or not den else num / den

    def gt(a, b):
        return None if a is None or b is None else a > b

    roa, roa_prev = ratio(cur, "netIncome", "totalAssets"), ratio(prev, "netIncome", "totalAssets")
    lev, lev_prev = ratio(cur, "totalDebt", "totalAssets"), ratio(prev, "totalDebt", "totalAssets")
    gm, gm_prev = ratio(cur, "grossProfit", "totalRevenue"), ratio(prev, "grossProfit", "totalRevenue")
    turn, turn_prev = ratio(cur, "totalRevenue", "totalAssets"), ratio(prev, "totalRevenue", "totalAssets")
    cfo, net_income = cur.get("operatingCashFlow"), cur.get("netIncome")
    shares, shares_prev = cur.get("dilutedAverageShares"), prev.get("dilutedAverageShares")

    return [
        ("ROA+",            None if roa is None else roa > 0),
        ("OCF+",            None if cfo is None else cfo > 0),
        ("ROA rising",      gt(roa, roa_prev)),
        ("Accrual quality", gt(cfo, net_income)),
        ("Deleveraging",    gt(lev_prev, lev)),
        # 1% tolerance: routine option vesting is not a capital raise.
        ("No dilution",     None if shares is None or shares_prev is None else shares <= shares_prev * 1.01),
        ("GM rising",       gt(gm, gm_prev)),
        ("Asset turns up",  gt(turn, turn_prev)),
    ]


def compute_fundamental_score(fund, price, target_mean, num_analysts, sector=None,
                              financial_history=None):
    """Compute 0-100 fundamental score from valuation, profitability, growth and health.

    Returns (score, reasons, quality, quality_details) — quality is the raw
    Piotroski F-score pass count (quality_details its labels), or (None, [])
    when no signal was computable at all. Returns (None, [], None, []) when
    there are no fundamentals at all — a ticker with no data must abstain, not
    read as a neutral "Hold". The analyst price target is reported in `reasons`
    but is deliberately not part of the score: it is sentiment, not a fundamental.
    """
    if not fund:
        return None, [], None, []
    reasons = []

    # 1. Valuation (0.20) — sector-relative P/E and P/S
    sector_key = sector or fund.get("sector")
    medians = SECTOR_MEDIANS.get(sector_key, DEFAULT_MEDIANS)

    pe_val = fund.get("trailingPE")
    if pe_val is not None and pe_val < 0:
        pe = 25  # Negative earnings
    else:
        pe = _sector_relative_score(pe_val, medians["pe"])
        if pe is None:
            pe = 50

    ps_val = fund.get("priceToSales")
    ps = _sector_relative_score(ps_val, medians["ps"])
    if ps is None:
        ps = 50

    # Both tier lists end at a real floor and start at a real ceiling, so a P/B
    # of 500 no longer scores the same as a P/B of 9.5.
    peg_val = fund.get("pegRatio")
    peg = _score_tier(peg_val, [(1, 100), (1.5, 75), (2, 50), (3, 25), (5, 10), (math.inf, 0)])
    earnings_growth = fund.get("earningsGrowth")
    if peg >= 75 and (earnings_growth is None or earnings_growth <= 0.02
                      or not 0.05 <= peg_val <= 20):
        # A low PEG on flat or shrinking earnings is a value trap, not a bargain
        # (NICE: PEG 0.62 against earningsGrowth -0.617).
        peg = 50
    pb = _score_tier(fund.get("priceToBook"), [(1, 100), (3, 75), (5, 50), (10, 25), (20, 10), (math.inf, 0)])
    val_score = pe * 0.35 + ps * 0.20 + peg * 0.25 + pb * 0.20
    if val_score >= 70:
        reasons.append(f"Attractive valuation vs {sector_key or 'market'} (P/E={pe_val}, median={medians['pe']}) (+Fund)")
    elif val_score <= 30:
        reasons.append(f"Expensive valuation vs {sector_key or 'market'} (P/E={pe_val}, median={medians['pe']}) (-Fund)")

    # 2. Profitability (0.25)
    gm = 50
    if fund.get("grossMargins") is not None:
        gm = 100 if fund["grossMargins"] > 0.5 else 75 if fund["grossMargins"] > 0.3 else 50 if fund["grossMargins"] > 0.15 else 25
    om = 50
    if fund.get("operatingMargins") is not None:
        om = 100 if fund["operatingMargins"] > 0.2 else 75 if fund["operatingMargins"] > 0.1 else 50 if fund["operatingMargins"] > 0 else 25
    roe = 50
    if fund.get("returnOnEquity") is not None:
        roe = 100 if fund["returnOnEquity"] > 0.2 else 75 if fund["returnOnEquity"] > 0.1 else 50 if fund["returnOnEquity"] > 0 else 25
    prof_score = (gm + om + roe) / 3
    if prof_score >= 70:
        reasons.append(f"Strong profitability (GM={fund.get('grossMargins') or 0:.0%}, ROE={fund.get('returnOnEquity') or 0:.0%}) (+Fund)")

    # 3. Growth (0.20)
    def growth_score(val):
        if val is None: return 50
        if val > 0.25: return 100
        if val > 0.1: return 75
        if val > 0: return 50
        return 25
    rg = growth_score(fund.get("revenueGrowth"))
    eg = growth_score(fund.get("earningsGrowth"))
    cyg = growth_score(fund.get("currentYearGrowth"))
    grow_score = (rg + eg + cyg) / 3
    if grow_score >= 70:
        reasons.append(f"Strong growth (Rev={fund.get('revenueGrowth') or 0:.0%}, EPS={fund.get('earningsGrowth') or 0:.0%}) (+Fund)")
    elif grow_score <= 30:
        reasons.append(f"Weak growth (-Fund)")

    # 4. Financial Health (0.35) — Piotroski F-score, 0-8. Signals with missing
    # inputs abstain, so the denominator is what was actually measurable. With no
    # measurable signal at all health is None, and its weight is renormalised
    # away below rather than scored as a neutral 50.
    signals = _f_score_signals(financial_history)
    known = [ok for _, ok in signals if ok is not None]
    health_score, quality, quality_details = None, None, []
    if known:
        quality = sum(known)
        quality_details = [label for label, ok in signals if ok]
        health_score = round(quality / len(known) * 100)
        # Threshold on the coverage-adjusted percentage, not the raw pass count:
        # the denominator varies from 1 to 8, so "1 passed" is a perfect score
        # when 1 signal was measurable and a terrible one when 8 were.
        if health_score >= 75:
            reasons.append(f"Strong quality ({quality}/{len(known)}: {', '.join(quality_details[:3])}) (+Fund)")
        elif health_score <= 25:
            reasons.append(f"Weak quality ({quality}/{len(known)}) (-Fund)")

    # The analyst price target is display-only. It is sentiment, and at 20% of
    # the weight it was the single largest term in a score labelled fundamental.
    if target_mean and price and num_analysts:
        upside = ((target_mean - price) / price) * 100
        reasons.append(f"Analyst target ${target_mean:.0f} ({upside:+.0f}%)")

    legs = [(0.20, val_score), (0.25, prof_score), (0.20, grow_score), (0.35, health_score)]
    coverage = sum(w for w, v in legs if v is not None)
    fund_total = sum(w * v for w, v in legs if v is not None) / coverage
    return round(fund_total, 1), reasons, quality, quality_details


def veto_gates(fund, financial_history, ticker, ocf_veto_exempt):
    """Hard score ceilings. Returns (reason, cap) for the tightest gate that
    fires, or (None, None).

    A gate whose input is missing abstains — it is skipped, never treated as
    passed. `ocf_veto_exempt` lists tickers whose negative operating cash flow
    is structural rather than distress (lenders originating loans).
    """
    fund = fund or {}
    ocf = fund.get("operatingCashflow")
    rev = fund.get("totalRevenue")
    total_debt = fund.get("totalDebt")
    total_cash = fund.get("totalCash")

    # Gate 1: cash burn -> Sell ceiling.
    if ocf is not None and ocf < 0:
        material = not rev or (ocf / rev) < -0.05
        if material and ticker not in (ocf_veto_exempt or ()):
            return ("cash_burn", 39)

    # Gate 2: leverage -> Hold ceiling. This is the capex-aware test: netDebt/ocf
    # separates a self-funded buildout (NBIS 0.076) from real leverage (ORCL
    # 4.24) without ever reading a capex figure.
    if ocf is not None and ocf > 0 and total_debt is not None and total_cash is not None:
        net_debt = total_debt - total_cash
        if net_debt > 0 and (net_debt / ocf) > 4.0:
            return ("leverage", 59)

    # Gate 3: margin erosion -> Hold ceiling. grossProfit and operatingIncome are
    # null for 7 of 53 tickers, so a hole in the series skips the series.
    rows = financial_history or []
    for series_key in ("grossProfit", "operatingIncome"):
        s = [row.get(series_key) for row in rows]
        r = [row.get("totalRevenue") for row in rows]
        if len(s) < 3 or any(x is None for x in s) or any(not x for x in r):
            continue
        margins = [a / b for a, b in zip(s, r)]
        if margins[-1] - margins[0] <= -0.05 and margins[-1] < margins[-2]:
            return ("margin_erosion", 59)

    return (None, None)


def merge_fundamentals(technicals, fund_data, financial_history=None, ticker=None,
                       ocf_veto_exempt=None):
    """Merge fundamentals and price targets into technicals, compute combined score."""
    if not technicals:
        return technicals

    pt = fund_data.get("price_target", {}) if fund_data else {}
    fund = fund_data.get("fundamentals", {}) if fund_data else {}
    earn = fund_data.get("earnings", {}) if fund_data else {}

    # Earnings date from Yahoo Finance calendarEvents
    technicals["next_earnings"] = earn if earn and earn.get("date") else None

    # Price target fields
    technicals["target_high"] = pt.get("target_high")
    technicals["target_low"] = pt.get("target_low")
    technicals["target_mean"] = pt.get("target_mean")
    technicals["target_median"] = pt.get("target_median")
    technicals["num_analysts"] = pt.get("num_analysts")

    # Fundamentals dict
    technicals["fundamentals"] = fund if fund else None
    technicals["financialHistory"] = financial_history

    # Technical score stays pure (already computed in fetch_technicals)
    tech_score = technicals.get("score", 50)
    technicals["tech_score"] = tech_score

    # Compute fundamental score
    fund_score, fund_reasons, quality, quality_details = compute_fundamental_score(
        fund, technicals.get("price"), pt.get("target_mean"), pt.get("num_analysts"),
        sector=fund.get("sector"), financial_history=financial_history,
    )
    technicals["quality_score"] = quality
    technicals["quality_details"] = quality_details

    # Veto gates cap both the fundamental score and the combined score. Capping
    # only fund_score is 40% defanged — at tech >= 55 a Sell-ceilinged name still
    # clears 39 on the combine.
    veto_reason, veto_cap = veto_gates(fund, financial_history, ticker, ocf_veto_exempt)
    technicals["veto_reason"] = veto_reason

    # Combined score: tech 40%, fundamental 60%
    if fund_score is None:
        combined, recommendation = None, "No Data"
    else:
        combined = round(tech_score * 0.40 + fund_score * 0.60, 1)
        if veto_cap is not None:
            combined = min(combined, veto_cap)
            fund_score = min(fund_score, veto_cap)
            fund_reasons = fund_reasons + [f"Capped at {veto_cap} ({veto_reason}) (-Fund)"]
        recommendation = score_to_recommendation(combined)

    technicals["fund_score"] = fund_score
    technicals["fund_score_reasons"] = fund_reasons
    technicals["combined_score"] = combined
    technicals["recommendation"] = recommendation

    # Keep pure technical recommendation
    technicals["tech_recommendation"] = score_to_recommendation(tech_score)

    # Merge reasons
    technicals["score_reasons"] = technicals.get("score_reasons", []) + fund_reasons

    return technicals


def compute_weights(portfolio, technicals):
    """Live allocation % from share counts and current prices - no new API calls,
    price is already fetched into technicals[ticker]['price']."""
    vals = {t: h["shares"] * ((technicals.get(t) or {}).get("price") or 0)
            for t, h in portfolio.items()}
    total = sum(vals.values())
    return {t: round(v / total * 100, 2) for t, v in vals.items()} if total else {}


def compute_exceptions(prev_data, technicals, weights, themes, expo, today):
    """Monitor mode: what CHANGED since the last run. Returns (alerts, monitor).

    **Invariant: every trigger is an edge against prior state, never a level.**
    A current-state predicate ("below SMA200", "earnings within 7 days") re-fires
    every run until it clears — that is the death-cross bug, and it is what made
    the old briefing 6,533 chars of restated snapshot. If you add a trigger here
    it compares `old` to `cur`, or it keys a fired-set on event identity.

    `alerts` is [{ticker, trigger, detail}]. `monitor` is the state that has no
    home in the per-ticker records: the 52w suppression clock, the earnings
    fired-set, the per-theme score EMA and the per-theme exposure side. It is
    stored at the top level of market-data.json next to `alerts_pending`.
    """
    prev = prev_data.get("tickers") or {}
    was = prev_data.get("monitor") or {}
    # Copied, not aliased: this returns the next state, it does not edit the last one.
    hi_lo_fired = dict(was.get("hi_lo_fired") or {})
    earnings_fired = dict(was.get("earnings_fired") or {})
    theme_ema = dict(was.get("theme_ema") or {})
    theme_expo_hi = dict(was.get("theme_expo_hi") or {})

    alerts = []

    def fire(name, trigger, detail):
        alerts.append({"ticker": name, "trigger": trigger, "detail": detail})

    for ticker, cur in technicals.items():
        cur = cur or {}
        old = (prev.get(ticker) or {}).get("technicals") or {}
        # A ticker the previous run never scored has no prior state to diff
        # against: baseline it silently. Without this the watchlist edits alone
        # (35 -> 42 -> 46 -> 53 across the measured window) emit 18 false alerts.
        if not old:
            continue

        c0, c1 = old.get("combined_score"), cur.get("combined_score")

        # 7. DATA_GAP — scored last run, not this run. No weight gate, and it is
        #    the one trigger that also covers the watchlist.
        if c0 is not None and c1 is None:
            fire(ticker, "DATA_GAP", f"scored {c0} last run, no score this run")

        if weights.get(ticker, 0) < MIN_WEIGHT:
            continue

        # 1. Recommendation bucket flip. The band is on the boundary, not on the
        #    move: the score has to leave last run's bucket by 3 points, so a name
        #    sitting on 60 does not flip Buy/Hold on every 0.4-point wobble. Raw
        #    flips run 570 over the stored history; this filter leaves 73.
        #    ponytail: stateless, so a drift that crosses a boundary in sub-3-point
        #    steps is never reported (measured: 2 such moves in 181 runs). Upgrade
        #    is to hold the last *reported* bucket in `monitor` and band against
        #    that instead — 34 fires vs 32, if those 2 turn out to matter.
        if c0 is not None and c1 is not None:
            lo, hi = _bucket_edges(c0)
            if c1 >= hi + REC_HYSTERESIS or c1 < lo - REC_HYSTERESIS:
                fire(ticker, "REC_FLIP", f"{score_to_recommendation(c0)} -> "
                                         f"{score_to_recommendation(c1)} (combined {c0} -> {c1})")

        # 2. SMA50/SMA200 sign flip.
        s50_0, s200_0 = old.get("sma50"), old.get("sma200")
        s50_1, s200_1 = cur.get("sma50"), cur.get("sma200")
        if None not in (s50_0, s200_0, s50_1, s200_1) and (s50_0 > s200_0) != (s50_1 > s200_1):
            fire(ticker, "MA_CROSS",
                 f"{'golden' if s50_1 > s200_1 else 'death'} cross (SMA50 {s50_1}, SMA200 {s200_1})")

        # 3. New 52-week extreme, against the *prior* run's range, then suppressed
        #    for 21 days so a trending name does not report the same run daily.
        price, hi0, lo0 = cur.get("price"), old.get("high_52w"), old.get("low_52w")
        side = None
        if price and hi0 and lo0:
            side = "HIGH" if price > hi0 else "LOW" if price < lo0 else None
        if side:
            last = hi_lo_fired.get(ticker)
            days = None if not last else (today - date.fromisoformat(last)).days
            if days is None or days >= HI_LO_SUPPRESS:
                fire(ticker, f"52W_{side}", f"${price:.2f} through prior 52w {side.lower()} ${hi0 if side == 'HIGH' else lo0}")
                hi_lo_fired[ticker] = today.isoformat()

        # 4. A veto gate newly fires or newly clears. Both runs must have been
        #    scored — "no gate" and "gate not evaluated" are not the same state.
        v0, v1 = old.get("veto_reason"), cur.get("veto_reason")
        if c0 is not None and c1 is not None and v0 != v1:
            fire(ticker, "VETO", f"{v0 or 'none'} -> {v1 or 'none'}")

        # 5. Earnings entering the 0-3 day window, keyed on (ticker, date) so a
        #    confirmed date fires once and a rescheduled one fires again.
        ne = (cur.get("next_earnings") or {}).get("date")
        edate = None
        if ne:
            try:
                edate = date.fromisoformat(ne)
            except (ValueError, TypeError):
                edate = None
        if edate and 0 <= (edate - today).days <= 3 and earnings_fired.get(ticker) != ne:
            fire(ticker, "EARNINGS", f"reports {ne} ({(edate - today).days}d)")
            earnings_fired[ticker] = ne

        # 6. Distribution newly true.
        r0, d0 = old.get("rvol5"), old.get("dir5")
        r1, d1 = cur.get("rvol5"), cur.get("dir5")
        if None not in (r0, d0, r1, d1):
            was_distrib = r0 >= DISTRIB_RVOL and d0 <= DISTRIB_DIR
            now_distrib = r1 >= DISTRIB_RVOL and d1 <= DISTRIB_DIR
            if now_distrib and not was_distrib:
                fire(ticker, "DISTRIBUTION", f"volume {r1:.1f}x, {abs(d1):.1f} average-days net selling")

    # 8. A theme's mean score drops 3 points below its own EMA. Theme-level, so
    #    the per-ticker weight gate does not apply; coverage is the gate instead,
    #    since a half-fetched theme's mean is not comparable to the EMA's.
    for name, members in themes.items():
        scored = [(technicals.get(t) or {}).get("combined_score") for t in members]
        scored = [s for s in scored if s is not None]
        if not members or len(scored) < THEME_COVERAGE * len(members):
            continue
        score = sum(scored) / len(scored)
        base = theme_ema.get(name)
        if base is not None and score <= base - THEME_DROP:
            fire(name, "THEME_DROP", f"mean score {score:.1f} vs EMA {base:.1f} ({len(scored)}/{len(members)} scored)")
            # Re-baseline on the way out. At alpha=0.1 a 13-point drop otherwise
            # keeps firing for ~24 runs while the EMA crawls back to within 3 —
            # a level, not an edge. Snapping reports each further 3-point leg
            # down exactly once and never re-reports the one just sent.
            base = score
        theme_ema[name] = round(score if base is None
                                else THEME_EMA_ALPHA * score + (1 - THEME_EMA_ALPHA) * base, 2)

    # 9. Theme exposure crossing 25%, edge with a 2-point band on the way back
    #    down. Only meaningful now that weights are live (Task 4) — static
    #    percentages took 2 distinct values across 162 runs.
    for name in themes:
        pct = expo.get(name, 0)
        was_hi = theme_expo_hi.get(name)
        if was_hi is None:                       # first sight: baseline silently
            theme_expo_hi[name] = pct >= EXPO_LIMIT
        elif not was_hi and pct >= EXPO_LIMIT:
            fire(name, "THEME_EXPO", f"exposure {pct:.1f}%, over {EXPO_LIMIT:.0f}%")
            theme_expo_hi[name] = True
        elif was_hi and pct <= EXPO_LIMIT - EXPO_HYSTERESIS:
            fire(name, "THEME_EXPO", f"exposure {pct:.1f}%, back under {EXPO_LIMIT:.0f}%")
            theme_expo_hi[name] = False

    # Anything the previous run computed but could not deliver is still news.
    seen = {(a["ticker"], a["trigger"]) for a in alerts}
    for a in prev_data.get("alerts_pending") or []:
        if (a.get("ticker"), a.get("trigger")) not in seen:
            alerts.append(a)

    return alerts, {"hi_lo_fired": hi_lo_fired, "earnings_fired": earnings_fired,
                    "theme_ema": theme_ema, "theme_expo_hi": theme_expo_hi}


def quiet_line(portfolio, expo, earnings):
    """The whole briefing on a day when nothing changed."""
    themes = [(n, v) for n, v in expo.items() if n != "untagged"]
    top = max(themes, key=lambda x: x[1]) if themes else None
    theme_str = f" {top[0]} {top[1]:.1f}%." if top else ""
    if earnings:
        e = earnings[0]
        try:
            d = date.fromisoformat(e["date"])
            when = f"{d.month}/{d.day}"
        except (ValueError, TypeError):
            when = e["date"]
        earn_str = f" Next earnings: {e['symbol']} {when}."
    else:
        earn_str = " No earnings in the next 14 days."
    return f"Nothing changed. {len(portfolio)} positions,{theme_str}{earn_str}"


def build_prompt(portfolio_news, watchlist_news, market_news, indicators, earnings, portfolio, watchlist, technicals, briefing_type, weights, TAG, expo, alerts=(), full=False):
    today_str = date.today().strftime("%B %d, %Y")

    if briefing_type == "pre-market":
        header = f"PRE-MARKET BRIEFING \u2014 {today_str}"
        time_context = "for today's trading session"
    else:
        header = f"PRE-CLOSE BRIEFING \u2014 {today_str}"
        time_context = "as we approach market close"

    # --- MARKET CONTEXT ---
    market_text = "## Market Indicators\n"
    fg = indicators.get("fear_greed")
    if fg:
        market_text += f"- Fear & Greed: {fg['now']} (prev: {fg['previous_close']}, 1w ago: {fg['one_week_ago']}, 1m ago: {fg['one_month_ago']})\n"
    indices = indicators.get("indices", {})
    for name, key in [("S&P 500", "sp500"), ("Dow Jones", "dow"), ("NASDAQ", "nasdaq")]:
        idx = indices.get(key)
        if idx:
            market_text += f"- {name}: ${idx['price']:,.2f} ({idx['changesPercentage']:+.2f}%)\n"
    vix = indicators.get("vix")
    if vix:
        market_text += f"- VIX: {vix['price']} ({vix['pct']:+.2f}%)\n"

    # --- EARNINGS ---
    earn_text = ""
    if earnings:
        earn_text = "\n## Upcoming Earnings\n"
        for e in earnings:
            eps = f"EPS est: ${e['eps_est']:.2f}" if e["eps_est"] is not None else "EPS est: N/A"
            held = "PORTFOLIO" if e["symbol"] in portfolio else "WATCHLIST"
            alloc = f" ({weights.get(e['symbol'], 0)}%)" if e["symbol"] in portfolio else ""
            earn_text += f"- [{held}] {e['symbol']}{alloc}: {e['date']} ({e['hour']}) \u2014 {eps}\n"

    # --- ECONOMIC CALENDAR ---
    cal_text = ""
    cal = indicators.get("calendar", [])
    if cal:
        cal_text = "\n## Economic Calendar\n"
        for e in cal[:5]:
            cal_text += f"- {e['date']}: {e['title']} - {e.get('description', '')}\n"

    # --- PRE-COMPUTE ALGORITHM DECISIONS ---
    buys, holds, sells = [], [], []
    for ticker, tech in technicals.items():
        if not tech or ticker not in portfolio:
            continue
        alloc = weights.get(ticker, 0)
        rec = tech.get("recommendation", "Hold")
        combined = tech.get("combined_score", tech.get("score", 50))
        tech_sc = tech.get("tech_score", tech.get("score", 50))
        fund_sc = tech.get("fund_score", 50)
        rsi = tech.get("rsi")
        signals = tech.get("signals", [])
        pt_mean = tech.get("target_mean")
        upside = round((pt_mean - tech["price"]) / tech["price"] * 100, 1) if pt_mean and tech.get("price") else None
        upside_str = f"target ${pt_mean:.0f} ({upside:+.1f}%)" if upside is not None else "no target"
        has_earnings = any(e["symbol"] == ticker for e in earnings)
        fund = tech.get("fundamentals") or {}
        pe_str = f"P/E={fund['trailingPE']:.1f}" if fund.get("trailingPE") else "P/E=N/A"
        fpe_str = f"FwdP/E={fund['forwardPE']:.1f}" if fund.get("forwardPE") else ""

        entry = f"{ticker} ({alloc}%): combined={combined} (tech={tech_sc}, fund={fund_sc}), ${tech['price']:.2f}, RSI={rsi}, {pe_str}, {fpe_str}, {upside_str}, signals=[{', '.join(signals[:4])}]"
        if has_earnings:
            entry += " *** EARNINGS SOON ***"

        if rec in ("Strong Buy", "Buy"):
            buys.append((combined, entry))
        elif rec in ("Strong Sell", "Sell"):
            sells.append((combined, entry))
        else:
            holds.append((combined, entry))

    # Watchlist picks
    wl_picks = []
    for ticker, tech in technicals.items():
        if not tech or ticker in portfolio:
            continue
        combined = tech.get("combined_score", tech.get("score", 50))
        rec = tech.get("recommendation", "Hold")
        rsi = tech.get("rsi")
        pt_mean = tech.get("target_mean")
        upside = round((pt_mean - tech["price"]) / tech["price"] * 100, 1) if pt_mean and tech.get("price") else None
        upside_str = f"target ${pt_mean:.0f} ({upside:+.1f}%)" if upside is not None else "no target"
        if rec in ("Strong Buy", "Buy") or (rsi and rsi < 35):
            theme_note = ""
            theme = TAG.get(ticker)
            if theme and rec in ("Strong Buy", "Buy") and expo.get(theme, 0) >= 25:
                theme_note = f" (adds to {theme}, already {expo[theme]:.1f}%)"
            wl_picks.append((combined, f"{ticker}: combined={combined}, ${tech['price']:.2f}, RSI={rsi}, {upside_str}, signals=[{', '.join(tech.get('signals', [])[:3])}]{theme_note}"))

    # combined is None for a ticker that abstained (no fundamentals), so sort it
    # as 0 rather than letting one failed fetch crash the whole briefing.
    buys.sort(key=lambda x: -(x[0] or 0))
    sells.sort(key=lambda x: x[0] or 0)
    holds.sort(key=lambda x: -(x[0] or 0))
    wl_picks.sort(key=lambda x: -(x[0] or 0))

    # --- STANDING FACTS (both modes) ---
    theme_str = ", ".join(f"{n} {v:.1f}%" for n, v in sorted(expo.items(), key=lambda x: -x[1]))
    standing = "\n## Standing facts (context only — do not report these as news)\n"
    standing += f"- {len(portfolio)} positions. Theme exposure: {theme_str}\n"
    if earnings:
        standing += "- Next earnings: " + ", ".join(f"{e['symbol']} {e['date']}" for e in earnings[:3]) + "\n"
    else:
        standing += "- Next earnings: none in the next 14 days\n"
    for _, entry in wl_picks[:3]:
        standing += f"- Watchlist entry signal: {entry}\n"

    # --- EXCEPTIONS (both modes) ---
    # The full Sunday briefing carries these too: alerts_pending is cleared after
    # any successful send, so an exception left out of the message it was cleared
    # by is lost for good — and trigger 5 is keyed on (ticker, date), so a dropped
    # earnings alert can never fire again for that date.
    exc_text = "\n## EXCEPTIONS — what changed since the last run\n"
    for a in alerts:
        exc_text += f"- {a['ticker']} [{a['trigger']}]: {a['detail']}\n"
    if not alerts:
        exc_text += "- None\n"

    # --- MONITOR MODE: exceptions only ---
    if not full:
        exc_news = ""
        for ticker in dict.fromkeys(a["ticker"] for a in alerts):   # alert order, deduped
            articles = portfolio_news.get(ticker) or watchlist_news.get(ticker) or []
            if articles:
                exc_news += f"\n### {ticker} news\n"
                for a in articles[:2]:
                    exc_news += f"- {a['headline']}: {a['summary']}\n"
        if exc_news:
            exc_news = "\n## News for the tickers above\n" + exc_news

        return f"""You are a market monitor for a long-term investor. Report ONLY what changed.

FORMAT RULES (strict):
- Under 1200 characters total. Shorter is better.
- ONLY use <b> and <i> HTML tags. No other tags.
- One • bullet per exception, one line each, in the order given below.

Write exactly this:

<b>\U0001f4e1 MONITOR — {today_str}</b>

One bullet per exception: what changed, then one short clause of why, but only if
the news or market context below actually explains it. Say nothing about any ticker
that is not in the exception list. Do not restate the portfolio, do not list
indicators, do not add sections, do not give advice on unchanged positions.

Then one final line beginning "Standing:" with the position count, the largest theme
exposure, and the next earnings date.

DATA:
{exc_text}{standing}
{market_text}{exc_news}"""

    algo_text = "\n## ALGORITHM DECISIONS (pre-computed)\n"
    algo_text += "\n### BUY/ADD (top by score):\n"
    for _, entry in buys[:3]:
        algo_text += f"- {entry}\n"
    if not buys:
        algo_text += "- None\n"
    algo_text += "\n### HOLD:\n"
    for _, entry in holds[:5]:
        algo_text += f"- {entry}\n"
    algo_text += "\n### SELL/REDUCE (weakest by score):\n"
    for _, entry in sells[:3]:
        algo_text += f"- {entry}\n"
    if not sells:
        algo_text += "- None\n"
    algo_text += "\n### WATCHLIST ENTRY SIGNALS:\n"
    for _, entry in wl_picks[:3]:
        algo_text += f"- {entry}\n"
    if not wl_picks:
        algo_text += "- None\n"

    # --- FULL TECHNICAL DATA ---
    tech_text = "\n## Technical Indicators\n"
    for ticker, tech in technicals.items():
        if tech:
            rsi_str = f"RSI={tech['rsi']}" if tech.get("rsi") else "RSI=N/A"
            sma50_str = f"SMA50=${tech['sma50']}" if tech.get("sma50") else "SMA50=N/A"
            sma200_str = f"SMA200=${tech['sma200']}" if tech.get("sma200") else "SMA200=N/A"
            signals = ", ".join(tech.get("signals", [])) if tech.get("signals") else "no signals"
            alloc = f" ({weights.get(ticker, 0)}%)" if ticker in portfolio else " [WL]"
            pt_mean = tech.get("target_mean")
            pt_str = f" | target=${pt_mean:.0f}" if pt_mean else ""
            tech_text += f"- {ticker}{alloc}: ${tech['price']:.2f} ({tech.get('change_pct', 0):+.2f}%) | {rsi_str} | {sma50_str} | {sma200_str} | score={tech.get('score', 'N/A')}{pt_str} | {signals}\n"

    # --- CONCENTRATION RISKS ---
    risk_text = "\n## Concentration Risks\n"
    for ticker, alloc in sorted(weights.items(), key=lambda x: -x[1]):
        if alloc > 10:
            risk_text += f"- {ticker}: {alloc}% of portfolio\n"

    # --- NEWS (with full summaries) ---
    news_text = "\n## General Market News\n"
    for article in market_news[:5]:
        news_text += f"- {article['headline']}: {article['summary']}\n"

    news_text += "\n## Portfolio Holdings News\n"
    for ticker, articles in portfolio_news.items():
        if articles:
            alloc = weights.get(ticker, 0)
            news_text += f"\n### {ticker} ({alloc}% of portfolio)\n"
            for a in articles:
                news_text += f"- {a['headline']}: {a['summary']}\n"

    if any(articles for articles in watchlist_news.values()):
        news_text += "\n## Watchlist News\n"
        for ticker, articles in watchlist_news.items():
            if articles:
                news_text += f"\n### {ticker}\n"
                for a in articles:
                    news_text += f"- {a['headline']}: {a['summary']}\n"

    return f"""You are a stock market analyst advising a long-term investor.
Produce a Telegram message briefing {time_context} using ALL the data below.

YOUR ROLE: Our scoring algorithm (0-100, Tech 40% + Fundamental 60%) has pre-computed decisions: Strong Buy (72+), Buy (60-71), Hold (40-59), Sell (28-39), Strong Sell (<28). Fundamentals include sector-relative valuation and a 0-8 Piotroski F-score, and hard veto gates that cap a score at 39 (cash burn) or 59 (leverage, margin erosion). Use these as a starting point, but make your OWN analysis by combining algorithm scores + news + technicals + upcoming events. If you disagree with the algorithm, say so and explain why.

FORMAT RULES (strict):
- Keep under 3900 characters total.
- ONLY use <b> and <i> HTML tags. No <u>, no <s>, no other tags.
- Use emojis liberally to make it scannable and visually appealing.
- Use \u2022 bullets for lists, keep each bullet to one line.

Structure your message EXACTLY in this order:

<b>\U0001f4ca {header}</b>

<b>\U0001f514 WHAT CHANGED</b>
One • bullet per entry in the EXCEPTIONS list below, all of them, in the order given. This is the only place these are reported, so do not drop any. Write "• Nothing changed since the last run." if the list says None.

<b>\U0001f3af MARKET DASHBOARD</b>
Show indices with price and arrows (\u2b06\ufe0f/\u2b07\ufe0f), VIX with arrow.
Fear & Greed score with emoji (\U0001f631<25, \U0001f628<45, \U0001f610<55, \U0001f60e<75, \U0001f929 75+).
Include trend vs previous close and 1 month ago with arrows.

<b>\U0001f4c5 EARNINGS ALERT</b>
List upcoming earnings from data. Use \u26a0\ufe0f for this week, \U0001f4c6 for next week. Show [PORTFOLIO] or [WATCHLIST] and allocation %.

<b>\U0001f4b0 ECONOMIC CALENDAR</b>
Key upcoming economic events with date and descriptive emoji. One line each.

<b>\U0001f4bc PORTFOLIO PULSE</b>
For each portfolio ticker with meaningful news (sorted by allocation):
Colored dot (\U0001f7e2 positive / \U0001f534 negative / \u26aa neutral price action), then <b>TICKER (alloc%)</b>: one-line news summary. End with impact (\U0001f7e2 Bullish / \U0001f534 Bearish / \u26aa Neutral Impact).

<b>\U0001f440 WATCHLIST RADAR</b>
Max 3 watchlist tickers with notable news or entry signals. For each: \U0001f7e1 <b>TICKER</b>: 2-3 sentence analysis including technicals, news catalyst, and actionable guidance.

<b>\U0001f4a1 RECOMMENDATIONS</b>
Based on ALL data (algorithm scores + news + technicals + sentiment), give your recommendations:
\U0001f7e2 <b>BUY/ADD</b>: Which tickers to buy/add and WHY. Include price target upside if available. Add \u2705 checkmark.
\U0001f7e1 <b>HOLD</b>: Which to hold and WHY. Group similar tickers together.
\U0001f534 <b>SELL/REDUCE</b>: Which to sell/trim and WHY. Add \u274c marker.
Include watchlist tickers in BUY if there's a good entry signal.
\u26a0\ufe0f Add an inline warning for concentration risk (any position >10%).
Skip empty categories.

<b>\u26a0\ufe0f RISK ALERTS</b>
Flag single-position concentration (>10%) and any theme exposure at or above 25%.

<b>\U0001f30d MARKET OUTLOOK</b>
2-3 sentences on overall sentiment and what to watch {time_context}.

Do NOT suggest stocks outside the portfolio/watchlist.

DATA:
{exc_text}{standing}{market_text}{earn_text}{cal_text}{algo_text}{tech_text}{risk_text}{news_text}"""


def analyze(prompt):
    api_key = os.environ["GEMINI_API_KEY"]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={api_key}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    for attempt in range(3):
        resp = requests.post(url, json=payload, timeout=60)
        if resp.status_code == 503 and attempt < 2:
            time.sleep(5)
            continue
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"]


def sanitize_telegram_html(text):
    """Make text safe for Telegram HTML parse mode.

    Strategy: protect allowed tags, escape everything else, restore tags.
    This handles bare < > in text (e.g. 'RSI > 70') that break Telegram's parser.
    """
    import re
    allowed = {'b', 'i', 'u', 's', 'code', 'pre', 'a'}
    placeholders = {}
    counter = [0]

    def protect_tag(m):
        tag_name = m.group(1).lower().strip().split()[0].lstrip('/')
        if tag_name in allowed:
            key = f"\x00TAG{counter[0]}\x00"
            placeholders[key] = m.group(0)
            counter[0] += 1
            return key
        return m.group(0)  # leave unsupported tags to be escaped below

    # 1. Replace allowed HTML tags with placeholders
    text = re.sub(r'<(/?\s*[a-zA-Z][^>]*)>', protect_tag, text)
    # 2. Escape all remaining HTML-special characters
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    # 3. Restore allowed tags
    for key, tag in placeholders.items():
        text = text.replace(key, tag)
    return text


def send_telegram(text, bot_token, chat_id):
    # Sanitize HTML to prevent parse failures
    text = sanitize_telegram_html(text)

    # Split into chunks at section boundaries to keep HTML tags balanced
    chunks = []
    if len(text) <= 4000:
        chunks = [text]
    else:
        import re
        # Split on section headers (lines starting with <b>), keeping the delimiter
        sections = re.split(r'\n\n(?=<b>)', text)
        chunk = ""
        for section in sections:
            if chunk and len(chunk) + len(section) + 2 > 4000:
                chunks.append(chunk)
                chunk = section
            else:
                chunk = chunk + "\n\n" + section if chunk else section
        if chunk:
            chunks.append(chunk)

    for chunk in chunks:
        for mode in ["HTML", None]:
            args = ["curl", "-s", "-X", "POST",
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    "-d", f"chat_id={chat_id}",
                    "--data-urlencode", f"text={chunk}"]
            if mode:
                args += ["-d", f"parse_mode={mode}"]
            result = subprocess.run(args, capture_output=True, text=True, timeout=15)
            resp = json.loads(result.stdout)
            if resp.get("ok"):
                break
            if mode is None:
                raise RuntimeError(f"Telegram error: {resp}")
            print(f"Warning: HTML parse failed, sending as plain text")


def save_market_data(portfolio, watchlist, technicals, portfolio_news, watchlist_news, indicators, earnings, weights, alerts=(), monitor=None):
    """Save all market data as JSON for the dashboard."""
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)

    # Build per-ticker data
    tickers_data = {}
    for ticker in portfolio:
        alloc = weights.get(ticker, 0)
        tech = technicals.get(ticker)
        news = portfolio_news.get(ticker, [])
        ticker_earnings = [e for e in earnings if e["symbol"] == ticker]
        tickers_data[ticker] = {
            "type": "portfolio",
            "allocation": alloc,
            "technicals": tech,
            "news": news,
            "earnings": ticker_earnings,
        }

    for ticker in watchlist:
        tech = technicals.get(ticker)
        news = watchlist_news.get(ticker, [])
        ticker_earnings = [e for e in earnings if e["symbol"] == ticker]
        tickers_data[ticker] = {
            "type": "watchlist",
            "allocation": None,
            "technicals": tech,
            "news": news,
            "earnings": ticker_earnings,
        }

    market_data = {
        "updated": datetime.now(tz=__import__('datetime').timezone.utc).isoformat(),
        "indicators": indicators,
        "earnings": earnings,
        "tickers": tickers_data,
        # Undelivered alerts and the monitor's own state. This save happens before
        # the Telegram leg, so alerts_pending is written full and only emptied once
        # the send has actually succeeded — see clear_alerts_pending.
        "alerts_pending": list(alerts),
        "monitor": monitor or {},
    }

    with open(DATA_PATH, "w") as f:
        json.dump(market_data, f, separators=(",", ":"))
    print(f"Market data saved ({DATA_PATH}, {len(market_data['alerts_pending'])} alert(s) pending)")


def clear_alerts_pending():
    """Mark this run's alerts delivered. Called only after send_telegram returns —
    if the send raises, the key survives and the next run re-reports them."""
    try:
        with open(DATA_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    data["alerts_pending"] = []
    with open(DATA_PATH, "w") as f:
        json.dump(data, f, separators=(",", ":"))


def save_briefing_history(briefing_type, content):
    """Append briefing to JSONL history file for the dashboard."""
    data_dir = os.path.join(os.path.dirname(__file__), "docs", "data")
    os.makedirs(data_dir, exist_ok=True)
    filepath = os.path.join(data_dir, "briefings.jsonl")
    entry = {
        "date": date.today().isoformat(),
        "type": briefing_type,
        "content": content,
    }
    with open(filepath, "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"Briefing saved to history ({filepath})")


def main():
    briefing_type = sys.argv[1] if len(sys.argv) > 1 else "pre-market"

    # Prior run's output. save_market_data is the only writer, so the on-disk file
    # is exactly the last run's state — read it before anything can overwrite it.
    # An empty dict (first run, corrupt file) baselines every ticker and fires
    # nothing, which needs no special-casing anywhere below.
    prev_data = {}
    try:
        with open(DATA_PATH) as f:
            prev_data = json.load(f)
    except (OSError, ValueError):
        pass

    finnhub_key = os.environ["FINNHUB_API_KEY"]
    bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    config = load_config()
    portfolio = config["portfolio"]
    watchlist = config["watchlist"]
    ocf_veto_exempt = config.get("ocf_veto_exempt", [])
    all_tickers = list(portfolio.keys()) + watchlist

    # Fetch market data. Market news only feeds the briefing prompt, so a Finnhub
    # outage must not abort the run before the dashboard data is fetched.
    try:
        market_news = fetch_market_news(finnhub_key)
    except Exception as e:
        print(f"Warning: failed to fetch market news: {e}")
        market_news = []
    fg_data = fetch_fear_greed_data()
    vix = fetch_vix()

    # Initialize Yahoo Finance auth for price targets
    init_yahoo_auth()

    # Fetch technical indicators and price targets for all tickers
    print("Fetching technical indicators and price targets...")
    technicals = {}
    for ticker in all_tickers:
        technicals[ticker] = fetch_technicals(ticker)
        time.sleep(0.2)
        fund_data = fetch_fundamentals(ticker)
        fh = fetch_financial_history(ticker)
        technicals[ticker] = merge_fundamentals(
            technicals[ticker], fund_data, fh, ticker, ocf_veto_exempt
        )
        time.sleep(0.2)  # Rate limit Yahoo Finance

    # Live allocation % from share counts x current price (replaces static config percentages)
    weights = compute_weights(portfolio, technicals)

    # Hand-tagged theme exposure (spec: "Thematic concentration")
    themes = config.get("themes", {})
    TAG = {t: name for name, tickers in themes.items() for t in tickers}
    expo = {name: sum(weights.get(t, 0) for t in tickers) for name, tickers in themes.items()}
    expo["untagged"] = sum(v for t, v in weights.items() if t not in TAG)

    # Build earnings list from Yahoo Finance calendarEvents (per-ticker)
    today = date.today()
    earnings_horizon = today + timedelta(days=14)
    earnings = []
    for ticker in all_tickers:
        tech = technicals.get(ticker) or {}
        ne = tech.get("next_earnings")
        if ne and ne.get("date"):
            try:
                edate = date.fromisoformat(ne["date"])
                if today <= edate <= earnings_horizon:
                    earnings.append({
                        "symbol": ticker,
                        "date": ne["date"],
                        "hour": "TBD",
                        "eps_est": ne.get("eps_est"),
                    })
            except (ValueError, TypeError):
                pass
    earnings.sort(key=lambda x: x["date"])
    print(f"Earnings found (Yahoo Finance): {len(earnings)} tickers in next 14 days")

    # Fetch news for portfolio holdings
    portfolio_news = {}
    for ticker in portfolio:
        try:
            portfolio_news[ticker] = fetch_company_news(ticker, finnhub_key)
        except Exception as e:
            print(f"Warning: failed to fetch news for {ticker}: {e}")
            portfolio_news[ticker] = []

    # Fetch news for watchlist
    watchlist_news = {}
    for ticker in watchlist:
        try:
            watchlist_news[ticker] = fetch_company_news(ticker, finnhub_key)
        except Exception as e:
            print(f"Warning: failed to fetch news for {ticker}: {e}")
            watchlist_news[ticker] = []

    indicators = {"vix": vix}
    if fg_data:
        indicators["fear_greed"] = fg_data["fear_greed"]
        indicators["indices"] = fg_data["indices"]
        indicators["calendar"] = fg_data["calendar"]
    # What changed since the last run — computed against prev_data, which was read
    # before this run touched anything, and *before* the save below advances it.
    alerts, monitor = compute_exceptions(prev_data, technicals, weights, themes, expo, today)
    for a in alerts:
        print(f"EXCEPTION {a['ticker']} [{a['trigger']}]: {a['detail']}")
    print(f"Exceptions this run: {len(alerts)}")

    # Save dashboard data before the AI/Telegram legs: all market data is already
    # fetched here, and the dashboard must not go stale just because Gemini or
    # Telegram is down. alerts_pending goes in full and is cleared only after a
    # successful send, so a failed send re-reports rather than losing the alert.
    save_market_data(portfolio, watchlist, technicals, portfolio_news, watchlist_news,
                     indicators, earnings, weights, alerts, monitor)

    # Sunday morning sends the whole book regardless — a bot that only ever speaks
    # on an exception is indistinguishable from a dead one, and this one was
    # already dead for 30 days without anyone noticing.
    full = briefing_type == "pre-market" and today.weekday() == 6

    if alerts or full:
        prompt = build_prompt(
            portfolio_news, watchlist_news, market_news,
            indicators, earnings, portfolio, watchlist, technicals, briefing_type,
            weights, TAG, expo, alerts, full,
        )
        analysis = analyze(prompt)
    else:
        analysis = quiet_line(portfolio, expo, earnings)
    print(f"Briefing length: {len(analysis)} chars ({'full' if full else 'monitor'})")

    send_telegram(analysis, bot_token, chat_id)
    print(f"Briefing sent successfully ({briefing_type})")
    clear_alerts_pending()

    save_briefing_history(briefing_type, analysis)

    # Cleanup
    if _yf_cookie_file and os.path.exists(_yf_cookie_file):
        os.unlink(_yf_cookie_file)


if __name__ == "__main__":
    main()
