# 5-Year Price Calculator Specification

## Overview
Interactive stock valuation calculator with three models, embedded in the dashboard detail panel. Users adjust sliders to see projected fair value vs current price.

## Models

### Model 1: Earnings Growth
**Inputs:**
| Parameter | Default | Range | Source |
|-----------|---------|-------|--------|
| Current EPS | trailingEps | — | defaultKeyStatistics |
| EPS Growth Rate | currentYearGrowth or 10% | -20% to 50% | slider |
| Target P/E | forwardPE or 15 | 5 to 60 | slider |
| Years | 5 | 1 to 10 | slider |

**Formula:**
```
Future EPS = Current EPS × (1 + growth%)^years
Fair Value = Future EPS × Target P/E
```

**Edge Cases:**
- Negative or zero EPS → disable model, show "Not applicable (negative earnings)"
- No earningsTrend → default growth to 10%
- No forwardPE → use trailingPE, then default 15

### Model 2: Revenue Growth
**Inputs:**
| Parameter | Default | Range | Source |
|-----------|---------|-------|--------|
| Revenue/Share | totalRevenue / sharesOutstanding | — | financialData |
| Revenue Growth | revenueGrowth or 10% | -20% to 50% | slider |
| Target P/S | priceToSales or 3 | 0.5 to 20 | slider |
| Years | 5 | 1 to 10 | slider |

**Formula:**
```
Future Rev/Share = Current Rev/Share × (1 + growth%)^years
Fair Value = Future Rev/Share × Target P/S
```

**Edge Cases:**
- No revenue data → disable model
- Very high P/S (>50) → cap at 50

### Model 3: DCF (Simplified)
**Inputs:**
| Parameter | Default | Range | Source |
|-----------|---------|-------|--------|
| Free Cash Flow | freeCashflow | — | financialData |
| FCF Growth | 10% | -10% to 40% | slider |
| Discount Rate | 10% | 5% to 15% | slider |
| Terminal Growth | 2.5% | 1% to 4% | slider |

**Formula:**
```
For years 1-5:
  Projected FCF(y) = FCF × (1 + fcfGrowth)^y
  PV(y) = Projected FCF(y) / (1 + discountRate)^y

Terminal Value = FCF(5) × (1 + terminalGrowth) / (discountRate - terminalGrowth)
PV Terminal = Terminal Value / (1 + discountRate)^5

Equity Value = Sum(PV 1-5) + PV Terminal + totalCash - totalDebt
Fair Value = Equity Value / sharesOutstanding
```

**Edge Cases:**
- No FCF data → disable model
- Negative FCF → show warning, allow but flag
- Discount rate ≤ terminal growth → show error "Discount rate must exceed terminal growth"
- Very high projections → cap display at +500%

## Output Format
For each model:
- **Projected fair value** ($) in large text
- **Upside/downside** (%) vs current price, green if positive, red if negative
- **Year-by-year bar chart** showing projected price/value progression
- **Current price line** overlaid on chart for reference

## UI Layout
- Three sub-tabs within the calculator tab (EPS Growth | Revenue | DCF)
- Each tab has 3-4 sliders with live value display
- Result card below sliders with fair value + chart
- All calculations happen client-side in real-time (no API calls)
