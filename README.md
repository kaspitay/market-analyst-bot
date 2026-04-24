# Market Analyst Bot

An automated stock market analysis system with a live dashboard and daily Telegram briefings. Combines technical indicators, fundamental analysis, and AI-powered insights to help long-term investors make informed decisions.

**Dashboard:** https://kaspitay.github.io/market-analyst-bot/

## What It Does

### Telegram Briefings (2x daily via GitHub Actions)
- **8:30 AM ET** — Pre-market briefing
- **3:00 PM ET** — Pre-close briefing

Each briefing includes market dashboard, earnings alerts, economic calendar, portfolio pulse with per-ticker news, algorithm-reviewed BUY/HOLD/SELL recommendations, watchlist radar, and market outlook.

The AI (Gemini) reviews the algorithm's pre-computed decisions and adds news context, earnings warnings, and flags disagreements.

### Live Dashboard (GitHub Pages)
- **Market Overview** — S&P 500, Dow, NASDAQ, VIX with Fear & Greed gauge
- **Portfolio & Watchlist Tables** — sortable, with RSI badges, score bars, signal tags
- **Expandable Detail Panels** with 3 tabs per ticker:
  - **Chart & Indicators** — 3-month price chart with volume, SMA lines, 52W range bar
  - **Fundamentals** — P/E, Forward P/E, PEG, margins, ROE, growth, D/E, FCF
  - **Price Calculator** — interactive 5-year projections with 3 models
- **Briefing History** — past Telegram briefings viewable in the dashboard

## Scoring System

### Technical Score (0-100)
Based on OHLCV data from Yahoo Finance:
- **Moving Averages (25%)** — SMA50, SMA200, EMA20 positions and crosses
- **Oscillators (40%)** — RSI, MACD, Stochastic, Bollinger Bands
- **Volume (20%)** — MFI, OBV divergence, volume confirmation
- **Trend Strength (15%)** — ADX

### Fundamental Score (0-100)
Based on Yahoo Finance quoteSummary (financialData, defaultKeyStatistics, summaryDetail, earningsTrend):
- **Valuation (25%)** — P/E, PEG, P/B
- **Profitability (20%)** — Gross/Operating margins, ROE
- **Growth (20%)** — Revenue growth, earnings growth, analyst estimates
- **Financial Health (15%)** — Debt/Equity, current ratio, FCF
- **Price Targets (20%)** — Analyst consensus upside/downside

### Combined Score
**Technical (40%) + Fundamental (60%)** — biased toward long-term investing.

Recommendation thresholds: Strong Buy (70+), Buy (55+), Hold (45+), Sell (30+), Strong Sell (<30).

## 5-Year Price Calculator

Three valuation models with interactive sliders:

| Model | Best For | Formula |
|-------|----------|---------|
| **EPS Growth** | Profitable companies (P/E < 200) | Future EPS = EPS x (1+growth)^years x Target P/E |
| **Revenue Growth** | Pre-profit / high-growth | Future Rev/Share x Target P/S |
| **DCF** | Cash-generating businesses | PV of projected FCF + terminal value |

The calculator auto-classifies each company (Hyper-Growth, Profitable, Value, Early Stage) and recommends the best model with guidance.

## Architecture

```
GitHub Actions (cron) --> analyzer.py --> Telegram Bot API
                              |
                    +---------+---------+
                    v         v         v
               Finnhub    feargreed   Yahoo Finance
              (news)      (F&G/indices) (OHLCV/fundamentals)
                    |
                    v
                Gemini AI (reviews algorithm output)
                    |
                    v
              docs/data/market-data.json --> GitHub Pages Dashboard
```

## Data Sources

| Source | Data | Cost |
|--------|------|------|
| [Yahoo Finance](https://finance.yahoo.com) | OHLCV, technicals, fundamentals (P/E, margins, FCF, etc.), analyst price targets, VIX | Free |
| [Finnhub](https://finnhub.io) | Company news, market news, earnings calendar | Free (60 calls/min) |
| [feargreedmeter.com](https://feargreedmeter.com) | Fear & Greed Index, S&P/Dow/NASDAQ, economic calendar | Free (scraped) |
| [Gemini API](https://aistudio.google.com) | AI-powered briefing generation | Free tier (Flash Lite) |

## Setup

### Prerequisites
- GitHub account
- Telegram account
- Finnhub account (free)
- Google AI Studio account (free)

### 1. Create a Telegram Bot

1. Open Telegram and search for `@BotFather`
2. Send `/newbot` and follow the prompts
3. Save the **bot token**
4. Send any message to your new bot
5. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
6. Find your **chat ID** in the response

### 2. Get API Keys

- **Finnhub:** Sign up at https://finnhub.io (free), copy API key
- **Google Gemini:** Go to https://aistudio.google.com/apikey, create key

### 3. Configure Your Portfolio

Edit `config.json`:

```json
{
  "portfolio": {
    "AAPL": 15.0,
    "MSFT": 12.0,
    "NVDA": 10.0
  },
  "watchlist": ["AMZN", "GOOG", "META"]
}
```

### 4. Fork and Deploy

1. Fork this repository
2. Go to **Settings > Secrets and variables > Actions**
3. Add secrets: `GEMINI_API_KEY`, `FINNHUB_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
4. Enable GitHub Pages (Settings > Pages > Source: Deploy from branch, Branch: main, Folder: /docs)
5. The bot runs automatically on schedule, or trigger manually from the Actions tab

### 5. Run Locally (Optional)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export GEMINI_API_KEY="your-key"
export FINNHUB_API_KEY="your-key"
export TELEGRAM_BOT_TOKEN="your-token"
export TELEGRAM_CHAT_ID="your-chat-id"

python analyzer.py pre-market
```

## Upgrading the AI Model

Default is **Gemini 2.5 Flash Lite** (free). Options:

| Model | Provider | Cost | How |
|-------|----------|------|-----|
| Gemini 2.5 Flash | Google | Free (limited) | Change model name in `analyzer.py` |
| Llama 3.3 70B | Groq | Free | See below |
| Claude Haiku 4.5 | Anthropic | ~$0.50/mo | Replace `analyze()` function |
| GPT-4o Mini | OpenAI | ~$0.30/mo | Replace `analyze()` function |

### Switching to Groq (Free)

1. Sign up at https://console.groq.com
2. Add `GROQ_API_KEY` to GitHub secrets
3. Replace the `analyze()` function in `analyzer.py`:

```python
def analyze(prompt):
    api_key = os.environ["GROQ_API_KEY"]
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "max_tokens": 2048}
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]
```

## Customization

### Change Schedule

Edit `.github/workflows/market-analysis.yml`:
```yaml
schedule:
  - cron: "30 12 * * 1-5"  # 8:30 AM ET — pre-market
  - cron: "0 19 * * 1-5"   # 3:00 PM ET — pre-close
```

### Change Analysis Style

Edit `build_prompt()` in `analyzer.py`. The prompt controls the Telegram briefing structure, sections, and character limit.

## Cost

Everything runs on free tiers: GitHub Actions, Finnhub, Yahoo Finance, Telegram, Gemini Flash Lite. **$0/month.**

## License

MIT
