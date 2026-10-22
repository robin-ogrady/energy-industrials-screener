"""
features.py: turn the clean data into one row of numbers per company.

Everything here is computed only from data that exists. If an input is
missing, the feature is NaN (missing), never a guess.

Main function: build_features(d) -> (feats, extras)
    feats   one row per scored company: identity columns, every metric used
            in scoring, and helper columns (coverage, flags)
    extras  dict of side tables for the dashboard: commodity betas,
            link dependence, headline scores
"""
import io
import re

import numpy as np
import pandas as pd

import config

FCFG = config.CONFIG["features"]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def series_for(long_df, field):
    """Pivot one field of a long table to a dict {ticker: Series by date}."""
    sub = long_df[long_df["field"] == field].sort_values("date")
    return {t: g.set_index("date")["value"] for t, g in sub.groupby("ticker")}


def value_asof(s, when, tolerance_days):
    """Last value on or before `when`, if it is at most tolerance_days old."""
    s = s[s.index <= when]
    if s.empty or (when - s.index[-1]).days > tolerance_days:
        return np.nan
    return s.iloc[-1]


def safe_pct_change(new, old):
    """(new - old) / |old|. Dividing by |old| keeps the sign right when old
    is negative (a loss shrinking counts as an improvement). NaN if old is 0."""
    if pd.isna(new) or pd.isna(old) or old == 0:
        return np.nan
    return (new - old) / abs(old)


# ---------------------------------------------------------------------------
# 1. Valuation vs own history
# ---------------------------------------------------------------------------
def valuation_vs_history(snapshot, fundamentals):
    """Current EV/EBITDA divided by the company's own quarterly average, minus 1.

    Negative = cheaper than usual. Only positive multiples are used (a
    negative EV/EBITDA means negative EBITDA and is not a valuation).
    """
    hist = fundamentals[(fundamentals["field"] == "EV_TO_T12M_EBITDA") & (fundamentals["value"] > 0)]
    stats = hist.groupby("ticker")["value"].agg(["mean", "count"])
    cur = snapshot.set_index("ticker")["EV_TO_T12M_EBITDA"]
    cur = cur.where(cur > 0)
    ok = stats["count"] >= FCFG["valuation_history_min_quarters"]
    avg = stats["mean"].where(ok).reindex(cur.index)
    return pd.DataFrame({"ev_hist_avg": avg, "ev_vs_history": cur / avg - 1})


# ---------------------------------------------------------------------------
# 2. Inventory vs sales, capex intensity
# ---------------------------------------------------------------------------
def inventory_features(snapshot, fundamentals):
    """Inventory growth minus sales growth over 4 quarters, inventory / sales,
    and capex / sales.

    - Uses the latest quarter that has both inventory and sales, and the
      quarter 4 rows earlier (it must be roughly a year earlier).
    - Companies with zero inventory (producers, utilities, railroads) get
      NaN for the inventory measures: the idea does not apply to them.
    - Sales for the ratios = sum of the last 4 quarters (trailing 12 months).
    """
    lag = FCFG["inventory_lookback_quarters"]
    wide = fundamentals[fundamentals["field"].isin(["BS_INVENTORIES", "SALES_REV_TURN"])] \
        .pivot_table(index=["ticker", "date"], columns="field", values="value")
    capex = snapshot.set_index("ticker")["CAPITAL_EXPEND"].abs()  # Bloomberg shows capex as a negative cash flow
    rows = {}
    for t, g in wide.groupby(level="ticker"):
        g = g.droplevel("ticker").sort_index()
        r = {}
        sales = g.get("SALES_REV_TURN", pd.Series(dtype=float)).dropna()
        if len(sales) >= 4 and sales.iloc[-4:].sum() > 0:
            ttm = sales.iloc[-4:].sum()
            r["sales_ttm"] = ttm
            r["capex_intensity"] = capex.get(t, np.nan) / ttm
        both = g.dropna(subset=[c for c in ["BS_INVENTORIES", "SALES_REV_TURN"] if c in g.columns])
        if len(both) > lag and "BS_INVENTORIES" in both.columns:
            now, then = both.iloc[-1], both.iloc[-1 - lag]
            gap_days = (both.index[-1] - both.index[-1 - lag]).days
            r["inv_quarter"] = both.index[-1]
            if now["BS_INVENTORIES"] > 0 and then["BS_INVENTORIES"] > 0 and then["SALES_REV_TURN"] > 0 and 300 <= gap_days <= 430:
                r["inv_growth"] = now["BS_INVENTORIES"] / then["BS_INVENTORIES"] - 1
                r["sales_growth_q"] = now["SALES_REV_TURN"] / then["SALES_REV_TURN"] - 1
                r["inv_minus_sales_growth"] = r["inv_growth"] - r["sales_growth_q"]
            if now["BS_INVENTORIES"] > 0 and r.get("sales_ttm", 0) > 0:
                r["inv_to_sales"] = now["BS_INVENTORIES"] / r["sales_ttm"]
        rows[t] = r
    return pd.DataFrame.from_dict(rows, orient="index")


