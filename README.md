# Market Analyst Bot

An automated stock market analysis bot that delivers daily briefings to Telegram. It fetches real-time market data, news, and economic indicators, then uses AI to generate actionable investment insights.

## What It Does

The bot runs twice daily via GitHub Actions:
- **8:30 AM ET** — Pre-market briefing before market opens
- **3:00 PM ET** — Pre-close briefing before market closes

Each briefing includes:
- **Market Dashboard** — S&P 500, Dow Jones, NASDAQ, VIX, Fear & Greed Index
- **Earnings Alerts** — Upcoming earnings for your portfolio and watchlist (next 14 days)
- **Economic Calendar** — Fed meetings, GDP reports, CPI, employment data
- **Portfolio Pulse** — News and impact analysis for your holdings with allocation %
- **Watchlist Radar** — News for stocks you're watching but don't hold
- **Buy/Hold/Sell Recommendations** — AI-generated advice based on news and sentiment
- **Opportunities** — New stocks and sectors worth investigating
- **Market Outlook** — Overall sentiment summary

## Architecture

```
GitHub Actions (cron) → analyzer.py → Telegram Bot API
                            │
                ┌───────────┼───────────┐
                ▼           ▼           ▼
           Finnhub API   feargreedmeter  Gemini API
          (news/earnings)  (F&G, indices) (AI analysis)
```

## Data Sources

