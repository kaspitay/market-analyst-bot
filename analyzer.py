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
        osc_score = max(0, min(100, (osc_raw + 70) / 1.4))

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
        vol_score = max(0, min(100, (vol_raw + 50) / 1.0))

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
            "high_52w": high_52w, "low_52w": low_52w,
            "signals": signals,
            "score": composite, "recommendation": recommendation, "score_reasons": score_reasons,
            "chart": chart_data,
        }
    except Exception as e:
        print(f"Warning: failed to fetch technicals for {ticker}: {e}")
        return None


def fetch_price_target(ticker):
    """Fetch analyst price targets from Yahoo Finance quoteSummary."""
    try:
        data = curl_json(
            f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}?modules=financialData"
        )
        fd = data["quoteSummary"]["result"][0]["financialData"]
        return {
            "target_high": fd.get("targetHighPrice", {}).get("raw"),
            "target_low": fd.get("targetLowPrice", {}).get("raw"),
            "target_mean": fd.get("targetMeanPrice", {}).get("raw"),
            "target_median": fd.get("targetMedianPrice", {}).get("raw"),
            "num_analysts": fd.get("numberOfAnalystOpinions", {}).get("raw"),
        }
    except Exception as e:
        print(f"Warning: failed to fetch price target for {ticker}: {e}")
        return None


def compute_price_target_score(price, target_mean, num_analysts):
    """Compute a 0-100 score based on analyst price target upside/downside."""
    if not target_mean or not price or not num_analysts:
        return 50, []  # Neutral if no data

    reasons = []
    upside = ((target_mean - price) / price) * 100

    if upside > 20:
        pt_score = 100
        reasons.append(f"Analyst target ${target_mean:.0f} = {upside:+.0f}% upside (+PT)")
    elif upside > 10:
        pt_score = 80
        reasons.append(f"Analyst target ${target_mean:.0f} = {upside:+.0f}% upside (+PT)")
    elif upside > 0:
        pt_score = 60
        reasons.append(f"Analyst target ${target_mean:.0f} = {upside:+.0f}% upside (+PT)")
    elif upside > -10:
        pt_score = 40
        reasons.append(f"Analyst target ${target_mean:.0f} = {upside:+.0f}% (-PT)")
    else:
        pt_score = 20
        reasons.append(f"Analyst target ${target_mean:.0f} = {upside:+.0f}% downside (-PT)")

    # Low analyst coverage reduces confidence — pull toward neutral
    if num_analysts < 5:
        pt_score = (pt_score + 50) / 2
        reasons.append(f"Low analyst coverage ({num_analysts}) — reduced confidence")

    return pt_score, reasons