# ---------------------------------------------------------------------------
# 3. Estimate revisions and analyst rating change
# ---------------------------------------------------------------------------
def estimate_features(estimates, asof):
    """Percent change in FY1 EPS estimate over 4 and 12 weeks, and the change
    in the consensus rating (1 sell to 5 buy) over 12 weeks.

    The anchor is each company's latest weekly value (must be recent). The
    older value is the last one on or before anchor minus N weeks.
    """
    stale = FCFG["max_stale_days"]
    eps = series_for(estimates, "BEST_EPS")
    rec = series_for(estimates, "EQY_REC_CONS")
    rows = {}
    for t in set(eps) | set(rec):
        r = {}
        s = eps.get(t)
        if s is not None and len(s):
            last_date = s.index[-1]
            if (asof - last_date).days <= stale:
                for w in FCFG["eps_revision_weeks"]:
                    old = value_asof(s, last_date - pd.Timedelta(weeks=w), 10)
                    r["eps_rev_%dw" % w] = safe_pct_change(s.iloc[-1], old)
        s = rec.get(t)
        if s is not None and len(s) and (asof - s.index[-1]).days <= stale:
            w = FCFG["rec_change_weeks"]
            old = value_asof(s, s.index[-1] - pd.Timedelta(weeks=w), 10)
            r["rec_change_%dw" % w] = s.iloc[-1] - old
        rows[t] = r
    return pd.DataFrame.from_dict(rows, orient="index")


# ---------------------------------------------------------------------------
# 4. Momentum
# ---------------------------------------------------------------------------
def momentum_features(prices, asof):
    """1 and 3 month price returns (21 and 63 trading days) from Prices_D."""
    rows = {}
    for t, s in series_for(prices, "PX_LAST").items():
        s = s[s > 0]
        if s.empty or (asof - s.index[-1]).days > FCFG["max_stale_days"]:
            continue
        rows[t] = {k: (s.iloc[-1] / s.iloc[-1 - n] - 1) if len(s) > n else np.nan
                   for k, n in FCFG["momentum_days"].items()}
    return pd.DataFrame.from_dict(rows, orient="index")


