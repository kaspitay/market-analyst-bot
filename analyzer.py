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


def fetch_technicals(ticker):
    """Fetch price history from Yahoo Finance and compute RSI, SMA 50, SMA 200, current price."""
    try:
        data = curl_json(
            f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=1y"
        )
        result = data["chart"]["result"][0]
        meta = result["meta"]
        timestamps = result.get("timestamp", [])
        quotes = result["indicators"]["quote"][0]
        raw_closes = quotes["close"]
        closes = [c for c in raw_closes if c is not None]

        current = meta["regularMarketPrice"]
        prev_close = meta["chartPreviousClose"]
        change_pct = round((current - prev_close) / prev_close * 100, 2) if prev_close else 0

        rsi = compute_rsi(closes)
        sma50 = round(sum(closes[-50:]) / 50, 2) if len(closes) >= 50 else None
        sma200 = round(sum(closes[-200:]) / 200, 2) if len(closes) >= 200 else None

        # 52-week high/low
        high_52w = round(max(closes[-252:]), 2) if closes else None
        low_52w = round(min(closes[-252:]), 2) if closes else None

        # Price vs SMAs
        signals = []
        if sma50 and current > sma50:
            signals.append("above SMA50")
        elif sma50:
            signals.append("below SMA50")
        if sma200 and current > sma200:
            signals.append("above SMA200")
        elif sma200:
            signals.append("below SMA200")
        if sma50 and sma200:
            if sma50 > sma200:
                signals.append("golden cross")
            else:
                signals.append("death cross")

        # RSI signal
        if rsi and rsi > 70:
            signals.append("overbought")
        elif rsi and rsi < 30:
            signals.append("oversold")

        # 3-month price history for charts (last ~63 trading days)
        chart_data = []
        for i in range(max(0, len(timestamps) - 63), len(timestamps)):
            if i < len(raw_closes) and raw_closes[i] is not None:
                chart_data.append({
                    "t": timestamps[i],
                    "c": round(raw_closes[i], 2),
                })

        return {
            "price": current,
            "prev_close": prev_close,
            "change_pct": change_pct,
            "rsi": rsi,
            "sma50": sma50,
            "sma200": sma200,
            "high_52w": high_52w,
            "low_52w": low_52w,
            "signals": signals,
            "chart": chart_data,
        }
    except Exception as e:
        print(f"Warning: failed to fetch technicals for {ticker}: {e}")
        return None


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

    # Build indicators data
    ind_text = "## Market Indicators\n"
    fg = indicators.get("fear_greed")
    if fg:
        ind_text += f"- Fear & Greed Index: {fg['now']} (prev close: {fg['previous_close']}, 1w ago: {fg['one_week_ago']}, 1m ago: {fg['one_month_ago']})\n"

    indices = indicators.get("indices", {})
    for name, key in [("S&P 500", "sp500"), ("Dow Jones", "dow"), ("NASDAQ", "nasdaq")]:
        idx = indices.get(key)
        if idx:
            ind_text += f"- {name}: ${idx['price']:,.2f} ({idx['changesPercentage']:+.2f}%)\n"

    vix = indicators.get("vix")
    if vix:
        ind_text += f"- VIX: {vix['price']} ({vix['change']:+.2f}, {vix['pct']:+.2f}%)\n"

    # Economic calendar
    cal = indicators.get("calendar", [])
    if cal:
        ind_text += "\n## Upcoming Economic Events\n"
        for e in cal:
            ind_text += f"- {e['date']}: {e['title']} - {e['description']}\n"

    # Build earnings data
    earn_text = ""
    if earnings:
        earn_text = "\n## Upcoming Earnings (next 14 days)\n"
        for e in earnings:
            eps = f"EPS est: ${e['eps_est']:.2f}" if e["eps_est"] is not None else "EPS est: N/A"
            held = "PORTFOLIO" if e["symbol"] in portfolio else "WATCHLIST"
            alloc = f" ({portfolio[e['symbol']]}%)" if e["symbol"] in portfolio else ""
            earn_text += f"- [{held}] {e['symbol']}{alloc}: {e['date']} ({e['hour']}) \u2014 {eps}\n"

    # Build portfolio allocation data
    port_text = "\n## Current Portfolio Allocation\n"
    for ticker, pct in sorted(portfolio.items(), key=lambda x: -x[1]):
        port_text += f"- {ticker}: {pct}%\n"

    # Build technical analysis data
    tech_text = "\n## Technical Indicators\n"
    for ticker, tech in technicals.items():
        if tech:
            rsi_str = f"RSI={tech['rsi']}" if tech["rsi"] else "RSI=N/A"
            sma50_str = f"SMA50=${tech['sma50']}" if tech["sma50"] else "SMA50=N/A"
            sma200_str = f"SMA200=${tech['sma200']}" if tech["sma200"] else "SMA200=N/A"
            signals = ", ".join(tech["signals"]) if tech["signals"] else "no signals"
            alloc = f" ({portfolio[ticker]}%)" if ticker in portfolio else " [WL]"
            tech_text += f"- {ticker}{alloc}: ${tech['price']} ({tech['change_pct']:+.2f}%) | {rsi_str} | {sma50_str} | {sma200_str} | {signals}\n"

    # Build news sections
    news_text = "\n## General Market News\n"
    for article in market_news:
        news_text += f"- {article['headline']}: {article['summary']}\n"

    news_text += "\n## Portfolio Holdings News\n"
    for ticker, articles in portfolio_news.items():
        if articles:
            news_text += f"\n### {ticker} ({portfolio.get(ticker, 0)}% of portfolio)\n"
            for a in articles:
                news_text += f"- {a['headline']}: {a['summary']}\n"

    if any(articles for articles in watchlist_news.values()):
        news_text += "\n## Watchlist News (not holding)\n"
        for ticker, articles in watchlist_news.items():
            if articles:
                news_text += f"\n### {ticker}\n"
                for a in articles:
                    news_text += f"- {a['headline']}: {a['summary']}\n"

    return f"""You are a stock market analyst advising a long-term investor.
Produce a Telegram message briefing {time_context} using the data below.

Format rules:
- Use Telegram HTML: <b>bold</b>, <i>italic</i>
- Use emojis liberally to make it scannable and visually appealing
- Use bullet points (\u2022) for lists
- Keep under 3900 characters total

Structure your message EXACTLY in this order:

1. <b>\U0001f4ca {header}</b>

2. <b>\U0001f3af MARKET DASHBOARD</b>
   Show the major indices (S&P 500, Dow, NASDAQ) with arrows (\u2b06\ufe0f/\u2b07\ufe0f).
   Show VIX level.
   Show Fear & Greed Index score with the appropriate emoji:
   - 0-25: \U0001f631 Extreme Fear
   - 25-45: \U0001f628 Fear
   - 45-55: \U0001f610 Neutral
   - 55-75: \U0001f60e Greed
   - 75-100: \U0001f929 Extreme Greed
   Include the trend (vs previous close and 1 month ago).

3. <b>\U0001f4c5 EARNINGS ALERT</b>
   List upcoming earnings. Use \u26a0\ufe0f for this week, \U0001f4c6 for next week.
   Mark [PORTFOLIO] or [WATCHLIST] for each. Show allocation % for portfolio tickers.

4. <b>\U0001f4b0 ECONOMIC CALENDAR</b>
   List key upcoming economic events (Fed meetings, GDP, CPI, employment).

5. <b>\U0001f4c8 TECHNICAL SNAPSHOT</b>
   Highlight the most notable technical signals from the data:
   - Stocks with RSI > 70 (overbought) or RSI < 30 (oversold) \u2014 these are key signals
   - Golden crosses or death crosses
   - Stocks significantly above/below their SMA200
   Keep it brief \u2014 only mention tickers with actionable signals.

6. <b>\U0001f4bc PORTFOLIO PULSE</b>
   Only tickers with meaningful news. Show ticker, allocation %, news summary, impact.
   Use \U0001f7e2 bullish / \U0001f534 bearish / \u26aa neutral.

7. <b>\U0001f440 WATCHLIST RADAR</b>
   News for watchlist tickers (not held). Flag any that look like good entry points.

8. <b>\U0001f4a1 RECOMMENDATIONS</b>
   This is the MOST IMPORTANT section. Based on ALL the data (news + technicals + sentiment):
   - \U0001f7e2 <b>BUY/ADD</b>: Which tickers to buy or add to, and WHY (combine news + technical signals)
   - \U0001f7e1 <b>HOLD</b>: Which to hold steady, and WHY
   - \U0001f534 <b>SELL/REDUCE</b>: Which to consider selling or trimming, and WHY
   - Only include tickers where there's a clear signal. Skip the rest.
   - Consider the portfolio allocation \u2014 if a position is already large (>10%), be cautious about recommending more.
   - Include watchlist tickers if there's a good entry signal.
   - Use RSI levels to support your recommendations (oversold = potential buy, overbought = caution).

9. <b>\u26a0\ufe0f RISK ALERTS</b>
   Flag any concentration risks (positions >10%), correlated positions moving together,
   or death cross signals that need attention.

10. <b>\U0001f680 OPPORTUNITIES</b>
   Suggest 1-3 stocks or sectors NOT in the portfolio or watchlist that show long-term potential.
   Brief explanation of why each is interesting.

11. <b>\U0001f30d MARKET OUTLOOK</b>
   2-3 sentences on overall sentiment and what to watch {time_context}.

Data:
{ind_text}{earn_text}{port_text}{tech_text}{news_text}"""


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


def send_telegram(text, bot_token, chat_id):
    # Telegram max is 4096 chars. Split on section headers if too long.
    chunks = []
    if len(text) <= 4000:
        chunks = [text]
    else:
        lines = text.split("\n")
        chunk = ""
        for line in lines:
            if len(chunk) + len(line) + 1 > 4000 and chunk:
                chunks.append(chunk)
                chunk = line
            else:
                chunk = chunk + "\n" + line if chunk else line
        if chunk:
            chunks.append(chunk)

    for i, chunk in enumerate(chunks):
        # Try HTML first, fall back to plain text
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
        if i < len(chunks) - 1:
            time.sleep(0.5)


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

    # Fetch technical indicators for all tickers
    print("Fetching technical indicators...")
    technicals = {}
    for ticker in all_tickers:
        technicals[ticker] = fetch_technicals(ticker)
        time.sleep(0.3)  # Rate limit Yahoo Finance

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

    send_telegram(analysis, bot_token, chat_id)
    print(f"Briefing sent successfully ({briefing_type})")

    # Save data for dashboard
    save_market_data(portfolio, watchlist, technicals, portfolio_news, watchlist_news, indicators, earnings)
    save_briefing_history(briefing_type, analysis)


if __name__ == "__main__":
    main()