| Source | Data | Cost |
|--------|------|------|
| [Finnhub](https://finnhub.io) | Company news, market news, earnings calendar | Free (60 calls/min) |
| [feargreedmeter.com](https://feargreedmeter.com) | Fear & Greed Index, S&P/Dow/NASDAQ, economic calendar | Free (scraped) |
| [Yahoo Finance](https://finance.yahoo.com) | VIX (volatility index) | Free |
| [Gemini API](https://aistudio.google.com) | AI-powered analysis and recommendations | Free tier |

## Setup

### Prerequisites

- GitHub account
- Telegram account
- Finnhub account (free)
- Google AI Studio account (free)

### 1. Create a Telegram Bot

1. Open Telegram and search for `@BotFather`
2. Send `/newbot` and follow the prompts
3. Save the **bot token** (format: `123456789:ABCdefGHI...`)
4. Send any message to your new bot
5. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
6. Find your **chat ID** in the response under `"chat":{"id": XXXXXXX}`

### 2. Get API Keys

**Finnhub:**
1. Sign up at https://finnhub.io (free)
2. Copy your API key from the dashboard

**Google Gemini:**
1. Go to https://aistudio.google.com/apikey
2. Sign in with your Google account
3. Create an API key

### 3. Configure Your Portfolio

Edit `config.json` with your holdings and watchlist:

```json
{
  "portfolio": {
    "AAPL": 15.0,
    "MSFT": 12.0,
    "NVDA": 10.0
  },
  "watchlist": [
    "AMZN", "GOOG", "META"
  ]
}
```

- **portfolio**: Tickers you hold with allocation percentages
- **watchlist**: Tickers you're watching but don't hold

### 4. Fork and Deploy

1. Fork this repository
2. Go to your fork's **Settings → Secrets and variables → Actions**
3. Add these repository secrets:

| Secret | Value |
|--------|-------|
| `GEMINI_API_KEY` | Your Google Gemini API key |
| `FINNHUB_API_KEY` | Your Finnhub API key |
| `TELEGRAM_BOT_TOKEN` | Your Telegram bot token |
| `TELEGRAM_CHAT_ID` | Your Telegram chat ID |

4. The bot will automatically run on the schedule. You can also trigger it manually from the **Actions** tab using "Run workflow".

### 5. Run Locally (Optional)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export GEMINI_API_KEY="your-key"
export FINNHUB_API_KEY="your-key"
export TELEGRAM_BOT_TOKEN="your-token"
export TELEGRAM_CHAT_ID="your-chat-id"

python analyzer.py pre-market   # or pre-close
```

## Upgrading to a Stronger AI Model

The default model is **Gemini 2.5 Flash Lite** (free). If you want better analysis quality, here are your options:

### Free Options

| Model | Provider | How to Switch | Quality | Limits |
|-------|----------|--------------|---------|--------|
| **Gemini 2.5 Flash** | Google | Change model name in `analyzer.py` | Better | 20 requests/day (may be region-limited) |
| **Llama 3.3 70B** | Groq | See Groq instructions below | Very good | 30 req/min, 131K tokens/min |
| **Llama 4 Scout** | Groq | See Groq instructions below | Excellent | Check groq.com for current limits |

### Paid Options (Estimated cost for 2 calls/day)

| Model | Provider | Monthly Cost | Quality |
|-------|----------|-------------|---------|
| **Gemini 2.5 Flash** | Google | ~$0.10-0.30 | Great |
| **Claude Haiku 4.5** | Anthropic | ~$0.50-1.00 | Great |
| **GPT-4o Mini** | OpenAI | ~$0.30-0.80 | Great |
| **Claude Sonnet 4.6** | Anthropic | ~$2-5 | Excellent |
| **GPT-4o** | OpenAI | ~$3-8 | Excellent |
| **Claude Opus 4.6** | Anthropic | ~$10-20 | Best |

### Switching to Groq (Free, Stronger)

1. Sign up at https://console.groq.com (free, no credit card)
2. Create an API key
3. Add `GROQ_API_KEY` to your GitHub secrets
4. In `analyzer.py`, replace the `analyze()` function:

```python
def analyze(prompt):
    api_key = os.environ["GROQ_API_KEY"]
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 2048,
    }
    for attempt in range(3):
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        if resp.status_code == 503 and attempt < 2:
            time.sleep(5)
            continue
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
```

5. Update the workflow to pass `GROQ_API_KEY` instead of `GEMINI_API_KEY`

### Switching to Claude (Paid)

1. Sign up at https://console.anthropic.com
2. Add credits (pay-as-you-go, minimum $5)
3. Create an API key and add as `ANTHROPIC_API_KEY` in GitHub secrets
4. Add `anthropic` to `requirements.txt`
5. In `analyzer.py`, replace the `analyze()` function:

```python
def analyze(prompt):
    import anthropic
    client = anthropic.Anthropic()
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",  # or claude-sonnet-4-6-20250514
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text
```

### Switching to OpenAI (Paid)

1. Sign up at https://platform.openai.com
2. Add credits and create an API key
3. Add as `OPENAI_API_KEY` in GitHub secrets
4. Add `openai` to `requirements.txt`
5. In `analyzer.py`, replace the `analyze()` function:

```python
def analyze(prompt):
    from openai import OpenAI
    client = OpenAI()
    response = client.chat.completions.create(
        model="gpt-4o-mini",  # or gpt-4o
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2048,
    )
    return response.choices[0].message.content
```

## Customization

### Change Schedule

Edit `.github/workflows/market-analysis.yml`. The cron times are in UTC:

```yaml
schedule:
  - cron: "30 12 * * 1-5"  # 8:30 AM ET (UTC-4) — pre-market
  - cron: "0 19 * * 1-5"   # 3:00 PM ET (UTC-4) — pre-close
```

Adjust for your timezone. Note: US Eastern Time is UTC-4 (EDT) or UTC-5 (EST).

### Change Analysis Style

Edit the prompt in `build_prompt()` in `analyzer.py`. You can adjust:
- Sections included in the briefing
- Tone and detail level
- Character limit (Telegram max is 4096)
- Focus areas (e.g., more emphasis on technical analysis, sectors, etc.)

## Cost Summary

| Component | Cost |
|-----------|------|
| GitHub Actions | Free (2000 min/month for private repos) |
| Finnhub API | Free |
| Fear & Greed data | Free |
| VIX data | Free |
| Telegram Bot | Free |
| Gemini Flash Lite | Free |
| **Total** | **$0/month** |

## License

MIT