# ---------------------------------------------------------------------------
# 5. Commodity sensitivity
# ---------------------------------------------------------------------------
def commodity_betas(prices, commodities):
    """Beta and correlation of each stock's daily returns to each commodity.

    Prices are merged on date FIRST (only days where both traded), then
    returns are computed, so both returns cover the same period. One simple
    regression per commodity (WTI and Brent move together, so a joint
    regression would split their effect arbitrarily).

    Returns a long table: ticker, commodity, beta, corr, n_obs.
    "Strongest" exposure = the commodity with the largest |correlation|,
    because betas to different commodities are not on the same scale.
    """
    comm = commodities.pivot_table(index="date", columns="ticker", values="value").sort_index()
    out = []
    for t, s in series_for(prices, "PX_LAST").items():
        for c in comm.columns:
            # Days where both the stock and this commodity have a price.
            both = pd.concat([s.rename("stock"), comm[c]], axis=1, join="inner").dropna()
            both = both[(both > 0).all(axis=1)].tail(FCFG["beta_window_days"] + 1)
            pair = both.pct_change(fill_method=None).iloc[1:].dropna()
            row = {"ticker": t, "commodity": c, "n_obs": len(pair), "beta": np.nan, "corr": np.nan}
            if len(pair) >= FCFG["beta_min_obs"] and pair[c].var() > 0:
                row["beta"] = pair["stock"].cov(pair[c]) / pair[c].var()
                row["corr"] = pair["stock"].corr(pair[c])
            out.append(row)
    betas = pd.DataFrame(out)
    if not betas.empty:
        betas["commodity_name"] = betas["commodity"].map(FCFG["commodities"]).fillna(betas["commodity"])
    return betas


def strongest_commodity(betas):
    """One row per ticker: the commodity with the largest |correlation|."""
    b = betas.dropna(subset=["corr"])
    if b.empty:
        return pd.DataFrame(columns=["top_commodity", "top_commodity_beta", "top_commodity_corr"])
    idx = b["corr"].abs().groupby(b["ticker"]).idxmax()
    top = b.loc[idx].set_index("ticker")
    return pd.DataFrame({"top_commodity": top["commodity_name"], "top_commodity_beta": top["beta"],
                         "top_commodity_corr": top["corr"]})


# ---------------------------------------------------------------------------
# 6. News sentiment
# ---------------------------------------------------------------------------
def sentiment_features(sentiment, asof, snapshot=None):
    """30-day average sentiment, trend (7-day avg minus 30-day avg), coverage.

    Days with no value are "no news" and simply not counted (never zero).
    If coverage is under the minimum, sentiment is marked low coverage and
    the scored columns (sent_30d, sent_trend) are left missing.
    Attention: a Snapshot news heat / story count column if one exists and
    has data, otherwise coverage is the attention proxy.
    """
    long_d, short_d = FCFG["sentiment_long_days"], FCFG["sentiment_short_days"]
    s = sentiment[sentiment["field"] == "NEWS_SENTIMENT_DAILY_AVG"]
    win = s[(s["date"] > asof - pd.Timedelta(days=long_d)) & (s["date"] <= asof)]
    short = win[win["date"] > asof - pd.Timedelta(days=short_d)]
    out = pd.DataFrame({
        "sent_30d_raw": win.groupby("ticker")["value"].mean(),
        "sent_7d_raw": short.groupby("ticker")["value"].mean(),
        "sent_coverage": win.groupby("ticker")["value"].count(),
    })
    out["sent_trend_raw"] = out["sent_7d_raw"] - out["sent_30d_raw"]
    out["sent_low_coverage"] = out["sent_coverage"].fillna(0) < FCFG["sentiment_min_coverage"]
    out["sent_30d"] = out["sent_30d_raw"].where(~out["sent_low_coverage"])
    out["sent_trend"] = out["sent_trend_raw"].where(~out["sent_low_coverage"])

    # Attention measure.
    heat_cols = []
    if snapshot is not None:
        heat_cols = [c for c in snapshot.columns if "NEWS" in c.upper()
                     and any(k in c.upper() for k in ("HEAT", "STORY", "COUNT"))
                     and snapshot[c].notna().any()]
    if heat_cols:
        out["attention"] = snapshot.set_index("ticker")[heat_cols[0]].reindex(out.index)
        out["attention_source"] = heat_cols[0]
    else:
        out["attention"] = out["sent_coverage"]
        out["attention_source"] = "news days in last %d (coverage)" % long_d
    return out


