import os
import sys
import json
import subprocess
import time
import requests
from datetime import date, datetime, timedelta


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
    today = date.today().isoformat()
    url = "https://finnhub.io/api/v1/company-news"
    params = {"symbol": ticker, "from": today, "to": today, "token": api_key}
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    articles = resp.json()[:3]
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


def compute_stochastic(highs, lows, closes, k_period=14, d_period=3):
    if len(closes) < k_period:
        return None, None
    k_values = []
    for i in range(k_period - 1, len(closes)):
        h = max(highs[i - k_period + 1:i + 1])
        l = min(lows[i - k_period + 1:i + 1])
        if h == l:
            k_values.append(50)
        else:
            k_values.append(((closes[i] - l) / (h - l)) * 100)
    if len(k_values) < d_period:
        return k_values[-1] if k_values else None, None
    d_value = sum(k_values[-d_period:]) / d_period
    return round(k_values[-1], 1), round(d_value, 1)


def compute_mfi(highs, lows, closes, volumes, period=14):
    if len(closes) < period + 1:
        return None
    pos_flow = 0
    neg_flow = 0
    for i in range(len(closes) - period, len(closes)):
        tp = (highs[i] + lows[i] + closes[i]) / 3
        prev_tp = (highs[i - 1] + lows[i - 1] + closes[i - 1]) / 3
        raw_mf = tp * volumes[i]
        if tp > prev_tp:
            pos_flow += raw_mf
        else:
            neg_flow += raw_mf
    if neg_flow == 0:
        return 100.0
    mfr = pos_flow / neg_flow
    return round(100 - (100 / (1 + mfr)), 1)


def compute_adx(highs, lows, closes, period=14):
    if len(closes) < period * 2:
        return None
    tr_list, plus_dm, minus_dm = [], [], []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        tr_list.append(tr)
        plus_dm.append(up if up > down and up > 0 else 0)
        minus_dm.append(down if down > up and down > 0 else 0)
    # Smooth with EMA
    def smooth(data, n):
        s = sum(data[:n])
        result = [s]
        for i in range(n, len(data)):
            s = s - s / n + data[i]
            result.append(s)
        return result
    str_list = smooth(tr_list, period)
    spdm = smooth(plus_dm, period)
    sndm = smooth(minus_dm, period)
    dx_list = []
    for i in range(len(str_list)):
        if str_list[i] == 0:
            continue
        pdi = 100 * spdm[i] / str_list[i]
        ndi = 100 * sndm[i] / str_list[i]
        if pdi + ndi == 0:
            continue
        dx_list.append(100 * abs(pdi - ndi) / (pdi + ndi))
    if len(dx_list) < period:
        return None
    adx = sum(dx_list[-period:]) / period
    return round(adx, 1)


def compute_bollinger_bands(closes, period=20, num_std=2):
    if len(closes) < period:
        return None, None, None
    window = closes[-period:]
    middle = sum(window) / period
    variance = sum((c - middle) ** 2 for c in window) / period
    stddev = variance ** 0.5
    return round(middle, 4), round(middle + num_std * stddev, 4), round(middle - num_std * stddev, 4)


