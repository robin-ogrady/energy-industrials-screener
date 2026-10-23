# Methodology

Energy & Industrials Supply Chain Screener. Data: `bloomberg_data_pull_wide_v12_values.xlsx`. **Not validated. A screening tool, not a trading signal.** A current snapshot: no historical scores, no backtest, no prediction.

## How a score is built
1. **Clean.** Bloomberg errors and `--` become missing. Missing dividend yield on a priced stock = 0 (flagged). P/E is never filled in. Multiples at or below zero are missing.
2. **Clip outliers** at the 5th and 95th percentile of all scored companies (winsorizing).
3. **Rank** each metric as a percentile (0 = worst, 1 = best) within the GICS industry; sector if fewer than 8 industry peers have the number; all companies as a last resort. Lower-is-better metrics are flipped.
4. **Group value** = average percentile of the metrics the company has in that group.
5. **Pass 1, standalone score:** weighted average of every group except Linked companies.
6. **Pass 2, Linked companies:** for the core names, the exposure-weighted average *standalone* score of their scored suppliers and customers. Weight = exposure % x cost-category weight (COGS and CAPEX 1.0, SGA 0.25, missing 1.0 but flagged). Using standalone scores avoids circular scores.
7. **Final score** = 1 + 9 x weighted average of the groups the company has (weights renormalized). 5.5 = neutral. Each group's contribution = 9 x weight x (group value - 0.5) points.
8. **Flags:** data completeness under 60% gets an asterisk; peer group caveats (all-company ranking, poor peer group, extreme ratios) get a badge.

## Factors, directions and weights

| Group (weight) | Metric | Better when |
|---|---|---|
| Health (20%) | Sales growth | higher |
|  | Gross margin | higher |
|  | Operating margin | higher |
|  | Return on equity | higher |
|  | Return on assets | higher |
|  | Free cash flow yield | higher |
| Risk (15%) | Net debt / EBITDA | lower |
|  | Debt / equity | lower |
|  | Current ratio | higher |
|  | Short interest (days to cover) | lower |
| Valuation (20%) | EV/EBITDA | lower |
|  | P/E | lower |
|  | Price / book | lower |
|  | EV/EBITDA vs own 5-yr average | lower |
|  | Dividend yield | higher |
| Demand pipeline (10%) | Inventory growth minus sales growth (1 yr) | lower |
|  | Inventory / sales | lower |
|  | Capex / sales **(off by default)** | higher |
| Expectations (15%) | EPS estimate revision (4 wk) | higher |
|  | EPS estimate revision (12 wk) | higher |
|  | Analyst rating change (12 wk) | higher |
|  | Analyst rating (1 sell to 5 buy) | higher |
|  | Target price upside | higher |
| Linked companies (10%) | Linked suppliers' avg score | linked companies score higher |
|  | Linked customers' avg score | linked companies score higher |
| Sentiment (5%) | News sentiment (30-day avg) | higher |
|  | News sentiment trend (7d minus 30d) | higher |
| Momentum (5%) | 1-month return | higher |
|  | 3-month return | higher |

## Notes
- **Capex intensity is OFF.** Capex / last 4 quarters of sales (higher = better) is off by default because its direction is debatable and the capex estimate field could not be used (unclear period). Turn it on in `config.py` (`use_capex_intensity`) or with the dashboard toggle.
- **Sentiment** uses only days with news (no news is not zero). Under 5 news days in 30 = left out. Headline scoring (Loughran-McDonald) is off by default and never enters the score unless switched on.
- **Supply chain** links are each core name's top 20 only and mostly Bloomberg estimates, and many linked companies are outside the universe, so Linked companies has limited coverage.
- **Commodity betas**, supplier dependence and news attention are shown on the dashboard but not scored.
- **Backtest:** A pre-registered backtest (S&P 1500 Energy and Industrials, 2016 to September 2026) did not support this score: its link to next-month returns was too weak to rule out luck (top minus bottom fifth after costs, p = 0.078). The supply chain part could not be tested. Rules, code and verdict: https://github.com/robin-ogrady/energy-industrials-backtest
- Scores are not stretched to fill 1 to 10, so they cluster near the middle. Gaps of 0.1 to 0.2 points between neighbors are noise: read the rank band and the drivers, not the decimals.
