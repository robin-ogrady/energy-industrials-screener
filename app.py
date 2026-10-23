"""
app.py: the Streamlit dashboard.

Run from the project folder:
    .venv/bin/streamlit run app.py

Pages: Rankings, Company, Network, Commodities, Data quality.
The sidebar holds the factor weight sliders (scores recompute live), the
capex and headline toggles, and the filters used by Rankings and the report.
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
import features
import loader
import make_report
import network
import scoring

st.set_page_config(page_title="Energy & Industrials Screener", layout="wide")

# ---------------------------------------------------------------------------
# Notes shown on several pages
# ---------------------------------------------------------------------------
NOTE = "Not validated. A screening tool, not a trading signal."

TOP20_NOTE = ("Supply chain links are the top 20 per company only, so a missing link does not mean there is "
              "no relationship. Most link figures are Bloomberg estimates, not reported numbers.")


def noise_note(result):
    return make_report.cluster_note(result["scores"]["score"])


def linked_note(d):
    ls = make_report.link_stats(d)
    return ("Linked companies has limited coverage: only the %d core names have links, and only %.0f percent of "
            "linked companies are in the universe and have a score." % (ls["n_core"], 100 * ls["share_in"]))

# Colors (validated reference palette). Blue = helps the score, red = hurts.
POS, NEG, MID = "#2a78d6", "#e34948", "#f0efec"
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]
DIVERGING = [[0.0, "#b3302f"], [0.25, "#ec9a99"], [0.5, MID], [0.75, "#86b6ef"], [1.0, "#184f95"]]

GROUPS = list(config.METRICS)
LABEL = config.group_label
DEFAULT_W = config.CONFIG["scoring"]["group_weights"]


def short(t):
    return network.short(t)


def style(fig, height=320, title=None):
    """Common Plotly look: light surface, recessive grid, no clutter."""
    fig.update_layout(
        height=height, template="plotly_white", plot_bgcolor=SURFACE, paper_bgcolor=SURFACE,
        margin=dict(l=10, r=10, t=40 if title else 10, b=10), title=dict(text=title or ""),
        font=dict(size=12, color=INK), hoverlabel=dict(bgcolor="white"),
        legend=dict(orientation="h", y=1.08, x=0, font=dict(color=INK2)),
    )
    fig.update_xaxes(gridcolor=GRID, zeroline=False, linecolor=GRID)
    fig.update_yaxes(gridcolor=GRID, zeroline=False, linecolor=GRID)
    return fig


# ---------------------------------------------------------------------------
# Cached data and scores
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner="Loading data...")
def get_data():
    return loader.load_data()


@st.cache_data(show_spinner="Building features...")
def get_features(headline_on):
    return features.build_features(get_data(), {"headline_lm_enabled": headline_on})


@st.cache_data(show_spinner=False)
def get_ranked(headline_on, options_items):
    feats, _ = get_features(headline_on)
    return scoring.rank_metrics(feats, dict(options_items))


@st.cache_data(show_spinner="Scoring...")
def get_result(weight_items, headline_on, options_items):
    feats, _ = get_features(headline_on)
    ranked = get_ranked(headline_on, options_items)
    out = scoring.aggregate(feats, ranked, get_data()["links"], dict(weight_items), dict(options_items))
    out["ranked"] = ranked
    return out


@st.cache_resource(show_spinner="Laying out the network...")
def get_layout():
    """Positions depend only on who is linked to whom, so compute once."""
    d = get_data()
    feats, _ = get_features(False)
    G = network.build_graph(d["links"], None, feats)
    return network.layout(G)


# ---------------------------------------------------------------------------
# Sidebar: weights, toggles, filters
# ---------------------------------------------------------------------------
def reset_weights():
    for g in GROUPS:
        st.session_state["w_" + g] = float(DEFAULT_W[g])


def sidebar():
    sb = st.sidebar
    sb.caption(NOTE)
    sb.caption("Backtest: not supported. [Details](%s)" % make_report.BACKTEST_URL)
    sb.subheader("Factor weights")
    for g in GROUPS:
        st.session_state.setdefault("w_" + g, float(DEFAULT_W[g]))
        sb.slider(LABEL(g), 0.0, 0.5, step=0.01, key="w_" + g)
    sb.button("Reset weights", on_click=reset_weights)
    weights = {g: st.session_state["w_" + g] for g in GROUPS}
    if sum(weights.values()) <= 0:
        sb.error("All weights are zero. Using the defaults.")
        weights = dict(DEFAULT_W)
    sb.caption("Weights are rescaled to sum to 100 percent, and again per company over the groups it has data for.")

    sb.subheader("Scoring options")
    capex_on = sb.toggle("Score capex intensity (Demand pipeline)",
                         value=config.CONFIG["scoring"]["use_capex_intensity"], key="capex_on",
                         help="Capex / last 4 quarters of sales, higher = better, ranked within industry.")
    with sb.expander("Headline scoring (optional)"):
        head_on = st.toggle("Score headlines (Loughran-McDonald)", value=config.CONFIG["features"]["headline_lm_enabled"],
                            key="head_on")
        head_in = st.toggle("Use headline score in the main score", value=False, key="head_in", disabled=not head_on)
        st.caption("Off by default. Downloads the word list into cache/ the first time.")

    d = get_data()
    feats, _ = get_features(head_on)
    sb.subheader("Filters")
    chains = sorted(x for x in feats["chain"].dropna().unique())
    sb.multiselect("Chain", chains, key="f_chain")
    sb.multiselect("Sector", sorted(feats["sector"].dropna().unique()), key="f_sector")
    sb.multiselect("Industry", sorted(feats["industry"].dropna().unique()), key="f_industry")
    sb.toggle("Core names only (%d)" % int(feats["is_core"].sum()), key="f_core")

    options = (("use_capex_intensity", capex_on), ("use_headline_score", bool(head_on and head_in)))
    result = get_result(tuple(sorted(weights.items())), head_on, options)

    # --- printable report, built with the current weights, options, filters ---
    sb.subheader("Report")
    filters = current_filters()
    sig = repr((sorted(weights.items()), options, sorted(filters.items()), head_on))
    if sb.button("Build PDF report", help="Uses the current weights, scoring options and filters"):
        with st.spinner("Building report..."):
            st.session_state["pdf_bytes"] = make_report.build_pdf(d, feats, result, filters, dict(options))
            st.session_state["pdf_sig"] = sig
    if st.session_state.get("pdf_bytes") and st.session_state.get("pdf_sig") == sig:
        sb.download_button("Download report", st.session_state["pdf_bytes"], file_name="report.pdf",
                           mime="application/pdf", type="primary")
    elif st.session_state.get("pdf_bytes"):
        sb.caption("Weights or filters changed: build the report again.")
    return d, feats, result, head_on


def current_filters():
    s = st.session_state
    return {"chain": s.get("f_chain") or [], "sector": s.get("f_sector") or [],
            "industry": s.get("f_industry") or [], "core_only": bool(s.get("f_core"))}


def apply_filters(df):
    """Filter a table that has chain, sector, industry and is_core columns
    (same rules as the PDF report)."""
    return make_report.filter_table(df, current_filters())


def ranking_table(feats, result):
    """One row per scored company with everything the Rankings page shows."""
    s = result["scores"]
    t = feats[["NAME", "chain", "sector", "industry", "is_core", "CUR_MKT_CAP"]].join(s)
    t["ticker"] = [short(x) for x in t.index]
    t["flag"] = np.where(t["low_data"], "*", "")
    t["mkt_cap_bn"] = t["CUR_MKT_CAP"] / 1e9
    t["linked_coverage"] = np.where(t["is_core"], t["linked_coverage"].str.replace(" scored", ""), "")
    t["caveat_badge"] = t["peer_caveat"].map(caveat_badge)
    return t.sort_values("score", ascending=False)


caveat_badge = make_report.caveat_badge


# ---------------------------------------------------------------------------
# Page: Rankings
# ---------------------------------------------------------------------------
def page_rankings():
    d, feats, result, _ = APP
    st.title("Rankings")
    st.warning(NOTE)
    st.info("**Backtest result:** %s [Rules, code and verdict](%s)." % (make_report.BACKTEST_NOTE, make_report.BACKTEST_URL))
    st.caption(noise_note(result))
    t = ranking_table(feats, result)
    shown = apply_filters(t)
    n_uni = len(d["universe"])
    c = st.columns(4)
    c[0].metric("Companies scored", "%d of %d" % (t["score"].notna().sum(), n_uni))
    c[1].metric("Shown with filters", len(shown))
    c[2].metric("Built on <60% of data (*)", int(shown["low_data"].sum()))
    c[3].metric("Peer group caveats", int((shown["peer_caveat"] != "").sum()))

    cols = ["rank", "top_pct_label", "ticker", "NAME", "score", "flag", "completeness", "industry",
            "mkt_cap_bn", "drivers", "linked_coverage", "caveat_badge", "peer_caveat"]
    shown = shown.assign(NAME=shown["NAME"].map(lambda x: make_report.nice_name(x, 40)))
    view = shown[cols].reset_index(drop=True)
    view["completeness"] = 100 * view["completeness"]
    event = st.dataframe(
        view, hide_index=True, width="stretch", height=560,
        on_select="rerun", selection_mode="single-row",
        column_config={
            "rank": st.column_config.NumberColumn("Rank", format="%d", width=45),
            "top_pct_label": st.column_config.TextColumn("Percentile", help="Rank among all scored companies", width=70),
            "ticker": st.column_config.TextColumn("Ticker", width=55),
            "NAME": st.column_config.TextColumn("Name", width=170),
            "score": st.column_config.NumberColumn("Score", format="%.1f", help="1 to 10", width=50),
            "flag": st.column_config.TextColumn("*", help="* = built on under 60 percent of the data", width=25),
            "completeness": st.column_config.ProgressColumn("Data %", format="%.0f%%", min_value=0, max_value=100, width=80),
            "industry": st.column_config.TextColumn("Industry", width=150),
            "mkt_cap_bn": st.column_config.NumberColumn("Mkt cap $bn", format="%.1f", width=70),
            "drivers": st.column_config.TextColumn("Top 3 drivers", width=210),
            "linked_coverage": st.column_config.TextColumn("Linked cov.", width=70,
                                                           help="Core names only: linked companies with a score, out of all linked companies"),
            "caveat_badge": st.column_config.TextColumn("Caveat", width=150, help="Peer group caveat; details on the company page"),
            "peer_caveat": st.column_config.TextColumn("Caveat detail"),
        })
    st.caption("Click a row to open the company page. Click a column header to sort. " + linked_note(d))
    sel = event.selection.rows if event and event.selection else []
    if sel:
        st.session_state["company"] = shown.index[sel[0]]
        st.switch_page(PAGES["company"])

    with st.expander("How the score works (short version)"):
        st.markdown(
            "1. Each metric is ranked as a percentile within the company's GICS industry (sector if fewer than 8 "
            "industry peers have the number, all companies as a last resort), after clipping outliers at the 5th "
            "and 95th percentile. Lower-is-better metrics are flipped.\n"
            "2. Metrics are averaged into 8 factor groups.\n"
            "3. **Pass 1:** a standalone score from every group except Linked companies.\n"
            "4. **Pass 2:** Linked companies = exposure-weighted average standalone score of the company's scored "
            "suppliers and customers (SGA links count 25 percent).\n"
            "5. Final score = weighted average of the groups the company has (weights renormalized), "
            "scaled as 1 + 9 x average. 5.5 is neutral.")


# ---------------------------------------------------------------------------
# Page: Company detail
# ---------------------------------------------------------------------------
def peer_median(ranked, feats, ticker, metric, level):
    """Median raw value of a metric across the peer group used for ranking."""
    r = ranked[ranked["metric"] == metric]
    if level == "industry":
        peers = feats.index[feats["industry"] == feats.at[ticker, "industry"]]
    elif level == "sector":
        peers = feats.index[feats["sector"] == feats.at[ticker, "sector"]]
    else:
        peers = feats.index
    return r[r["ticker"].isin(peers)]["value"].median()


def badge_row(items):
    """Show caveat badges as small colored labels."""
    for kind, text in items:
        if kind == "warn":
            st.warning(text, icon="⚠️")
        else:
            st.info(text, icon="ℹ️")


def page_company():
    d, feats, result, head_on = APP
    _, extras = get_features(head_on)
    s = result["scores"]
    tickers = s.sort_values("score", ascending=False).index.tolist()
    qp = st.query_params.get("ticker")
    if "company" not in st.session_state:
        guess = (qp or "SLB").upper()
        guess = guess if guess.endswith("EQUITY") else guess + " US Equity"
        st.session_state["company"] = guess if guess in tickers else tickers[0]
    t = st.selectbox("Company", tickers, key="company",
                     format_func=lambda x: "%s  %s" % (short(x), feats.at[x, "NAME"]))
    f = feats.loc[t]
    row = s.loc[t]
    ex = scoring.explain(result, t)

    st.title("%s  (%s)" % (f["NAME"], short(t)))
    st.caption("%s / %s%s   |   %s" % (f["sector"], f["industry"],
                                       ("   |   chain: %s" % f["chain"]) if isinstance(f["chain"], str) else "", NOTE))
    c = st.columns(5)
    c[0].metric("Score (1 to 10)", "%.1f%s" % (row["score"], " *" if row["low_data"] else ""))
    c[1].metric("Rank", "%d of %d" % (row["rank"], s["score"].notna().sum()), row["top_pct_label"], delta_color="off")
    c[2].metric("Data completeness", "%.0f%%" % (100 * row["completeness"]))
    c[3].metric("Standalone score", "%.1f" % row["standalone_score"], help="Score without the Linked companies group")
    c[4].metric("Linked coverage (scored)", row["linked_coverage"].replace(" scored", "") if f["is_core"] else "n/a",
                help="Linked companies that have a score, out of all linked companies (core names only)")

    badges = []
    if row["low_data"]:
        badges.append(("warn", "Built on under 60 percent of the data."))
    if row["peer_caveat"]:
        badges.append(("warn", "Peer group caveat: " + row["peer_caveat"]))
    if f.get("div_assumed_zero"):
        badges.append(("info", "No dividend yield in the data: assumed 0 (probably a non-payer)."))
    if f.get("sent_low_coverage", True):
        badges.append(("info", "News sentiment: low coverage (under %d news days in 30), left out of the score."
                       % config.CONFIG["features"]["sentiment_min_coverage"]))
    badge_row(badges)
    st.caption(noise_note(result))

    # --- score by factor group + reasons ----------------------------------------
    left, right = st.columns([3, 2])
    g = ex["groups"].copy()
    g = g.reindex(GROUPS)
    g["label"] = [LABEL(x) for x in g.index]
    gp = g.dropna(subset=["points"])
    fig = go.Figure(go.Bar(
        x=gp["points"], y=gp["label"], orientation="h",
        marker=dict(color=[POS if v >= 0 else NEG for v in gp["points"]], cornerradius=4),
        text=["%+.2f" % v for v in gp["points"]], textposition="outside", textfont=dict(color=INK2),
        hovertemplate="%{y}<br>%{x:+.2f} points<extra></extra>"))
    fig.add_vline(x=0, line_color=INK2, line_width=1)
    fig.update_yaxes(autorange="reversed")
    lim = max(0.3, 1.35 * float(gp["points"].abs().max())) if len(gp) else 1
    fig.update_xaxes(title="points vs a neutral 5.5", range=[-lim, lim])
    left.plotly_chart(style(fig, 300, "Score by factor group"))
    missing = [LABEL(x) for x in g.index if pd.isna(g.at[x, "points"])]
    if missing:
        left.caption("No data (weight spread over the others): " + ", ".join(missing))
    right.markdown("**What pushed the score up**")
    for r in ex["reasons_up"] or ["(nothing stood out)"]:
        right.markdown("- " + r)
    right.markdown("**What pulled it down**")
    for r in ex["reasons_down"] or ["(nothing stood out)"]:
        right.markdown("- " + r)

    # --- key metrics vs peer median ----------------------------------------------
    with st.expander("Key metrics vs peer median", expanded=False):
        m = ex["metrics"].copy()
        m = m[m["group"] != config.LINKED_GROUP]
        if len(m):
            m["peer median"] = [peer_median(result["ranked"], feats, t, a, b) for a, b in zip(m["metric"], m["peer_level"])]
            units = {x["key"]: x["unit"] for x in scoring.metric_defs({"use_capex_intensity": True, "use_headline_score": True})}
            m["value_s"] = [scoring.fmt_value(v, units.get(k, "num")) for v, k in zip(m["value"], m["metric"])]
            m["median_s"] = [scoring.fmt_value(v, units.get(k, "num")) for v, k in zip(m["peer median"], m["metric"])]
            m["percentile"] = (100 * m["pct"]).round(0)
            m["peers"] = [scoring.peer_phrase(a, b) for a, b in zip(m["peer_level"], m["peer_name"])]
            m["group"] = m["group"].map(LABEL)
            st.dataframe(m[["group", "label", "value_s", "median_s", "percentile", "peers", "contribution"]]
                         .sort_values(["group", "contribution"]), hide_index=True, width="stretch",
                         column_config={"label": "Metric", "value_s": "Value", "median_s": "Peer median",
                                        "percentile": st.column_config.NumberColumn("Percentile (100 = best)", format="%d"),
                                        "contribution": st.column_config.NumberColumn("Points", format="%+.2f")})

    # --- charts --------------------------------------------------------------------
    c1, c2 = st.columns(2)
    with c1:
        price_chart(d, extras, t, f)
    with c2:
        inventory_chart(d, t, f)
    c3, c4 = st.columns(2)
    with c3:
        estimates_charts(d, t, f)
    with c4:
        news_panel(d, extras, t, f)

    # --- linked suppliers and customers -----------------------------------------------
    linked_section(d, feats, result, t)


def price_chart(d, extras, t, f):
    p = d["prices"][(d["prices"]["ticker"] == t)].set_index("date")["value"].sort_index()
    names = config.CONFIG["features"]["commodities"]
    default = f.get("top_commodity")
    opts = list(names.values())
    pick = st.selectbox("Commodity overlay", opts, index=opts.index(default) if default in opts else 0, key="comm_pick")
    code = [k for k, v in names.items() if v == pick][0]
    cm = d["commodities"][d["commodities"]["ticker"] == code].set_index("date")["value"].sort_index()
    both = pd.concat([p.rename("stock"), cm.rename("comm")], axis=1, join="inner").dropna()
    fig = go.Figure()
    if len(both):
        idx = 100 * both / both.iloc[0]
        fig.add_trace(go.Scatter(x=idx.index, y=idx["stock"], name=short(t), line=dict(color=SERIES[0], width=2)))
        fig.add_trace(go.Scatter(x=idx.index, y=idx["comm"], name=pick, line=dict(color=SERIES[1], width=2)))
        fig.update_layout(hovermode="x unified")
    fig.update_yaxes(title="indexed, first day = 100")
    st.plotly_chart(style(fig, 300, "Price vs %s (both indexed to 100)" % pick))
    b = extras["betas"]
    b = b[b["ticker"] == t]
    if len(b):
        st.caption("Sensitivity over the last year: " + "; ".join(
            "%s beta %.2f (corr %.2f)" % (r["commodity_name"], r["beta"], r["corr"])
            for _, r in b.dropna(subset=["beta"]).iterrows()))


def inventory_chart(d, t, f):
    q = d["fundamentals"]
    q = q[(q["ticker"] == t) & q["field"].isin(["BS_INVENTORIES", "SALES_REV_TURN"])]
    w = q.pivot_table(index="date", columns="field", values="value").sort_index()
    fig = go.Figure()
    ok = "BS_INVENTORIES" in w and "SALES_REV_TURN" in w and (w["BS_INVENTORIES"] > 0).any()
    if ok:
        w = w.dropna()
        w = w[(w > 0).all(axis=1)]          # index needs positive starting values
    if ok and len(w) >= 2:
        idx = 100 * w / w.iloc[0]
        fig.add_trace(go.Scatter(x=idx.index, y=idx["BS_INVENTORIES"], name="Inventory",
                                 line=dict(color=SERIES[0], width=2), mode="lines+markers", marker=dict(size=8)))
        fig.add_trace(go.Scatter(x=idx.index, y=idx["SALES_REV_TURN"], name="Quarterly sales",
                                 line=dict(color=SERIES[1], width=2), mode="lines+markers", marker=dict(size=8)))
        fig.update_layout(hovermode="x unified")
        fig.update_yaxes(title="indexed, first quarter = 100")
    else:
        ok = False
        fig.add_annotation(text="No inventory data (company carries no inventory)", showarrow=False)
        fig.update_xaxes(visible=False)
        fig.update_yaxes(visible=False)
    st.plotly_chart(style(fig, 300, "Inventory vs sales (both indexed to 100)"))
    bits = []
    if pd.notna(f.get("inv_minus_sales_growth")):
        bits.append("inventory growth minus sales growth over 4 quarters: %+.1f%%" % (100 * f["inv_minus_sales_growth"]))
    if pd.notna(f.get("inv_to_sales")):
        bits.append("inventory / sales: %.1f%%" % (100 * f["inv_to_sales"]))
    if pd.notna(f.get("capex_intensity")):
        bits.append("capex / sales: %.1f%%" % (100 * f["capex_intensity"]))
    if bits:
        st.caption("; ".join(bits))


def estimates_charts(d, t, f):
    e = d["estimates"][d["estimates"]["ticker"] == t]
    eps = e[e["field"] == "BEST_EPS"].sort_values("date")
    rec = e[e["field"] == "EQY_REC_CONS"].sort_values("date")
    fig = go.Figure(go.Scatter(x=eps["date"], y=eps["value"], line=dict(color=SERIES[0], width=2), name="FY1 EPS"))
    fig.update_yaxes(title="USD per share")
    st.plotly_chart(style(fig, 230, "Consensus EPS estimate, current fiscal year (weekly)"))
    fig = go.Figure(go.Scatter(x=rec["date"], y=rec["value"], line=dict(color=SERIES[0], width=2), name="Rating"))
    fig.update_yaxes(title="1 sell to 5 buy")
    st.plotly_chart(style(fig, 200, "Analyst consensus rating"))
    bits = []
    for k, lab in [("eps_rev_4w", "4-week revision"), ("eps_rev_12w", "12-week revision")]:
        if pd.notna(f.get(k)):
            bits.append("%s %+.1f%%" % (lab, 100 * f[k]))
    if pd.notna(f.get("rec_change_12w")):
        bits.append("rating change (12 wk) %+.2f" % f["rec_change_12w"])
    st.caption("; ".join(bits) + ". A jump can also be a fiscal-year rollover, not a revision.")


def news_panel(d, extras, t, f):
    st.markdown("**News**")
    s = d["sentiment"][d["sentiment"]["ticker"] == t].sort_values("date")
    fig = go.Figure(go.Bar(x=s["date"], y=s["value"], marker=dict(color=[POS if v >= 0 else NEG for v in s["value"]]),
                           hovertemplate="%{x|%b %d}: %{y:.2f}<extra></extra>"))
    asof = extras["asof"]
    fig.add_vrect(x0=asof - pd.Timedelta(days=config.CONFIG["features"]["sentiment_long_days"]), x1=asof,
                  fillcolor=MID, opacity=0.6, line_width=0, layer="below")
    if pd.notna(f.get("sent_30d_raw")):
        fig.add_hline(y=f["sent_30d_raw"], line_dash="dot", line_color=INK2,
                      annotation_text="30-day avg %.2f" % f["sent_30d_raw"], annotation_font_color=INK2)
    fig.update_yaxes(range=[-1, 1], title="Bloomberg daily sentiment")
    st.plotly_chart(style(fig, 230, "News sentiment (days without news are blank, not zero)"))
    cov = f.get("sent_coverage")
    cov = 0 if pd.isna(cov) else int(cov)
    msg = "Coverage: %d news days in the last 30 (shaded)." % cov
    if f.get("sent_low_coverage", True):
        msg += " **Low coverage**: sentiment is left out of the score."
    elif pd.notna(f.get("sent_trend_raw")):
        msg += " Trend (7-day minus 30-day): %+.2f." % f["sent_trend_raw"]
    st.markdown(msg)
    st.caption("Attention measure: %s." % f.get("attention_source", "coverage"))
    news = d["news"]
    news = news[news["ticker"] == t] if not news.empty else news
    if news.empty:
        st.caption("Headlines not loaded.")
    else:
        h = extras["headlines"]
        show = h[h["ticker"] == t] if not h.empty else news
        cols = [c for c in ["date", "headline", "manual_score", "lm_score"] if c in show.columns]
        st.dataframe(show[cols], hide_index=True, width="stretch")
        ht = extras["headline_by_ticker"]
        if not ht.empty and t in ht.index and ht.at[t, "disagree"]:
            st.warning("Sources disagree: Bloomberg sentiment, headline tone and your manual score point different ways.")
    if extras["headline_message"] not in ("ok", "Headline scoring is off."):
        st.caption(extras["headline_message"])


def linked_section(d, feats, result, t):
    st.subheader("Linked suppliers and customers")
    st.caption(TOP20_NOTE + " " + linked_note(d))
    L = d["links"]
    scores = result["scores"]["score"]
    mine = L[L["main_ticker"] == t].copy()
    appears = L[(L["universe_ticker"] == t) & (L["main_ticker"] != t)].copy()
    if mine.empty and appears.empty:
        st.info("No supply chain links: links were pulled only for the core names, and this company does not "
                "appear in their top 20 lists.")
        return

    def table(df, direction):
        x = df[df["direction"] == direction].copy()
        if x.empty:
            return None
        sc = pd.to_numeric(x["universe_ticker"].map(scores), errors="coerce")
        x["score"] = ["%.1f" % v if pd.notna(v) else ("not scored" if u else "not in universe")
                      for v, u in zip(sc, x["in_universe"])]
        x["ticker"] = np.where(x["in_universe"], x["universe_ticker"].map(short), x["related_ticker"])
        x["exposure"] = 100 * x["exposure_pct"]
        x["reverse"] = 100 * x["reverse_pct"]
        x["cost_type"] = x["cost_type"].fillna("missing")
        x["note"] = np.where((direction == "Customer") & (x["cost_type"] == "CAPEX"), "capex-driven revenue", "")
        x["year"] = x["as_of_year"]
        return x.sort_values("exposure", ascending=False)[
            ["related_name", "ticker", "score", "exposure", "reverse", "cost_type", "note", "source_type", "year"]]

    cfg = lambda exp_help, rev_help: {
        "related_name": "Company", "ticker": "Ticker",
        "score": st.column_config.TextColumn("Score", help="Only companies in the universe have a score"),
        "exposure": st.column_config.NumberColumn("Exposure %", format="%.2f", help=exp_help),
        "reverse": st.column_config.NumberColumn("Reverse %", format="%.2f", help=rev_help),
        "cost_type": "Cost type", "note": "", "source_type": "Source", "year": st.column_config.NumberColumn("As of", format="%d"),
    }
    if not mine.empty:
        r = result["scores"].loc[t]
        st.markdown("**Linked coverage: %s** (linked companies with a score, out of all linked companies)."
                    % r["linked_coverage"])
        dep = feats.loc[t]
        bits = []
        if pd.notna(dep.get("supplier_dep_max")):
            bits.append("most dependent supplier: %s gets %.1f%% of its revenue from %s"
                        % (dep["supplier_dep_max_name"], 100 * dep["supplier_dep_max"], short(t)))
        if pd.notna(dep.get("customer_concentration")):
            bits.append("listed customers = %.1f%% of revenue (%.0f%% of that is capex-driven)"
                        % (100 * dep["customer_concentration"], 100 * dep.get("capex_driven_share", 0)))
        if bits:
            st.caption("Dependence: " + "; ".join(bits) + ".")
        a, b = st.container(), st.container()   # stacked: the tables need the full width
        with a:
            st.markdown("Suppliers")
            x = table(mine, "Supplier")
            if x is None:
                st.caption("No supplier links.")
            else:
                st.dataframe(x, hide_index=True, width="stretch", height=360,
                             column_config=cfg("Share of this company's cost category paid to the supplier",
                                               "Share of the supplier's revenue that comes from this company"))
        with b:
            st.markdown("Customers")
            x = table(mine, "Customer")
            if x is None:
                st.caption("No customer links (Bloomberg returns no quantified customers). Not an error.")
            else:
                st.dataframe(x, hide_index=True, width="stretch", height=360,
                             column_config=cfg("Share of this company's revenue from the customer",
                                               "Share of the customer's cost category paid to this company"))
        G = network.build_graph(L, result["scores"], feats)
        sub = network.ego_subgraph(G, t)
        if len(sub):
            pos = network.layout(sub, seed=3)
            st.plotly_chart(network.figure(sub, pos, highlight=None, height=460, label_all=True))
    if not appears.empty:
        st.markdown("**Appears in these core companies' lists**")
        x = appears.copy()
        x["main"] = x["main_ticker"].map(short)
        x["role"] = np.where(x["direction"] == "Supplier", "supplier to", "customer of")
        x["exposure"] = 100 * x["exposure_pct"]
        x["reverse"] = 100 * x["reverse_pct"]
        st.dataframe(x[["role", "main", "exposure", "reverse", "cost_type", "source_type", "as_of_year"]],
                     hide_index=True, width="stretch",
                     column_config={"role": "Role", "main": "Core company", "cost_type": "Cost type",
                                    "source_type": "Source", "exposure": st.column_config.NumberColumn("Exposure %", format="%.2f"),
                                    "reverse": st.column_config.NumberColumn("Reverse %", format="%.2f"),
                                    "as_of_year": st.column_config.NumberColumn("As of", format="%d")})


# ---------------------------------------------------------------------------
# Page: Network
# ---------------------------------------------------------------------------
def page_network():
    d, feats, result, _ = APP
    st.title("Supply chain network")
    st.caption(TOP20_NOTE)
    G = network.build_graph(d["links"], result["scores"], feats)
    pos = get_layout()
    n_in = sum(1 for _, a in G.nodes(data=True) if a["in_universe"])
    c = st.columns(4)
    c[0].metric("Companies (nodes)", len(G))
    c[1].metric("Links (edges)", G.number_of_edges())
    c[2].metric("In universe (scored)", "%d (%.0f%%)" % (n_in, 100 * n_in / max(len(G), 1)))
    c[3].metric("Core names", sum(1 for _, a in G.nodes(data=True) if a["is_core"]))
    core = sorted([n for n, a in G.nodes(data=True) if a["is_core"]], key=short)
    a, b = st.columns([2, 1])
    st.session_state.setdefault("net_focus", "SLB US Equity" if "SLB US Equity" in core else "(none)")
    focus = a.selectbox("Highlight a company and its neighbors", ["(none)"] + core, key="net_focus", format_func=short)
    only = b.toggle("Show only that company's neighbors", value=False)
    if only and focus != "(none)":
        sub = network.ego_subgraph(G, focus)
        st.plotly_chart(network.figure(sub, network.layout(sub, seed=3), highlight=None, label_all=True))
    else:
        st.plotly_chart(network.figure(G, pos, highlight=None if focus == "(none)" else focus))
    st.caption("Node color = score (darker blue = higher). Hollow grey = not in the universe, no score. "
               "Node size = market cap (log scale; default size if unknown). Thick outline = core name. "
               "Edge color and dash = cost category (orange = CAPEX: the customer's capital spending flows into "
               "the main company's revenue). Edge width = exposure. Arrows run supplier -> company -> customer "
               "(hover the middle of a link).")


# ---------------------------------------------------------------------------
# Page: Commodities
# ---------------------------------------------------------------------------
def page_commodities():
    d, feats, result, head_on = APP
    _, extras = get_features(head_on)
    st.title("Commodity sensitivity")
    st.caption("One simple regression of daily stock returns on daily commodity returns over the last year, per "
               "commodity. Correlation is comparable across commodities; beta is not (commodities differ in volatility). "
               "Uses the sidebar filters. " + NOTE)
    measure = st.radio("Show", ["Correlation", "Beta"], horizontal=True)
    col = "corr" if measure == "Correlation" else "beta"
    b = extras["betas"].copy()
    keep = apply_filters(feats.join(result["scores"][["score"]]))
    b = b[b["ticker"].isin(keep.index)]
    if b.empty:
        st.info("No companies match the filters.")
        return
    m = b.pivot_table(index="ticker", columns="commodity_name", values=col)
    order = list(config.CONFIG["features"]["commodities"].values())
    m = m[[c for c in order if c in m.columns]]
    info = feats.loc[m.index, ["industry"]].fillna("(no industry)")
    info["strength"] = m.abs().max(axis=1)
    info = info.sort_values(["industry", "strength"], ascending=[True, False])
    m = m.loc[info.index]
    ylab = [short(x) for x in m.index]
    lim = float(np.nanmax(np.abs(m.values))) if col == "beta" else 1.0
    lim = min(lim, 2.0) if col == "beta" else lim
    fig = go.Figure(go.Heatmap(
        z=m.values, x=list(m.columns), y=list(range(len(m))), colorscale=DIVERGING, zmid=0, zmin=-lim, zmax=lim,
        customdata=np.dstack([np.array([[i] * m.shape[1] for i in info["industry"]]),
                              np.array([[y] * m.shape[1] for y in ylab])]),
        hovertemplate="%{customdata[1]} (%{customdata[0]})<br>%{x}: %{z:.2f}<extra></extra>",
        xgap=2, ygap=1, colorbar=dict(title=measure, thickness=12, lenmode="pixels", len=280, y=1, yanchor="top")))
    # Industry separators and labels.
    starts = np.flatnonzero(np.r_[True, info["industry"].values[1:] != info["industry"].values[:-1]])
    for s_ in starts:
        fig.add_hline(y=s_ - 0.5, line_color=INK2, line_width=1)
        fig.add_annotation(x=-0.07, xref="paper", y=s_ - 0.4, yref="y", text=info["industry"].iloc[s_][:30],
                           showarrow=False, xanchor="right", yanchor="top", font=dict(size=10, color=INK2))
    fig.update_yaxes(tickmode="array", tickvals=list(range(len(m))), ticktext=ylab, autorange="reversed",
                     tickfont=dict(size=9))
    fig.update_xaxes(side="top")
    style(fig, max(400, 14 * len(m) + 120))
    fig.update_layout(margin=dict(l=250, r=10, t=40, b=10))
    st.plotly_chart(fig)
    top = feats.loc[m.index, ["NAME", "industry", "top_commodity", "top_commodity_beta", "top_commodity_corr"]]
    with st.expander("Strongest exposure per company (table)"):
        st.dataframe(top, width="stretch",
                     column_config={"top_commodity_beta": st.column_config.NumberColumn("Beta", format="%.2f"),
                                    "top_commodity_corr": st.column_config.NumberColumn("Corr", format="%.2f")})


# ---------------------------------------------------------------------------
# Page: Data quality
# ---------------------------------------------------------------------------
def page_quality():
    d, feats, result, _ = APP
    q = d["quality"]
    st.title("Data quality")
    st.caption("Data file: %s. Pulled %s." % (config.DATA_FILE, feats["asof"].max().date()))
    c = st.columns(4)
    c[0].metric("Universe", len(d["universe"]))
    c[1].metric("Scored", int(result["scores"]["score"].notna().sum()))
    c[2].metric("Excluded", len(q["excluded"]))
    c[3].metric("Broken fields", len(d["broken_fields"]))

    st.subheader("Excluded tickers (not scored)")
    st.dataframe(q["excluded"], hide_index=True, width="stretch")
    st.subheader("Broken fields (100 percent errors, dropped)")
    st.write(", ".join(d["broken_fields"]) + ". An inventory-to-sales measure from Fundamentals_Q replaces INVENTORY_TURNOVER.")

    st.subheader("Missing data by field (Snapshot)")
    m = q["snapshot_field_missing"].sort_values("pct_missing")
    m = m[m["pct_missing"] > 0]
    fig = go.Figure(go.Bar(x=100 * m["pct_missing"], y=m["field"], orientation="h", marker=dict(color=SERIES[0], cornerradius=4),
                           text=["%.1f%%" % (100 * v) for v in m["pct_missing"]], textposition="outside",
                           hovertemplate="%{y}: %{x:.1f}%<extra></extra>"))
    fig.update_xaxes(title="percent of universe tickers missing", range=[0, 110])
    st.plotly_chart(style(fig, 24 * len(m) + 80))

    st.subheader("Time series coverage")
    st.dataframe(q["ts_field_coverage"], hide_index=True, width="stretch",
                 column_config={"pct_companies_no_data": st.column_config.NumberColumn("Companies with no data", format="percent")})

    st.subheader("Missing data by company")
    tm = q["ticker_missing"].sort_values("pct_missing_snapshot", ascending=False)
    tm = tm.join(result["scores"][["completeness"]], on="ticker")
    st.dataframe(tm, hide_index=True, width="stretch", height=300,
                 column_config={"pct_missing_snapshot": st.column_config.NumberColumn("Snapshot fields missing", format="percent"),
                                "completeness": st.column_config.NumberColumn("Score data completeness", format="percent")})

    st.subheader("Field status from the Data_Dictionary")
    st.caption("Any field under %.0f percent missing counts as working, whatever its label."
               % (100 * config.CONFIG["loader"]["working_field_max_missing"]))
    st.dataframe(q["dictionary"][["group", "column_name", "sheet", "field_status", "pct_missing_snapshot", "working", "broken"]],
                 hide_index=True, width="stretch",
                 column_config={"pct_missing_snapshot": st.column_config.NumberColumn("Missing", format="percent")})

    st.subheader("Assumptions")
    st.markdown("- Missing dividend yield on a priced stock is treated as 0 (no dividend): **%d companies**.\n"
                "- P/E is never filled in (missing usually means negative earnings).\n"
                "- EV/EBITDA, P/E and P/B at or below zero are treated as missing.\n"
                "- Negative net debt / EBITDA counts as net cash only when EV/EBITDA is positive; otherwise missing (%d company).\n"
                "- News sentiment with under %d news days in the last 30 is left out: **%d companies**."
                % (len(q["div_assumed_zero"]), int(feats["nd_ebitda_ambiguous"].sum()),
                   config.CONFIG["features"]["sentiment_min_coverage"], int(feats["sent_low_coverage"].fillna(True).sum())))

    st.subheader("Supply chain")
    st.caption(TOP20_NOTE)
    cl = q["core_link_counts"].copy()
    cl["ticker"] = cl["ticker"].map(short)
    no_c = cl.loc[cl["customers"] == 0, "ticker"].tolist()
    st.markdown("Core names with **no customer links** (Bloomberg returns no quantified customers, not an error): "
                + ", ".join(no_c))
    L = d["links"]
    u = L.drop_duplicates("related_key")
    st.markdown("%d supplier links (both supplier tabs merged), %d customer links, %d unique related companies, "
                "%d in the universe (%.0f%%). %.0f%% of links are estimates."
                % ((L["direction"] == "Supplier").sum(), (L["direction"] == "Customer").sum(), len(u),
                   u["in_universe"].sum(), 100 * u["in_universe"].mean(), 100 * (L["source_type"] == "Estimate").mean()))
    with st.expander("Links per core company"):
        st.dataframe(cl, hide_index=True, width="stretch")
    st.markdown("**Link match review list** (fuzzy name matches, uncertain and unmatched related companies)")
    st.dataframe(d["link_review"], hide_index=True, width="stretch", height=300)


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------
APP = sidebar()
PAGES = {
    "rankings": st.Page(page_rankings, title="Rankings", url_path="rankings", default=True),
    "company": st.Page(page_company, title="Company", url_path="company"),
    "network": st.Page(page_network, title="Network", url_path="network"),
    "commodities": st.Page(page_commodities, title="Commodities", url_path="commodities"),
    "quality": st.Page(page_quality, title="Data quality", url_path="data-quality"),
}
st.navigation(list(PAGES.values()), position="top").run()