def merge_price_target(technicals, price_target):
    """Merge price target data into technicals and recalculate composite score."""
    if not technicals:
        return technicals

    # Add price target fields
    if price_target:
        technicals["target_high"] = price_target.get("target_high")
        technicals["target_low"] = price_target.get("target_low")
        technicals["target_mean"] = price_target.get("target_mean")
        technicals["target_median"] = price_target.get("target_median")
        technicals["num_analysts"] = price_target.get("num_analysts")
    else:
        technicals["target_high"] = None
        technicals["target_low"] = None
        technicals["target_mean"] = None
        technicals["target_median"] = None
        technicals["num_analysts"] = None

    # Compute price target score
    pt_score, pt_reasons = compute_price_target_score(
        technicals.get("price"), technicals.get("target_mean"), technicals.get("num_analysts")
    )

    # Recalculate composite with new weights (must reverse-engineer component scores from existing score)
    # Original: composite = (ma * 0.25) + (osc * 0.40) + (vol * 0.20) + (trend * 0.15)
    # We stored the composite but not the component scores, so recalculate from score_reasons
    # Simpler approach: adjust the existing score by blending in the PT component
    old_score = technicals.get("score", 50)
    # New weights: old components 90%, price target 10%
    new_score = round(old_score * 0.90 + pt_score * 0.10, 1)

    technicals["score"] = new_score
    technicals["score_reasons"] = technicals.get("score_reasons", []) + pt_reasons

    if new_score >= 70:
        technicals["recommendation"] = "Strong Buy"
    elif new_score >= 55:
        technicals["recommendation"] = "Buy"
    elif new_score >= 45:
        technicals["recommendation"] = "Hold"
    elif new_score >= 30:
        technicals["recommendation"] = "Sell"
    else:
        technicals["recommendation"] = "Strong Sell"

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
    market_text = "## Market Context\n"
    fg = indicators.get("fear_greed")
    if fg:
        market_text += f"- Fear & Greed: {fg['now']} (prev: {fg['previous_close']}, 1m ago: {fg['one_month_ago']})\n"
    indices = indicators.get("indices", {})
    for name, key in [("S&P 500", "sp500"), ("Dow", "dow"), ("NASDAQ", "nasdaq")]:
        idx = indices.get(key)
        if idx:
            market_text += f"- {name}: ${idx['price']:,.2f} ({idx['changesPercentage']:+.2f}%)\n"
    vix = indicators.get("vix")
    if vix:
        market_text += f"- VIX: {vix['price']} ({vix['pct']:+.2f}%)\n"

    # --- UPCOMING EVENTS ---
    events_text = "\n## Upcoming Events\n"
    cal = indicators.get("calendar", [])
    for e in cal[:3]:
        events_text += f"- {e['date']}: {e['title']}\n"
    if earnings:
        for e in earnings[:6]:
            held = "PORTFOLIO" if e["symbol"] in portfolio else "WL"
            alloc = f" ({portfolio[e['symbol']]}%)" if e["symbol"] in portfolio else ""
            events_text += f"- [{held}] {e['symbol']}{alloc}: earnings {e['date']} ({e['hour']})\n"

    # --- PRE-COMPUTE ALGORITHM DECISIONS ---
    buys, holds, sells = [], [], []
    for ticker, tech in technicals.items():
        if not tech or ticker not in portfolio:
            continue
        alloc = portfolio.get(ticker, 0)
        rec = tech.get("recommendation", "Hold")
        score = tech.get("score", 50)
        rsi = tech.get("rsi")
        signals = tech.get("signals", [])
        pt_mean = tech.get("target_mean")
        upside = round((pt_mean - tech["price"]) / tech["price"] * 100, 1) if pt_mean and tech.get("price") else None
        upside_str = f"target ${pt_mean:.0f} ({upside:+.1f}%)" if upside is not None else "no target"
        reasons = ", ".join(tech.get("score_reasons", [])[:3])
        has_earnings = any(e["symbol"] == ticker for e in earnings)

        entry = f"{ticker} ({alloc}%): score={score}, ${tech['price']}, RSI={rsi}, {upside_str}, signals=[{', '.join(signals[:3])}], reasons=[{reasons}]"
        if has_earnings:
            entry += " *** EARNINGS SOON ***"

        if rec in ("Strong Buy", "Buy"):
            buys.append((score, entry))
        elif rec in ("Strong Sell", "Sell"):
            sells.append((score, entry))
        else:
            holds.append((score, entry))

    # Watchlist picks
    wl_picks = []
    for ticker, tech in technicals.items():
        if not tech or ticker in portfolio:
            continue
        score = tech.get("score", 50)
        rec = tech.get("recommendation", "Hold")
        rsi = tech.get("rsi")
        pt_mean = tech.get("target_mean")
        upside = round((pt_mean - tech["price"]) / tech["price"] * 100, 1) if pt_mean and tech.get("price") else None
        upside_str = f"target ${pt_mean:.0f} ({upside:+.1f}%)" if upside is not None else "no target"
        if rec in ("Strong Buy", "Buy") or (rsi and rsi < 35):
            wl_picks.append((score, f"{ticker} [WL]: score={score}, ${tech['price']}, RSI={rsi}, {upside_str}, signals=[{', '.join(tech.get('signals', [])[:3])}]"))

    buys.sort(key=lambda x: -x[0])
    sells.sort(key=lambda x: x[0])
    holds.sort(key=lambda x: -x[0])
    wl_picks.sort(key=lambda x: -x[0])

    algo_text = "\n## ALGORITHM DECISIONS (pre-computed — present these to the user)\n"
    algo_text += "\n### BUY/ADD (top picks by score):\n"
    for _, entry in buys[:3]:
        algo_text += f"- {entry}\n"
    if not buys:
        algo_text += "- None\n"

    algo_text += "\n### HOLD:\n"
    for _, entry in holds[:3]:
        algo_text += f"- {entry}\n"

    algo_text += "\n### SELL/REDUCE (weakest by score):\n"
    for _, entry in sells[:3]:
        algo_text += f"- {entry}\n"
    if not sells:
        algo_text += "- None\n"

    algo_text += "\n### WATCHLIST ENTRY SIGNALS:\n"
    for _, entry in wl_picks[:2]:
        algo_text += f"- {entry}\n"
    if not wl_picks:
        algo_text += "- None\n"

    # --- CONCENTRATION RISKS ---
    risk_text = "\n## Concentration Risks\n"
    for ticker, alloc in sorted(portfolio.items(), key=lambda x: -x[1]):
        if alloc > 10:
            risk_text += f"- {ticker}: {alloc}% of portfolio\n"

    # --- NEWS ---
    news_text = "\n## Market News\n"
    for article in market_news[:5]:
        news_text += f"- {article['headline']}\n"
    news_text += "\n## Ticker News\n"
    for ticker, articles in {**portfolio_news, **watchlist_news}.items():
        if articles:
            news_text += f"- {ticker}: {articles[0]['headline']}\n"

    return f"""You are a market analyst reviewing our algorithm's output and preparing a daily briefing {time_context}.

YOUR ROLE: Our scoring algorithm has already analyzed all tickers and made BUY/HOLD/SELL decisions (shown below). Your job is to:
1. Present the algorithm's recommendations clearly
2. Add brief context from news/earnings/macro for each recommendation
3. Flag if you DISAGREE with any recommendation based on the news or upcoming events (e.g. "algorithm says BUY but earnings in 2 days — consider waiting")
4. Highlight concentration risks inline

FORMAT RULES (strict):
- MAXIMUM 2800 characters. Must fit in ONE Telegram message.
- ONLY use <b> and <i> HTML tags. No other tags.
- Use \u2022 bullets, one short line each.
- Be concise — short sentences, no filler.

EXACTLY these 5 sections:

<b>\U0001f4ca {header}</b>

<b>\U0001f3af MARKET</b>
Indices with arrows, VIX, Fear & Greed (emoji: \U0001f631<25, \U0001f628<45, \U0001f610<55, \U0001f60e<75, \U0001f929 75+). Max 5 lines.

<b>\U0001f4c5 UPCOMING</b>
Top 5 items: earnings with dates + key economic events. One line each.

<b>\U0001f4a1 ACTIONS</b>
Present the algorithm's decisions below. For each ticker: one line with ticker, allocation %, the recommendation, and brief WHY (add news context or your own note).
Use \U0001f7e2 for BUY, \U0001f7e1 for HOLD, \U0001f534 for SELL.
Add \u26a0\ufe0f inline for concentration risk (>10%) or earnings-soon warnings.
If you disagree with a recommendation, say so briefly with \U0001f9d0.
Max 2 per category. Skip empty categories.

<b>\U0001f440 WATCHLIST</b>
Max 2 watchlist tickers with entry signals. Skip if none.

Do NOT add any other sections. No Outlook, no Opportunities, no Technical Snapshot, no Risk Alerts section.

DATA:
{market_text}{events_text}{algo_text}{risk_text}{news_text}"""


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
    """Strip HTML tags that Telegram doesn't support, keeping only allowed ones."""
    import re
    # Telegram HTML mode only supports: b, i, u, s, code, pre, a
    allowed = {'b', 'i', 'u', 's', 'code', 'pre', 'a'}
    def replace_tag(m):
        tag = m.group(1).lower().strip().split()[0].lstrip('/')
        if tag in allowed:
            return m.group(0)
        return ''
    return re.sub(r'<(/?\s*[a-zA-Z][^>]*)>', replace_tag, text)


