# Workbook format

The screener reads one Excel workbook: a **values-only** copy of a workbook
filled on a Bloomberg terminal. This page explains how to build it. The
easiest way to see the layout is to generate the invented sample and open it:

```zsh
.venv/bin/python make_sample_data.py      # writes sample/sample_workbook.xlsx
```

Your workbook must use the same sheet names, headers and positions. Sheet and
column names are case-sensitive.

## General rules

- **Values only.** Build the workbook with Bloomberg Excel formulas (BDP, BDH,
  BDS), then save a copy and replace every formula with its value (select all,
  Copy, Paste Special > Values). Bloomberg formulas turn into errors on a
  machine without a terminal.
- **Errors are fine.** Any text starting with `#` (`#N/A N/A`,
  `#N/A Invalid Field`, `#N/A Field Not Applicable`, `#N/A Invalid Security`) and
  `--` are read as missing. Nothing is filled in with made-up numbers.
- **Tickers** use Bloomberg format, e.g. `XOM US Equity`.
- **Percent fields** use Bloomberg's percent units (3.97 means 3.97 percent),
  except where noted.
- Fill in all sheets, then run `.venv/bin/python loader.py`. It prints a data
  summary. Every row marked `CHECK` points at a layout problem.

## Table sheets (header in row 1, data from row 2)

| Sheet | Columns | Notes |
|---|---|---|
| `Universe` | `ticker`, `chain`, `role`, `notes`, `raw_ticker`, `demo_set`, `dup_check` | One row per company. `demo_set = Y` marks the **core names**: put them first. Core names are the only ones with supply chain data; `chain` is an optional label for them. Empty rows after the list are fine. |
| `Snapshot` | `ticker`, `asof`, then Bloomberg fields (below) | One row per Universe ticker. `=BDP($A2, "FIELD")` in each cell. |
| `Settings` | `setting`, `value`, `note` | Pull dates, for your own records. |
| `News` | `ticker`, `date`, `headline`, `manual_score` | Optional. Your own headlines with a score of -1, 0 or +1. Rows containing `EXAMPLE` are ignored. |
| `Links` | anything | Not used (supply chain data comes from the SC_ tabs). Rows containing `EXAMPLE` are ignored. |
| `Data_Dictionary` | `group`, `column_name`, `sheet`, `update_frequency`, `unit`, `what_it_is`, `why_it_matters`, `how_to_read`, `field_status` | Shown on the Data quality page. `field_status` is free text (e.g. "verified", "unverified"). |

**Snapshot fields** (Bloomberg mnemonics as column headers):
`NAME`, `GICS_SECTOR_NAME`, `GICS_INDUSTRY_NAME`, `CUR_MKT_CAP`, `PX_LAST`,
`PX_VOLUME`, `CHG_PCT_YTD`, `CHG_PCT_1M`, `SALES_GROWTH`, `GROSS_MARGIN`,
`OPER_MARGIN`, `TOT_DEBT_TO_COM_EQY`, `NET_DEBT_TO_EBITDA`, `RETURN_COM_EQY`,
`RETURN_ON_ASSET`, `CUR_RATIO`, `CF_FREE_CASH_FLOW`, `FREE_CASH_FLOW_YIELD`,
`PE_RATIO`, `PX_TO_BOOK_RATIO`, `EV_TO_T12M_EBITDA`, `EQY_DVD_YLD_IND`,
`BS_INVENTORIES`, `CAPITAL_EXPEND`, `BEST_CAPEX`, `EQY_REC_CONS`,
`BEST_TARGET_PRICE`, `SHORT_INT_RATIO`, `EQY_INST_PCT_SH_OUT`,
`NEWS_SENTIMENT_DAILY_AVG`, `BEST_EPS_FY1`, `BEST_EPS_FY2`.

Extra columns are allowed. A field that comes back 100 percent errors is
dropped automatically (in our pull, `INVENTORY_TURNOVER`, `BACKLOG` and
`NEWS_HEAT_PUB_AVG` did). A ticker without `PX_LAST` is treated as delisted and
not scored. `BEST_EPS_FY1` / `BEST_EPS_FY2` need the fiscal period override
(`BEST_FPERIOD_OVERRIDE=1FY` / `2FY`). Check any field that errors with `FLDS <GO>`.

## Time series sheets (one column group per company)

Each company gets a group of columns side by side, **in Universe order**
(group 1 = first Universe ticker, group 2 = second, ...):

- row 1: the ticker, in the group's first column
- row 2: headers, `date` then the field names
- row 3 down: the data, each group with its own dates

