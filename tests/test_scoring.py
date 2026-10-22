"""
Tests for features.py and scoring.py, on small made-up companies.

Run:  .venv/bin/python -m pytest -q
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402
import features  # noqa: E402
import loader  # noqa: E402
import scoring  # noqa: E402


def make_feats(n=12, seed=0, industry="Machinery", sector="Industrials"):
    """n fake companies in one industry with random values for every metric."""
    rng = np.random.default_rng(seed)
    idx = ["T%02d US Equity" % i for i in range(n)]
    f = pd.DataFrame(index=idx)
    f["industry"], f["sector"] = industry, sector
    for m in scoring.metric_defs({"use_capex_intensity": True}):   # every metric, capex included
        if m.get("rank", True):
            f[m["key"]] = rng.normal(size=n)
    return f


# ---- 1. scores stay between 1 and 10 ------------------------------------------
def test_scores_between_1_and_10_even_with_outliers_and_gaps():
    f = make_feats(40)
    f.iloc[0, 2:] = 1e9      # absurd outliers
    f.iloc[1, 2:] = -1e9
    f.iloc[2:6, 5:12] = np.nan
    s = scoring.score_all(f)["scores"]["score"]
    assert s.between(1, 10).all()


def test_best_on_everything_scores_10_and_worst_scores_1():
    f = make_feats(10)
    for m in scoring.metric_defs():
        if m.get("rank", True):
            f[m["key"]] = np.arange(10) * m["direction"]   # T09 best on every metric
    s = scoring.score_all(f)["scores"]["score"]
    assert s["T09 US Equity"] == pytest.approx(10)
    assert s["T00 US Equity"] == pytest.approx(1)


# ---- 2. lower-is-better flips -----------------------------------------------------
def test_lower_is_better_is_flipped():
    f = make_feats(10)
    f["PE_RATIO"] = np.arange(1, 11, dtype=float)        # T00 cheapest
    f["SALES_GROWTH"] = np.arange(1, 11, dtype=float)    # T09 fastest growth
    r = scoring.rank_metrics(f).set_index(["ticker", "metric"])["pct"]
    assert r[("T00 US Equity", "PE_RATIO")] == 1.0       # lowest P/E = best
    assert r[("T09 US Equity", "PE_RATIO")] == 0.0
    assert r[("T09 US Equity", "SALES_GROWTH")] == 1.0   # higher growth = best


# ---- 3. missing data does not crash ---------------------------------------------
def test_missing_data_does_not_crash():
    f = make_feats(10)
    keys = [m["key"] for m in scoring.metric_defs() if m.get("rank", True)]
    f.loc["T00 US Equity", keys] = np.nan                # a company with nothing
    f["PE_RATIO"] = np.nan                               # a metric nobody has
    r = scoring.score_all(f, links=None)
    assert np.isnan(r["scores"].loc["T00 US Equity", "score"])
    assert r["scores"].loc["T00 US Equity", "completeness"] == 0
    assert r["scores"].drop("T00 US Equity")["score"].notna().all()
    scoring.explain(r, "T01 US Equity")                  # explanation works too
    empty = scoring.score_all(f, links=pd.DataFrame())   # empty links table
    assert empty["scores"]["score"].notna().sum() == 9


# ---- 4. renormalization -------------------------------------------------------------
def test_weights_renormalize_over_available_groups():
    f = pd.DataFrame({"industry": "X", "sector": "Y",
                      "SALES_GROWTH": [1.0, 2.0, 3.0],          # Health
                      "PE_RATIO": [np.nan, 10.0, 20.0]},       # Valuation
                     index=["A", "B", "C"])
    r = scoring.score_all(f)
    s = r["scores"]["score"]
    # A only has Health (percentile 0) -> Health gets 100 percent weight -> 1.0
    assert s["A"] == pytest.approx(1.0)
    # B: Health pct 0.5, Valuation pct 1.0 (cheapest of B, C); equal weights 0.2/0.2
    assert s["B"] == pytest.approx(1 + 9 * (0.5 * 0.2 + 1.0 * 0.2) / 0.4)
    assert r["effective_weights"].loc["A", "Health"] == pytest.approx(1.0)


def test_contributions_add_up_to_the_score():
    r = scoring.score_all(make_feats(20))
    s = r["scores"]["score"]
    assert (5.5 + r["contributions"].sum(axis=1) - s).abs().max() < 1e-9
    det = r["metric_detail"].groupby(["ticker", "group"])["contribution"].sum()
    grp = r["contributions"].stack()
    assert (det - grp.reindex(det.index)).abs().max() < 1e-9


def test_small_industry_falls_back_to_sector():
    a = make_feats(3, industry="Tiny", sector="Energy")
    b = make_feats(10, seed=1, industry="Big", sector="Energy")
    b.index = ["B%02d" % i for i in range(10)]
    r = scoring.rank_metrics(pd.concat([a, b]))
    lv = r.set_index(["ticker", "metric"])["peer_level"]
    assert lv[("T00 US Equity", "PE_RATIO")] == "sector"
    assert lv[("B00", "PE_RATIO")] == "industry"


# ---- 5. two-pass linked score ---------------------------------------------------------
def make_links(rows):
    cols = ["main_ticker", "direction", "universe_ticker", "exposure_pct", "cost_type"]
    L = pd.DataFrame(rows, columns=cols)
    L["in_universe"] = L["universe_ticker"].notna()
    return L


def test_linked_score_uses_standalone_scores_and_cost_weights():
    f = make_feats(12)
    A, B, C = f.index[:3]
    links = make_links([
        (A, "Supplier", B, 0.03, "COGS"),
        (A, "Supplier", C, 0.01, "SGA"),          # SGA counts 25 percent
        (A, "Supplier", None, 0.50, "COGS"),      # not in universe: ignored
        (B, "Customer", A, 0.10, "CAPEX"),        # gives B a linked score too
    ])
    r = scoring.score_all(f, links)
    sc = r["scores"]
    sB, sC = sc.loc[B, "standalone_score"], sc.loc[C, "standalone_score"]
    expected = (sB * 0.03 + sC * 0.01 * 0.25) / (0.03 + 0.01 * 0.25)
    got = r["metric_detail"].set_index(["ticker", "metric"]).loc[(A, "linked_suppliers"), "value"]
    # Must use B's STANDALONE score, even though B's final score differs.
    assert sc.loc[B, "score"] != pytest.approx(sB)
    assert got == pytest.approx(expected)
    # Companies without links: final score equals standalone score.
    assert sc.loc[C, "score"] == pytest.approx(sc.loc[C, "standalone_score"])
    assert sc.loc[A, "n_scored_links"] == 2


def test_percent_units_converted_before_weighting(monkeypatch):
    """%Cost Category of 3.0 (percent units) must become a weight of 0.03."""
    monkeypatch.setitem(loader.LCFG, "sc_n_blocks", 1)
    hdr = ("Ticker", "Company Name", "Cost Category", "%Cost Category", "%Supplier Rev",
           "Relationship Value (Q) (Mln) (USD)", "Relationship Year", "Relationship Period", "Source", "As Of Date")
    rows = [("A",) + (None,) * 9, hdr,
            ("B", "Bco", "COGS", 3.0, 17.0, 10.0, "2026", "Q1", "Estimate (B)", "7/7/2026")]
    rows = [tuple(loader.clean_value(v) for v in r) for r in rows]
    recs, _ = loader.parse_sc_tab(rows, "SC_Suppliers", "Supplier", ["A"])
    recs["universe_ticker"], recs["in_universe"] = "B", True
    _, detail = scoring.linked_scores(recs, pd.Series({"B": 7.0}))
    assert detail["link_weight"].iloc[0] == pytest.approx(0.03)
    assert recs["reverse_pct"].iloc[0] == pytest.approx(0.17)


# ---- 6. features ------------------------------------------------------------------------
def test_sentiment_no_news_days_are_not_zero_and_low_coverage_excluded():
    asof = pd.Timestamp("2026-10-19")
    rows = [("A", asof - pd.Timedelta(days=k), "NEWS_SENTIMENT_DAILY_AVG", 0.5) for k in range(6)]
    rows += [("B", asof - pd.Timedelta(days=k), "NEWS_SENTIMENT_DAILY_AVG", 0.5) for k in range(3)]
    s = pd.DataFrame(rows, columns=["ticker", "date", "field", "value"])
    out = features.sentiment_features(s, asof)
    assert out.loc["A", "sent_30d"] == pytest.approx(0.5)     # gaps not counted as 0
    assert out.loc["A", "sent_coverage"] == 6
    assert out.loc["B", "sent_low_coverage"]
    assert np.isnan(out.loc["B", "sent_30d"])
    assert out.loc["B", "sent_30d_raw"] == pytest.approx(0.5)  # still shown


def test_eps_revision_sign_with_negative_estimates():
    assert features.safe_pct_change(-0.5, -1.0) == pytest.approx(0.5)  # loss shrinking = up
    assert features.safe_pct_change(2.2, 2.0) == pytest.approx(0.1)
    assert np.isnan(features.safe_pct_change(1.0, 0.0))


# ---- 7. options and badges added before Phase 3 ----------------------------------------
def test_capex_toggle_removes_the_metric_and_renormalizes():
    f = make_feats(12)
    on = scoring.score_all(f, options={"use_capex_intensity": True})
    off = scoring.score_all(f, options={"use_capex_intensity": False})
    assert "capex_intensity" in set(on["ranked"]["metric"])
    assert "capex_intensity" not in set(off["ranked"]["metric"])
    assert off["scores"]["completeness"].eq(1).all()      # 2 of 2 demand metrics, not 2 of 3
    assert off["scores"]["score"].between(1, 10).all()


def test_top_percent_label():
    s = scoring.score_all(make_feats(50))["scores"]
    assert config.CONFIG["scoring"]["use_capex_intensity"] is False   # off by default
    best = s.sort_values("score", ascending=False).iloc[0]
    assert best["top_pct_label"] == "top 2%"               # rank 1 of 50


def test_peer_caveats_all_companies_manual_and_extreme(monkeypatch):
    big = make_feats(10, industry="Big", sector="Industrials")
    tiny = make_feats(3, seed=2, industry="Mining", sector="Materials")
    tiny.index = ["M%d" % i for i in range(3)]
    f = pd.concat([big, tiny])
    f.loc["T00 US Equity", "PX_TO_BOOK_RATIO"] = 1e4      # extreme ratio
    monkeypatch.setitem(config.CONFIG, "peer_group_caveats", {"T01 US Equity": "poor fit"})
    cav = scoring.score_all(f)["scores"]["peer_caveat"]
    assert "ranked against all companies" in cav["M0"]
    assert cav["T01 US Equity"].startswith("poor fit")
    assert "extreme value: Price / book" in cav["T00 US Equity"]
    assert cav["T05 US Equity"] == ""


def test_linked_coverage_counts_distinct_scored_companies():
    f = make_feats(6)
    A, B, C = f.index[:3]
    links = make_links([(A, "Supplier", B, 0.03, "COGS"), (A, "Customer", B, 0.02, "CAPEX"),
                        (A, "Supplier", None, 0.01, "COGS"), (A, "Customer", C, 0.01, "COGS")])
    links["related_key"] = ["B", "B", "X", "C"]
    s = scoring.score_all(f, links)["scores"]
    assert s.loc[A, "linked_coverage"] == "2 of 3 scored"
