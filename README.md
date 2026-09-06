# Market Analyst Bot

An automated stock market analysis system with a live dashboard and daily Telegram briefings. Combines technical indicators, fundamental analysis, and AI-powered insights to help long-term investors make informed decisions.

**Dashboard:** https://kaspitay.github.io/market-analyst-bot/

## What It Does

### Telegram Briefing — Monitor Mode (1x daily via GitHub Actions)
- **7:30 AM ET** — Pre-market run. This is the only scheduled run.

This is a silent-by-default **exception monitor**, not a twice-daily restated snapshot. It only
sends a real message when something actually changed since the last run: a recommendation bucket
flip, a veto gate firing or clearing, a new 52-week high/low, earnings entering the next few days,
a distribution (heavy selling volume) day, or thematic concentration drifting past its exposure
threshold. On a quiet day it sends one short "Nothing changed" line instead of restating the whole
portfolio. **Sunday mornings always send the full per-position briefing** regardless of what
changed, so you still see the whole book at least once a week.

The AI (Gemini) reviews the algorithm's pre-computed decisions and exceptions, adding news context and flagging disagreements.

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
- **150-day MA position + slope (70%)** — where price sits in its 52-week range, adjusted for whether the 150-day moving average is rising or falling
- **PPO-normalized MACD magnitude (30%)** — sign and strength of the 12/26 EMA spread

RSI, SMA50/SMA200, and 52-week high/low are still computed and shown on the dashboard, but are **display-only** — they are not part of the score.

### Fundamental Score (0-100)
Based on Yahoo Finance quoteSummary (financialData, defaultKeyStatistics, summaryDetail, earningsTrend) plus multi-year financial history:
- **Valuation (20%)** — P/E, P/S (both sector-relative), PEG, P/B
- **Profitability (25%)** — Gross/Operating margins, ROE
- **Growth (20%)** — Revenue growth, earnings growth, analyst estimates
- **Financial Health (35%)** — Piotroski-style 0-8 F-score (ROA, operating cash flow, ΔROA, accruals, leverage change, dilution, gross margin change, asset turnover change); renormalized across the other legs when health can't be measured

The analyst price target is **display-only** — it is not part of the score.

**Veto gates** hard-cap the score regardless of the weights above, since a strong ratio blend can't outrun a real red flag: **39** for cash burn (negative operating cash flow), **59** for leverage (net debt / operating cash flow > 4x) or margin erosion.

### Combined Score
**Technical (40%) + Fundamental (60%)** — biased toward long-term investing.

Recommendation thresholds: Strong Buy (72+), Buy (60+), Hold (40+), Sell (28+), Strong Sell (<28).

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

Edit `config.json`. Portfolio holdings are keyed by **share count**, not a static percentage —
live allocation % is derived every run from share count x current price (already fetched, no
extra API calls), so it's always accurate and never needs editing when prices move:

```json
{
  "portfolio": {
    "AAPL": {"shares": 10},
    "MSFT": {"shares": 5},
    "NVDA": {"shares": 20}
  },
  "watchlist": ["AMZN", "GOOG", "META"],
  "themes": {
    "ai-datacenter": ["NVDA", "AMD"],
    "megacap-platform": ["AMZN", "GOOG", "MSFT"]
  },
  "ocf_veto_exempt": ["NU", "SOFI"]
}
```

Two more keys `main()` reads that aren't obvious from the shape above:
- **`themes`** — hand-tagged groups of tickers (portfolio and watchlist together), used to track
  thematic concentration risk that a derived `sector` field can't (Yahoo's `sector` puts a bitcoin
  miner in "Financial Services" next to Mastercard). A theme's exposure is the sum of its members'
  live portfolio weight; the bot warns when any theme's exposure crosses 25%.
- **`ocf_veto_exempt`** — tickers exempt from the cash-burn veto gate because their negative
  operating cash flow is structural, not distress. Real example: `["NU", "SOFI"]` — both lenders
  whose operating cash flow runs negative from originating loans, not from burning cash.

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
  - cron: "30 11 * * *"  # 7:30 AM ET — the only scheduled run (monitor mode)
```

### Change Analysis Style

Edit `build_prompt()` in `analyzer.py`. The prompt controls the Telegram briefing structure, sections, and character limit.

## Cost

Everything runs on free tiers: GitHub Actions, Finnhub, Yahoo Finance, Telegram, Gemini Flash Lite. **$0/month.**

## License

MIT