def send_telegram(text, bot_token, chat_id):
    # Sanitize HTML to prevent parse failures
    text = sanitize_telegram_html(text)

    # Send as a single message (must be under 4096 chars)
    for mode in ["HTML", None]:
        args = ["curl", "-s", "-X", "POST",
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                "-d", f"chat_id={chat_id}",
                "--data-urlencode", f"text={text}"]
        if mode:
            args += ["-d", f"parse_mode={mode}"]
        result = subprocess.run(args, capture_output=True, text=True, timeout=15)
        resp = json.loads(result.stdout)
        if resp.get("ok"):
            return
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

    # Fetch technical indicators and price targets for all tickers
    print("Fetching technical indicators and price targets...")
    technicals = {}
    for ticker in all_tickers:
        technicals[ticker] = fetch_technicals(ticker)
        time.sleep(0.2)
        pt = fetch_price_target(ticker)
        technicals[ticker] = merge_price_target(technicals[ticker], pt)
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

    # Safety net: truncate if too long for single Telegram message
    if len(analysis) > 3500:
        print(f"Warning: briefing is {len(analysis)} chars, truncating...")
        cutoff = analysis[:3500].rfind('\n\n<b>')
        if cutoff > 2000:
            analysis = analysis[:cutoff]

    send_telegram(analysis, bot_token, chat_id)
    print(f"Briefing sent successfully ({briefing_type})")

    # Save data for dashboard
    save_market_data(portfolio, watchlist, technicals, portfolio_news, watchlist_news, indicators, earnings)
    save_briefing_history(briefing_type, analysis)


if __name__ == "__main__":
    main()