# ---------------------------------------------------------------------------
# 7. Optional headline scoring (Loughran-McDonald), OFF by default
# ---------------------------------------------------------------------------
def load_lm_wordlist():
    """Return (positive_words, negative_words) or None if unavailable.

    The list is downloaded once into the gitignored cache folder. Any
    failure (no internet, moved file) returns None and the feature is skipped.
    """
    path = config.CACHE_DIR / "lm_master_dictionary.csv"
    if not path.exists():
        import urllib.request
        for url in FCFG["lm_urls"]:
            try:
                with urllib.request.urlopen(url, timeout=20) as r:
                    path.write_bytes(r.read())
                break
            except Exception:
                continue
    if not path.exists():
        return None
    try:
        lm = pd.read_csv(path)
        cols = {c.lower(): c for c in lm.columns}
        word = lm[cols["word"]].astype(str).str.upper()
        pos = set(word[lm[cols["positive"]] > 0])
        neg = set(word[lm[cols["negative"]] > 0])
        return pos, neg
    except Exception:
        return None


def headline_scores(news, enabled=None):
    """Score each headline as (positive words - negative words) / words found.

    Returns (per_headline_df, per_ticker_df, message). Does nothing unless
    switched on (headline_lm_enabled in config.py or the dashboard toggle).
    """
    empty = pd.DataFrame()
    enabled = FCFG["headline_lm_enabled"] if enabled is None else enabled
    if not enabled:
        return empty, empty, "Headline scoring is off."
    if news is None or news.empty or "headline" not in news.columns:
        return empty, empty, "Headlines not loaded."
    words = load_lm_wordlist()
    if words is None:
        return empty, empty, "Loughran-McDonald word list could not be downloaded; skipped."
    pos, neg = words
    h = news.dropna(subset=["headline"]).copy()

    def score(text):
        toks = re.findall(r"[A-Z]+", str(text).upper())
        p, n = sum(t in pos for t in toks), sum(t in neg for t in toks)
        return (p - n) / (p + n) if (p + n) else 0.0

    h["lm_score"] = h["headline"].apply(score)
    per_t = h.groupby("ticker").agg(lm_score=("lm_score", "mean"), manual_score=("manual_score", "mean"),
                                    n_headlines=("headline", "count"))
    return h, per_t, "ok"


# ---------------------------------------------------------------------------
# 8. Supply chain dependence (shown on the dashboard, not scored)
# ---------------------------------------------------------------------------
def dependence_features(links):
    """Per main company:
    - supplier_dep_max: the largest share of a supplier's revenue that comes
      from this company (e.g. 0.17 = one supplier gets 17 percent of its
      revenue from us), and who that supplier is.
    - supplier_dep_wavg: exposure-weighted average of that share.
    - customer_concentration: share of revenue from the listed (top 20)
      customers, a lower bound since only the top 20 are listed.
    - capex_driven_share: part of that customer revenue tagged CAPEX.
    """
    if links is None or links.empty:
        return pd.DataFrame()
    rows = {}
    for t, g in links.groupby("main_ticker"):
        r = {}
        sup = g[g["direction"] == "Supplier"].dropna(subset=["reverse_pct"])
        if len(sup):
            i = sup["reverse_pct"].idxmax()
            r["supplier_dep_max"] = sup.loc[i, "reverse_pct"]
            r["supplier_dep_max_name"] = sup.loc[i, "related_name"]
            w = sup["exposure_pct"].fillna(0)
            r["supplier_dep_wavg"] = (sup["reverse_pct"] * w).sum() / w.sum() if w.sum() > 0 else np.nan
        cus = g[g["direction"] == "Customer"].dropna(subset=["exposure_pct"])
        if len(cus):
            r["customer_concentration"] = cus["exposure_pct"].sum()
            r["capex_driven_share"] = cus.loc[cus["cost_type"] == "CAPEX", "exposure_pct"].sum() / cus["exposure_pct"].sum()
        r["n_supplier_links"] = int((g["direction"] == "Supplier").sum())
        r["n_customer_links"] = int((g["direction"] == "Customer").sum())
        r["pct_links_estimated"] = float((g["source_type"] == "Estimate").mean())
        rows[t] = r
    return pd.DataFrame.from_dict(rows, orient="index")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def sources_disagree(*values):
    """True when two of the sentiment sources point in opposite directions
    (one clearly positive, another clearly negative). Missing values ignored."""
    signs = {np.sign(v) for v in values if pd.notna(v) and abs(v) > 0.05}
    return signs == {-1.0, 1.0}