def compute_obv(closes, volumes):
    if len(closes) < 2 or len(volumes) < 2:
        return None, None
    obv = [0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv.append(obv[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            obv.append(obv[-1] - volumes[i])
        else:
            obv.append(obv[-1])
    return obv[-1], obv


def detect_obv_divergence(closes, obv_values, lookback=20):
    if not obv_values or len(closes) < lookback or len(obv_values) < lookback:
        return None
    price_delta = closes[-1] - closes[-lookback]
    obv_delta = obv_values[-1] - obv_values[-lookback]
    if price_delta < 0 and obv_delta > 0:
        return "bullish"
    if price_delta > 0 and obv_delta < 0:
        return "bearish"
    return None


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
        prev_close = meta["chartPreviousClose"]
        change_pct = round((current - prev_close) / prev_close * 100, 2) if prev_close else 0

        # --- INDICATORS ---
        rsi = compute_rsi(closes)
        sma50 = round(sum(closes[-50:]) / 50, 2) if len(closes) >= 50 else None
        sma200 = round(sum(closes[-200:]) / 200, 2) if len(closes) >= 200 else None
        ema20 = compute_ema(closes, 20)
        ema20_val = round(ema20[-1], 2) if ema20 else None

        # MACD (12, 26, 9)
        ema12 = compute_ema(closes, 12)
        ema26 = compute_ema(closes, 26)
        macd_line, macd_signal, macd_hist = None, None, None
        if ema12 and ema26:
            offset = len(ema12) - len(ema26)
            macd_vals = [ema12[i + offset] - ema26[i] for i in range(len(ema26))]
            macd_line = round(macd_vals[-1], 4) if macd_vals else None
            sig = compute_ema(macd_vals, 9)
            if sig:
                macd_signal = round(sig[-1], 4)
                macd_hist = round(macd_vals[-1] - sig[-1], 4)

        # Stochastic (14, 3)
        stoch_k, stoch_d = compute_stochastic(highs, lows, closes)

        # MFI (14)
        mfi = compute_mfi(highs, lows, closes, volumes)

        # ADX (14)
        adx = compute_adx(highs, lows, closes)

        # Bollinger Bands (20, 2)
        bb_middle, bb_upper, bb_lower = compute_bollinger_bands(closes)

        # OBV
        obv_current, obv_series = compute_obv(closes, volumes)
        obv_divergence = detect_obv_divergence(closes, obv_series) if obv_series else None

        # 52-week high/low
        high_52w = round(max(closes[-252:]), 2) if closes else None
        low_52w = round(min(closes[-252:]), 2) if closes else None

        # Volume averages
        vol_avg_20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else None

        # --- SIGNALS ---
        signals = []
        if sma50 and current > sma50: signals.append("above SMA50")
        elif sma50: signals.append("below SMA50")
        if sma200 and current > sma200: signals.append("above SMA200")
        elif sma200: signals.append("below SMA200")
        if sma50 and sma200:
            if sma50 > sma200: signals.append("golden cross")
            else: signals.append("death cross")
        if rsi and rsi > 70: signals.append("overbought")
        elif rsi and rsi < 30: signals.append("oversold")
        if macd_hist is not None:
            if macd_hist > 0: signals.append("MACD bullish")
            else: signals.append("MACD bearish")
        if adx and adx > 25: signals.append(f"strong trend (ADX {adx})")
        if bb_upper and bb_lower:
            if current <= bb_lower: signals.append("BB oversold")
            elif current >= bb_upper: signals.append("BB overbought")
        if obv_divergence == "bullish": signals.append("OBV bullish divergence")
        elif obv_divergence == "bearish": signals.append("OBV bearish divergence")

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
        score_reasons = []

        # 1. Moving Average Score (0-100, weight 25%)
        ma_score = 0
        if sma50 and current > sma50:
            ma_score += 25; score_reasons.append("Price > SMA50 (+MA)")
        if sma200 and current > sma200:
            ma_score += 25; score_reasons.append("Price > SMA200 (+MA)")
        if sma50 and sma200 and sma50 > sma200:
            ma_score += 25; score_reasons.append("Golden cross: SMA50 > SMA200 (+MA)")
        if ema20_val and sma50 and ema20_val > sma50:
            ma_score += 25; score_reasons.append("EMA20 > SMA50 (+MA)")

        # 2. Oscillator Score (0-100, weight 40%)
        osc_raw = 0  # range roughly -70 to +70, normalized later
        if rsi:
            if rsi < 30: osc_raw += 25; score_reasons.append(f"RSI {rsi} — oversold (+Osc)")
            elif rsi < 40: osc_raw += 12; score_reasons.append(f"RSI {rsi} — low (+Osc)")
            elif rsi > 70: osc_raw -= 25; score_reasons.append(f"RSI {rsi} — overbought (-Osc)")
            elif rsi > 60: osc_raw -= 12; score_reasons.append(f"RSI {rsi} — high (-Osc)")
        if macd_line is not None and macd_signal is not None:
            if macd_line > macd_signal and macd_line > 0:
                osc_raw += 25; score_reasons.append("MACD strong bullish (+Osc)")
            elif macd_line > macd_signal:
                osc_raw += 12; score_reasons.append("MACD bullish crossover (+Osc)")
            elif macd_line < macd_signal and macd_line < 0:
                osc_raw -= 25; score_reasons.append("MACD strong bearish (-Osc)")
            elif macd_line < macd_signal:
                osc_raw -= 12; score_reasons.append("MACD bearish crossover (-Osc)")
        if stoch_k is not None and stoch_d is not None:
            if stoch_k < 20 and stoch_k > stoch_d:
                osc_raw += 20; score_reasons.append(f"Stochastic oversold + bullish ({stoch_k:.0f}) (+Osc)")
            elif stoch_k > 80 and stoch_k < stoch_d:
                osc_raw -= 20; score_reasons.append(f"Stochastic overbought + bearish ({stoch_k:.0f}) (-Osc)")
        if bb_upper and bb_lower:
            if current <= bb_lower: osc_raw += 20; score_reasons.append("BB oversold (+Osc)")
            elif current >= bb_upper: osc_raw -= 20; score_reasons.append("BB overbought (-Osc)")
        osc_score = max(0, min(100, (osc_raw + 90) / 1.8))

        # 3. Volume Score (0-100, weight 20%)
        vol_raw = 0
        if mfi is not None:
            if mfi < 20: vol_raw += 20; score_reasons.append(f"MFI {mfi} — oversold (+Vol)")
            elif mfi > 80: vol_raw -= 20; score_reasons.append(f"MFI {mfi} — overbought (-Vol)")
        if vol_avg_20 and len(volumes) > 0 and len(opens) > 0:
            if closes[-1] > opens[-1] and volumes[-1] > vol_avg_20:
                vol_raw += 15; score_reasons.append("Bullish volume confirmation (+Vol)")
            elif closes[-1] < opens[-1] and volumes[-1] > vol_avg_20:
                vol_raw -= 15; score_reasons.append("Bearish volume confirmation (-Vol)")
        if obv_divergence == "bullish":
            vol_raw += 15; score_reasons.append("OBV bullish divergence (+Vol)")
        elif obv_divergence == "bearish":
            vol_raw -= 15; score_reasons.append("OBV bearish divergence (-Vol)")
        vol_score = max(0, min(100, (vol_raw + 65) / 1.3))

        # 4. Trend Strength Score (0-100, weight 15%)
        if adx:
            if adx > 40: trend_score = 100; score_reasons.append(f"ADX {adx} — very strong trend (+Trend)")
            elif adx > 25: trend_score = 75; score_reasons.append(f"ADX {adx} — strong trend (+Trend)")
            elif adx > 20: trend_score = 50
            else: trend_score = 25; score_reasons.append(f"ADX {adx} — weak/ranging (-Trend)")
        else:
            trend_score = 50

        # Composite
        composite = (ma_score * 0.25) + (osc_score * 0.40) + (vol_score * 0.20) + (trend_score * 0.15)
        composite = round(composite, 1)

        if composite >= 70: recommendation = "Strong Buy"
        elif composite >= 55: recommendation = "Buy"
        elif composite >= 45: recommendation = "Hold"
        elif composite >= 30: recommendation = "Sell"
        else: recommendation = "Strong Sell"

        # 3-month price history for charts
        chart_data = []
        for i in range(max(0, len(valid_timestamps) - 63), len(valid_timestamps)):
            chart_data.append({"t": valid_timestamps[i], "c": round(closes[i], 2), "v": volumes[i]})

        return {
            "price": current, "prev_close": prev_close, "change_pct": change_pct, "ytd_pct": ytd_pct,
            "rsi": rsi, "sma50": sma50, "sma200": sma200,
            "macd": macd_line, "macd_signal": macd_signal, "macd_hist": macd_hist,
            "stoch_k": stoch_k, "stoch_d": stoch_d,
            "mfi": mfi, "adx": adx,
            "bb_upper": bb_upper, "bb_lower": bb_lower, "bb_middle": bb_middle,
            "obv": obv_current, "obv_divergence": obv_divergence,
            "high_52w": high_52w, "low_52w": low_52w,
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
        modules = "financialData,defaultKeyStatistics,summaryDetail,earningsTrend"
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

        def g(obj, key):
            v = obj.get(key, {})
            return v.get("raw") if isinstance(v, dict) else None

        # Earnings trend growth rates
        trends = et.get("trend", [])
        cy_growth = None
        ny_growth = None
        try:
            if len(trends) > 2:
                cy_growth = trends[2].get("growth", {}).get("raw")
            if len(trends) > 3:
                ny_growth = trends[3].get("growth", {}).get("raw")
        except Exception:
            pass

        price = g(fd, "currentPrice")
        market_cap = g(sd, "marketCap")
        total_revenue = g(fd, "totalRevenue")
        shares = round(market_cap / price) if market_cap and price and price > 0 else None
        rev_per_share = round(total_revenue / shares, 2) if total_revenue and shares else None

        return {
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
            },
        }
    except Exception as e:
        print(f"Warning: failed to fetch fundamentals for {ticker}: {e}")
        return None


def _score_tier(val, tiers):
    """Score a value against [(threshold, score), ...] tiers. Returns score for first matching tier."""
    if val is None:
        return 50
    for threshold, score in tiers:
        if val <= threshold:
            return score
    return tiers[-1][1] if tiers else 50


def compute_fundamental_score(fund, price, target_mean, num_analysts):
    """Compute 0-100 fundamental score from valuation, profitability, growth, health, and price target."""
    if not fund:
        return 50, []
    reasons = []

    # 1. Valuation (25%)
    pe = _score_tier(fund.get("trailingPE"), [(15, 100), (20, 75), (30, 50), (40, 25)])
    if fund.get("trailingPE") and fund["trailingPE"] < 0:
        pe = 25  # Negative earnings
    peg = _score_tier(fund.get("pegRatio"), [(1, 100), (1.5, 75), (2, 50), (3, 25)])
    pb = _score_tier(fund.get("priceToBook"), [(3, 75), (5, 50), (10, 25)])
    val_score = (pe + peg + pb) / 3
    if val_score >= 70:
        reasons.append(f"Attractive valuation (P/E={fund.get('trailingPE', 'N/A')}) (+Fund)")
    elif val_score <= 30:
        reasons.append(f"Expensive valuation (P/E={fund.get('trailingPE', 'N/A')}) (-Fund)")

    # 2. Profitability (20%)
    gm = _score_tier(fund.get("grossMargins"), [(-999, 0)]) if fund.get("grossMargins") is None else (
        100 if fund["grossMargins"] > 0.5 else 75 if fund["grossMargins"] > 0.3 else 50 if fund["grossMargins"] > 0.15 else 25
    )
    om = 50
    if fund.get("operatingMargins") is not None:
        om = 100 if fund["operatingMargins"] > 0.2 else 75 if fund["operatingMargins"] > 0.1 else 50 if fund["operatingMargins"] > 0 else 25
    roe = 50
    if fund.get("returnOnEquity") is not None:
        roe = 100 if fund["returnOnEquity"] > 0.2 else 75 if fund["returnOnEquity"] > 0.1 else 50 if fund["returnOnEquity"] > 0 else 25
    prof_score = (gm + om + roe) / 3
    if prof_score >= 70:
        reasons.append(f"Strong profitability (GM={fund.get('grossMargins', 0):.0%}, ROE={fund.get('returnOnEquity', 0):.0%}) (+Fund)")

    # 3. Growth (20%)
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
        reasons.append(f"Strong growth (Rev={fund.get('revenueGrowth', 0):.0%}, EPS={fund.get('earningsGrowth', 0):.0%}) (+Fund)")
    elif grow_score <= 30:
        reasons.append(f"Weak growth (-Fund)")

    # 4. Financial Health (15%)
    de = 50
    if fund.get("debtToEquity") is not None:
        de = 100 if fund["debtToEquity"] < 50 else 75 if fund["debtToEquity"] < 100 else 50 if fund["debtToEquity"] < 200 else 25
    cr = 50
    if fund.get("currentRatio") is not None:
        cr = 100 if fund["currentRatio"] > 2 else 75 if fund["currentRatio"] > 1.5 else 50 if fund["currentRatio"] > 1 else 25
    fcf = 75 if fund.get("freeCashflow") and fund["freeCashflow"] > 0 else 25
    health_score = (de + cr + fcf) / 3

    # 5. Price Target (20%)
    pt_score = 50
    if target_mean and price and num_analysts:
        upside = ((target_mean - price) / price) * 100
        if upside > 20: pt_score = 100
        elif upside > 10: pt_score = 80
        elif upside > 0: pt_score = 60
        elif upside > -10: pt_score = 40
        else: pt_score = 20
        if num_analysts and num_analysts < 5:
            pt_score = (pt_score + 50) / 2
        reasons.append(f"Analyst target ${target_mean:.0f} ({upside:+.0f}%)")

    fund_total = val_score * 0.25 + prof_score * 0.20 + grow_score * 0.20 + health_score * 0.15 + pt_score * 0.20
    return round(fund_total, 1), reasons


def merge_fundamentals(technicals, fund_data):
    """Merge fundamentals and price targets into technicals, compute combined score."""
    if not technicals:
        return technicals

    pt = fund_data.get("price_target", {}) if fund_data else {}
    fund = fund_data.get("fundamentals", {}) if fund_data else {}

    # Price target fields
    technicals["target_high"] = pt.get("target_high")
    technicals["target_low"] = pt.get("target_low")
    technicals["target_mean"] = pt.get("target_mean")
    technicals["target_median"] = pt.get("target_median")
    technicals["num_analysts"] = pt.get("num_analysts")

    # Fundamentals dict
    technicals["fundamentals"] = fund if fund else None

    # Technical score stays pure (already computed in fetch_technicals)
    tech_score = technicals.get("score", 50)
    technicals["tech_score"] = tech_score

    # Compute fundamental score
    fund_score, fund_reasons = compute_fundamental_score(
        fund, technicals.get("price"), pt.get("target_mean"), pt.get("num_analysts")
    )
    technicals["fund_score"] = fund_score
    technicals["fund_score_reasons"] = fund_reasons

    # Combined score: tech 40%, fundamental 60%
    combined = round(tech_score * 0.40 + fund_score * 0.60, 1)
    technicals["combined_score"] = combined

    # Recommendation from combined score
    if combined >= 70: technicals["recommendation"] = "Strong Buy"
    elif combined >= 55: technicals["recommendation"] = "Buy"
    elif combined >= 45: technicals["recommendation"] = "Hold"
    elif combined >= 30: technicals["recommendation"] = "Sell"
    else: technicals["recommendation"] = "Strong Sell"

    # Keep pure technical recommendation
    if tech_score >= 70: technicals["tech_recommendation"] = "Strong Buy"
    elif tech_score >= 55: technicals["tech_recommendation"] = "Buy"
    elif tech_score >= 45: technicals["tech_recommendation"] = "Hold"
    elif tech_score >= 30: technicals["tech_recommendation"] = "Sell"
    else: technicals["tech_recommendation"] = "Strong Sell"

    # Merge reasons
    technicals["score_reasons"] = technicals.get("score_reasons", []) + fund_reasons

    return technicals


def fetch_earnings(tickers, api_key):
    try:
        today = date.today()
        end = today + timedelta(days=14)
        resp = requests.get(
            "https://finnhub.io/api/v1/calendar/earnings",
            params={"from": today.isoformat(), "to": end.isoformat(), "token": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        ticker_set = set(tickers)
        earnings = []
        for e in data.get("earningsCalendar", []):
            if e.get("symbol") in ticker_set:
                hour_map = {"bmo": "Before Open", "amc": "After Close", "": "TBD"}
                earnings.append({
                    "symbol": e["symbol"],
                    "date": e["date"],
                    "hour": hour_map.get(e.get("hour", ""), "TBD"),
                    "eps_est": e.get("epsEstimate"),
                })
        earnings.sort(key=lambda x: x["date"])
        return earnings
    except Exception as e:
        print(f"Warning: failed to fetch earnings: {e}")
        return []


def build_prompt(portfolio_news, watchlist_news, market_news, indicators, earnings, portfolio, watchlist, technicals, briefing_type):
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
            alloc = f" ({portfolio[e['symbol']]}%)" if e["symbol"] in portfolio else ""
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
        alloc = portfolio.get(ticker, 0)
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
            wl_picks.append((combined, f"{ticker}: combined={combined}, ${tech['price']:.2f}, RSI={rsi}, {upside_str}, signals=[{', '.join(tech.get('signals', [])[:3])}]"))

    buys.sort(key=lambda x: -x[0])
    sells.sort(key=lambda x: x[0])
    holds.sort(key=lambda x: -x[0])
    wl_picks.sort(key=lambda x: -x[0])

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
            alloc = f" ({portfolio[ticker]}%)" if ticker in portfolio else " [WL]"
            pt_mean = tech.get("target_mean")
            pt_str = f" | target=${pt_mean:.0f}" if pt_mean else ""
            tech_text += f"- {ticker}{alloc}: ${tech['price']:.2f} ({tech.get('change_pct', 0):+.2f}%) | {rsi_str} | {sma50_str} | {sma200_str} | score={tech.get('score', 'N/A')}{pt_str} | {signals}\n"

    # --- CONCENTRATION RISKS ---
    risk_text = "\n## Concentration Risks\n"
    for ticker, alloc in sorted(portfolio.items(), key=lambda x: -x[1]):
        if alloc > 10:
            risk_text += f"- {ticker}: {alloc}% of portfolio\n"

    # --- NEWS (with full summaries) ---
    news_text = "\n## General Market News\n"
    for article in market_news[:5]:
        news_text += f"- {article['headline']}: {article['summary']}\n"

    news_text += "\n## Portfolio Holdings News\n"
    for ticker, articles in portfolio_news.items():
        if articles:
            alloc = portfolio.get(ticker, 0)
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

YOUR ROLE: Our scoring algorithm (0-100, based on RSI, MACD, SMA, volume, ADX, and analyst price targets) has pre-computed BUY/HOLD/SELL decisions shown in the data. Use these as a starting point, but make your OWN analysis by combining algorithm scores + news + technicals + upcoming events. If you disagree with the algorithm, say so and explain why.

FORMAT RULES (strict):
- Keep under 3900 characters total.
- ONLY use <b> and <i> HTML tags. No <u>, no <s>, no other tags.
- Use emojis liberally to make it scannable and visually appealing.
- Use \u2022 bullets for lists, keep each bullet to one line.

Structure your message EXACTLY in this order:

<b>\U0001f4ca {header}</b>

<b>\U0001f3af MARKET DASHBOARD</b>
Show indices with price and arrows (\u2b06\ufe0f/\u2b07\ufe0f), VIX with arrow.
Fear & Greed score with emoji (\U0001f631<25, \U0001f628<45, \U0001f610<55, \U0001f60e<75, \U0001f929 75+).
Include trend vs previous close and 1 month ago with arrows.

<b>\U0001f4c5 EARNINGS ALERT</b>
List upcoming earnings from data. Use \u26a0\ufe0f for this week, \U0001f4c6 for next week. Show [PORTFOLIO] or [WATCHLIST] and allocation %.

<b>\U0001f4b0 ECONOMIC CALENDAR</b>
Key upcoming economic events with date and descriptive emoji. One line each.

<b>\U0001f4c8 TECHNICAL SNAPSHOT</b>
\U0001f525 Overbought (RSI > 70): list tickers with allocation% or [WL] and RSI value.
\U0001f9ca Oversold (RSI < 30): list tickers with allocation% or [WL] and RSI value.
\U0001f6a7 Death Crosses Identified: comma-separated list of tickers.
\U0001f31f Golden Crosses Identified: comma-separated list of tickers.
Only include categories that have tickers. Skip empty ones.

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
\u26a0\ufe0f Add inline warnings for concentration risk (>10%) or death crosses.
Skip empty categories.

<b>\u26a0\ufe0f RISK ALERTS</b>
Flag concentration risks (positions >10%), correlated positions, death crosses needing attention.

<b>\U0001f30d MARKET OUTLOOK</b>
2-3 sentences on overall sentiment and what to watch {time_context}.

Do NOT suggest stocks outside the portfolio/watchlist.

DATA:
{market_text}{earn_text}{cal_text}{algo_text}{tech_text}{risk_text}{news_text}"""


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


def save_market_data(portfolio, watchlist, technicals, portfolio_news, watchlist_news, indicators, earnings):
    """Save all market data as JSON for the dashboard."""
    data_dir = os.path.join(os.path.dirname(__file__), "docs", "data")
    os.makedirs(data_dir, exist_ok=True)

    # Build per-ticker data
    tickers_data = {}
    for ticker, alloc in portfolio.items():
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
    }

    filepath = os.path.join(data_dir, "market-data.json")
    with open(filepath, "w") as f:
        json.dump(market_data, f, separators=(",", ":"))
    print(f"Market data saved ({filepath})")


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

    finnhub_key = os.environ["FINNHUB_API_KEY"]
    bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    config = load_config()
    portfolio = config["portfolio"]
    watchlist = config["watchlist"]
    all_tickers = list(portfolio.keys()) + watchlist

    # Fetch market data
    market_news = fetch_market_news(finnhub_key)
    fg_data = fetch_fear_greed_data()
    vix = fetch_vix()
    earnings = fetch_earnings(all_tickers, finnhub_key)

    # Initialize Yahoo Finance auth for price targets
    init_yahoo_auth()

    # Fetch technical indicators and price targets for all tickers
    print("Fetching technical indicators and price targets...")
    technicals = {}
    for ticker in all_tickers:
        technicals[ticker] = fetch_technicals(ticker)
        time.sleep(0.2)
        fund_data = fetch_fundamentals(ticker)
        technicals[ticker] = merge_fundamentals(technicals[ticker], fund_data)
        time.sleep(0.2)  # Rate limit Yahoo Finance

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
    prompt = build_prompt(
        portfolio_news, watchlist_news, market_news,
        indicators, earnings, portfolio, watchlist, technicals, briefing_type,
    )
    analysis = analyze(prompt)
    print(f"Briefing length: {len(analysis)} chars")

    send_telegram(analysis, bot_token, chat_id)
    print(f"Briefing sent successfully ({briefing_type})")

    # Save data for dashboard
    save_market_data(portfolio, watchlist, technicals, portfolio_news, watchlist_news, indicators, earnings)
    save_briefing_history(briefing_type, analysis)

    # Cleanup
    if _yf_cookie_file and os.path.exists(_yf_cookie_file):
        os.unlink(_yf_cookie_file)


if __name__ == "__main__":
    main()
