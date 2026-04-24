import os
import sys
import json
import subprocess
import time
import requests
from datetime import date, timedelta


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


def build_prompt(portfolio_news, watchlist_news, market_news, indicators, earnings, portfolio, watchlist, briefing_type):
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

5. <b>\U0001f4bc PORTFOLIO PULSE</b>
   Only tickers with meaningful news. Show ticker, allocation %, news summary, impact.
   Use \U0001f7e2 bullish / \U0001f534 bearish / \u26aa neutral.

6. <b>\U0001f440 WATCHLIST RADAR</b>
   News for watchlist tickers (not held). Flag any that look like good entry points.

7. <b>\U0001f4a1 RECOMMENDATIONS</b>
   This is the MOST IMPORTANT section. Based on ALL the data:
   - \U0001f7e2 <b>BUY/ADD</b>: Which tickers to buy or add to, and WHY (1-2 sentences each)
   - \U0001f7e1 <b>HOLD</b>: Which to hold steady, and WHY
   - \U0001f534 <b>SELL/REDUCE</b>: Which to consider selling or trimming, and WHY
   - Only include tickers where there's a clear signal from today's news. Skip the rest.
   - Consider the portfolio allocation \u2014 if a position is already large (>10%), be cautious about recommending more.
   - Include watchlist tickers if there's a good entry signal.

8. <b>\U0001f680 OPPORTUNITIES</b>
   Suggest 1-3 stocks or sectors NOT in the portfolio or watchlist that show long-term potential.
   Brief explanation of why each is interesting.

9. <b>\U0001f30d MARKET OUTLOOK</b>
   2-3 sentences on overall sentiment and what to watch {time_context}.

Data:
{ind_text}{earn_text}{port_text}{news_text}"""


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
    result = subprocess.run(
        ["curl", "-s", "-X", "POST",
         f"https://api.telegram.org/bot{bot_token}/sendMessage",
         "-d", f"chat_id={chat_id}",
         "-d", "parse_mode=HTML",
         "--data-urlencode", f"text={text}"],
        capture_output=True, text=True, timeout=15,
    )
    resp = json.loads(result.stdout)
    if not resp.get("ok"):
        raise RuntimeError(f"Telegram error: {resp}")


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
        indicators, earnings, portfolio, watchlist, briefing_type,
    )
    analysis = analyze(prompt)

    send_telegram(analysis, bot_token, chat_id)
    print(f"Briefing sent successfully ({briefing_type})")


if __name__ == "__main__":
    main()
