# Dashboard Usage Guide

How to get the most out of the Market Analyst Dashboard.

## Daily Workflow

### Morning (Before Market Opens)
1. Check the **Telegram briefing** — scan the Market Dashboard, Earnings Alert, and Recommendations sections
2. Open the **dashboard** — look at the main table sorted by **Action** (click the column header) to see what the algorithm recommends
3. Click tickers flagged as **Strong Buy** or **Strong Sell** to understand why
4. Check the **Fundamentals tab** for any ticker you're considering trading

### During the Day
- Sort the table by **Day** column to see what's moving
- Click movers to check if the move aligns with or contradicts the algorithm's recommendation

### Weekly
- Sort by **YTD** to see your best and worst performers this year
- Review the **Fundamentals tab** for your largest positions — check if margins, growth, and FCF are trending the right way
- Use the **Price Calculator** to stress-test your thesis on key holdings

## Understanding the Scores

### Reading the Main Table
- **Action column** shows the combined recommendation (Strong Buy / Buy / Hold / Sell / Strong Sell) with a colored score bar
- **Score bar color**: green (55+) = bullish, yellow (30-55) = neutral, red (<30) = bearish
- The score blends **Technical (40%) + Fundamental (60%)** — it favors long-term fundamentals over short-term price action

### When Technical and Fundamental Scores Disagree
This is where the interesting signals are:
- **High tech score + low fund score** — stock is in a technical uptrend but fundamentally expensive or deteriorating. Could be momentum that reverses. Be cautious adding.
- **Low tech score + high fund score** — fundamentally strong but technically weak (maybe in a pullback). This is often a buying opportunity for long-term holders.
- **Both high** — strong conviction buy signal
- **Both low** — strong conviction to reduce or avoid

## Using the Fundamentals Tab

### Valuation Section
- **P/E < 20** is generally cheap, **> 35** is expensive (but growth stocks can justify higher)
- **Forward P/E** matters more than trailing P/E — it reflects expected earnings
- **PEG < 1** means the stock is cheap relative to its growth rate (Peter Lynch's rule)
- **EV/EBITDA** is useful for comparing companies with different debt levels

### Profitability Section
- **Gross Margin > 50%** indicates strong pricing power (SaaS, luxury brands)
- **Operating Margin > 15%** means the business is operationally efficient
- **ROE > 15%** means management is deploying capital effectively
- Watch for **declining margins** over time — can signal competitive pressure

### Growth Section
- Compare **Revenue Growth** vs **Earnings Growth** — if earnings grow faster than revenue, margins are expanding (good)
- **Current Year EPS Estimate** and **Next Year** show what analysts expect — downward revisions are bearish
- **Quarterly EPS Growth** shows the most recent trend

### Financial Health Section
- **Debt/Equity < 100** is conservative, **> 200** is concerning (except for banks/financials)
- **Current Ratio > 1.5** means the company can cover short-term obligations
- **Positive Free Cash Flow** is essential — it's the cash the business actually generates after all expenses
- **Total Cash vs Total Debt** — net cash positive companies have a safety buffer

## Using the Price Calculator

### Which Model to Use

The calculator auto-detects the best model, but here's the logic:

| Company Type | Best Model | Example |
|---|---|---|
| Profitable, established | **EPS Growth** | AMZN, GOOG, MSFT, CRM |
| High-growth, pre-profit | **Revenue Growth** | NBIS, CIFR, early-stage SaaS |
| Cash-generating, mature | **DCF** | ORCL, UNH, MSFT |
| All three viable | Cross-check all models | AMZN, NVDA |

### EPS Growth Model Tips
- **Growth Rate**: Default comes from analyst estimates. Be skeptical of rates above 25% for more than 2-3 years — very few companies sustain that.
- **Target P/E**: This is what you think the market will pay per dollar of earnings in the future. Tech typically trades at 20-35x, industrials at 12-18x, utilities at 10-15x.
- **Conservative case**: Use 70% of the default growth rate and a lower P/E
- **Bull case**: Use the default growth rate with the current P/E maintained

### Revenue Growth Model Tips
- Best for companies where earnings don't exist yet or are volatile
- **Target P/S is the key input** — it represents what multiple the market will assign to the company's revenue
  - Current P/S is the default starting point
  - As companies mature, P/S typically compresses: a 50x P/S startup might trade at 8-12x as a mature company
  - SaaS companies: 8-15x P/S when mature. Hardware: 2-5x. Retail: 0.5-2x.
- If current P/S is very high (>30), try running with a lower target P/S (10-15) to see what the price would be if valuation normalizes

### DCF Model Tips
- Most theoretically sound model but sensitive to inputs
- **FCF Growth**: How fast will free cash flow grow? 10-15% is reasonable for established tech
- **Discount Rate**: 10% is standard. Use 12-15% for riskier companies, 8% for blue chips
- **Terminal Growth**: Always keep at 2-3% (roughly GDP growth). Going above 4% gives unrealistic results
- If the result seems too high, your FCF growth rate is probably too aggressive

### Cross-Checking Models
For your most important positions, run all available models:
- If all three give similar fair values — high confidence
- If they diverge significantly — dig into why. Usually means one assumption is off.
- The truth is usually between the most conservative and most aggressive estimate

## Signal Tags Cheat Sheet

| Signal | Meaning | Action |
|--------|---------|--------|
| **golden cross** | SMA50 crossed above SMA200 | Bullish trend starting |
| **death cross** | SMA50 crossed below SMA200 | Bearish trend starting |
| **overbought** | RSI > 70 | May be due for a pullback |
| **oversold** | RSI < 30 | May be a buying opportunity |
| **BB oversold** | Price at lower Bollinger Band | Oversold + high volatility |
| **BB overbought** | Price at upper Bollinger Band | Overbought + high volatility |
| **OBV bullish divergence** | Price falling but volume accumulating | Smart money buying |
| **OBV bearish divergence** | Price rising but volume declining | Smart money selling |
| **MACD bullish/bearish** | MACD crossover signal | Short-term momentum shift |
| **strong trend (ADX)** | ADX > 25 | Trend is strong, don't fade it |

## Portfolio Management Tips

### Concentration Risk
- The algorithm flags positions > 10% of portfolio — these deserve more scrutiny
- If a position has grown to 15%+ through appreciation, consider trimming to rebalance

### Earnings Season
- The dashboard shows upcoming earnings with badges
- Consider reducing position size before earnings if you have large allocations
- After earnings, check if the fundamental data updated (margins, growth rates)

### Using Watchlist
- Add stocks you're interested in but haven't bought yet
- The algorithm scores them the same way — when a watchlist stock hits "Strong Buy" with solid fundamentals, that's your entry signal
- The Telegram briefing highlights watchlist tickers with notable entry signals