def build_features(d, options=None):
    """Build the feature table for every company that can be scored.

    options: {"headline_lm_enabled": bool} to override config.py (dashboard).
    """
    options = options or {}
    snap = d["snapshot"]
    asof = snap["asof"].max()
    scored = snap[~snap["excluded"]].copy()

    feats = scored.set_index("ticker")
    uni = d["universe"].set_index("ticker")
    feats["chain"] = uni["chain"].reindex(feats.index)
    feats["is_core"] = uni["is_core"].reindex(feats.index).fillna(False).astype(bool)
    feats = feats.rename(columns={"GICS_SECTOR_NAME": "sector", "GICS_INDUSTRY_NAME": "industry"})

    # Multiples at or below zero are not meaningful valuations.
    for c in ["EV_TO_T12M_EBITDA", "PE_RATIO", "PX_TO_BOOK_RATIO"]:
        feats[c] = feats[c].where(feats[c] > 0)
    # Negative net debt / EBITDA is good (net cash) only if EBITDA is
    # positive. We know EBITDA is positive when EV/EBITDA is positive;
    # otherwise the sign is ambiguous and the value is left missing.
    amb = (feats["NET_DEBT_TO_EBITDA"] < 0) & feats["EV_TO_T12M_EBITDA"].isna()
    feats["nd_ebitda_ambiguous"] = amb
    feats.loc[amb, "NET_DEBT_TO_EBITDA"] = np.nan

    feats["target_upside"] = feats["BEST_TARGET_PRICE"] / feats["PX_LAST"] - 1

    parts = [
        valuation_vs_history(scored, d["fundamentals"]),
        inventory_features(scored, d["fundamentals"]),
        estimate_features(d["estimates"], asof),
        momentum_features(d["prices"], asof),
        sentiment_features(d["sentiment"], asof, snap),
    ]
    betas = commodity_betas(d["prices"], d["commodities"])
    parts.append(strongest_commodity(betas))
    dep = dependence_features(d["links"])
    parts.append(dep)
    for p in parts:
        if p is not None and not p.empty:
            feats = feats.join(p.reindex(feats.index), how="left", rsuffix="_dup")

    # Companies with no sentiment rows at all count as low coverage.
    feats["sent_low_coverage"] = feats["sent_low_coverage"].astype("boolean").fillna(True).astype(bool)

    hl, hl_t, hl_msg = headline_scores(d["news"], options.get("headline_lm_enabled"))
    if not hl_t.empty:
        # Show Bloomberg sentiment, the headline score and the manual score
        # side by side, and flag companies where they disagree.
        hl_t = hl_t.join(feats["sent_30d_raw"].rename("bloomberg_sent_30d"), how="left")
        hl_t["disagree"] = [sources_disagree(a, b, c) for a, b, c in
                            zip(hl_t["bloomberg_sent_30d"], hl_t["lm_score"], hl_t["manual_score"])]
        feats["headline_lm"] = hl_t["lm_score"].reindex(feats.index)
    extras = {"asof": asof, "betas": betas[betas["ticker"].isin(feats.index)] if not betas.empty else betas,
              "headlines": hl, "headline_by_ticker": hl_t, "headline_message": hl_msg}
    return feats, extras


if __name__ == "__main__":
    import loader
    f, ex = build_features(loader.load_data())
    keys = [m["key"] for g in config.METRICS.values() for m in g if m.get("rank", True)]
    pd.set_option("display.width", 200)
    print("Companies:", len(f))
    print(f[keys].notna().mean().round(2).to_string())