Group *i* (counting from 0) starts at column `1 + i × width`. Groups after the
last company may be empty.

| Sheet | Width | Columns per group | What to pull |
|---|---|---|---|
| `Prices_D` | 2 | `date`, `PX_LAST` | 1 year of daily prices |
| `Fundamentals_Q` | 5 | `date`, `BS_INVENTORIES`, `SALES_REV_TURN`, `GROSS_MARGIN`, `EV_TO_T12M_EBITDA` | about 5 years of quarterly values (needs at least 8 quarters) |
| `Estimates_W` | 3 | `date`, `BEST_EPS`, `EQY_REC_CONS` | 1 year weekly; `BEST_EPS` with `BEST_FPERIOD_OVERRIDE=1FY` |
| `Sentiment_D` | 2 | `date`, `NEWS_SENTIMENT_DAILY_AVG` | about 90 days; days without news simply have no row |
| `Commodities_D` | 2 | `date`, `PX_LAST` | 1 year daily for `CL1 Comdty`, `CO1 Comdty`, `NG1 Comdty`, `HG1 Comdty`, `HRC1 Comdty` (one group each; row 1 holds the commodity ticker) |

A typical history formula sits in row 2 of the group and spills down, e.g.
`=BDH(A$1, "PX_LAST", Settings!$B$3, Settings!$B$2, "Dir=V", "Dts=S")`. Use
the Bloomberg Excel add-in's function builder to get the options right for
your terminal (periodicity, e.g. weekly or quarterly, is set there). Group
widths can be changed in `config.py` (`group_width`) if you add fields.

## Supply chain sheets

Three sheets with the same block layout: `SC_Suppliers` (top 20 suppliers by
relationship value), `SC_Suppliers_Rev` (top 20 by the supplier's revenue
share) and `SC_Customers` (top 20 customers). There is **one block per core
name, in Universe order**, each 23 rows tall:

| Block row | Content |
|---|---|
| 1 | the core company's ticker in column A |
| 2 | headers (below) |
| 3 to 22 | up to 20 related companies, one per row (fewer is fine, leave the rest empty) |
| 23 | blank |

Block *i* (counting from 0) starts at row `1 + 23 × i`. Columns are read **by
header name**, so their order can differ between sheets:

- `SC_Suppliers` and `SC_Suppliers_Rev`: `Ticker`, `Company Name`,
  `Cost Category` (SGA, COGS or CAPEX), `%Cost Category`, `%Supplier Rev`,
  `Relationship Value (Q) (Mln) (USD)`, `Relationship Year`,
  `Relationship Period`, `Source`, `As Of Date`
- `SC_Customers`: `Ticker`, `Company Name`, `%Company Rev`, `Cost Category`,
  `%Customer Cost Category`, `Relationship Value (Q) (Mln) (USD)`,
  `Relationship Year`, `Relationship Period`, `Source`, `As Of Date`

These are the column names of the Bloomberg supply chain (`SPLC <GO>`) export,
so you can paste the export's values into each block. Alternatively use one BDS
formula per block, e.g. `SUPPLY_CHAIN_SUPPLIERS` with the overrides
`SUPPLY_CHAIN_SUM_COUNT_OVERRIDE=20`, `QUANTIFIED_OVERRIDE=Y` and
`SUP_CHAIN_RELATIONSHIP_SORT_OVR=C` (by value) or `=R` (by the supplier's
revenue share), or `SUPPLY_CHAIN_CUSTOMERS` with the first two.

Percent columns are in percent units. `As Of Date` is text like `6/5/2026`.
`Source` starting with "Estimate" counts as an estimate; anything else as
reported. A core name with no quantified customers is fine (Bloomberg returns
an error row; it is read as "no customer links").

A related company counts as **in the universe** when its ticker exactly matches
a Snapshot ticker, or, failing that, when its name matches closely (rapidfuzz
score 95 or higher). Every name match and near miss is listed in
`cache/link_match_review.csv` and on the Data quality page.

## Pointing the tool at your file

Put the workbook in the project folder and set `DATA_FILE` in `config.py`, or
leave `config.py` alone and set an environment variable:

```zsh
SCREENER_DATA_FILE=my_pull_values.xlsx .venv/bin/python loader.py
SCREENER_DATA_FILE=my_pull_values.xlsx .venv/bin/streamlit run app.py
```

The first run parses the workbook (a few seconds) and caches the result in
`cache/`. The cache rebuilds automatically when the file changes.
