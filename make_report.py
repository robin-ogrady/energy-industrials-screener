"""
make_report.py: write the printable report and the exports.

    .venv/bin/python make_report.py

writes, in the project folder:
    report.pdf       4 pages, US Letter, black-and-white friendly
                     p1 top 15 + bottom 5, p2 the core names,
                     p3 why the top 5 score well, p4 method and caveats
    scores.csv       one row per scored company (scores, ranks, factor
                     groups; no raw Bloomberg fields)
    methodology.md   one page: every factor, its direction and weight

The dashboard's "Download report" button calls build_pdf() with the
current weights, options and filters.

Licensed data: the PDF shows only scores, ranks, derived factors and a
few headline numbers inside the plain-English reasons, never raw tables.
"""
import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import letter  # noqa: E402
from reportlab.lib.styles import ParagraphStyle  # noqa: E402
from reportlab.lib.units import inch  # noqa: E402
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph,  # noqa: E402
                                SimpleDocTemplate, Spacer, Table, TableStyle)

import config  # noqa: E402
import features  # noqa: E402
import loader  # noqa: E402
import network  # noqa: E402
import scoring  # noqa: E402

OUT_PDF = config.PROJECT_DIR / "report.pdf"
OUT_CSV = config.PROJECT_DIR / "scores.csv"
OUT_MD = config.PROJECT_DIR / "methodology.md"

NOTE = "Not validated. A screening tool, not a trading signal."
# Result of the pre-registered backtest (github.com/robin-ogrady/energy-industrials-backtest).
BACKTEST_URL = "https://github.com/robin-ogrady/energy-industrials-backtest"
BACKTEST_NOTE = ("A pre-registered backtest (S&P 1500 Energy and Industrials, 2016 to September 2026) did not support "
                 "this score: its link to next-month returns was too weak to rule out luck (top minus bottom fifth "
                 "after costs, p = 0.078). The supply chain part could not be tested.")
NOISE = "Gaps of 0.1 to 0.2 points between neighbors are noise: read the rank band and the drivers, not the decimals."


def score_band(scores):
    """'about 3.5 and 7.5': where the scores actually sit, rounded to 0.5."""
    s = scores.dropna()
    if s.empty:
        return "n/a"
    return "about %.1f and %.1f" % (np.floor(2 * s.min()) / 2, np.ceil(2 * s.max()) / 2)


def cluster_note(scores):
    return ("Scores are not stretched, so they cluster between %s. " % score_band(scores)) + NOISE


def link_stats(d, feats=None):
    """Numbers used in the supply chain caveats, computed from the data."""
    L = d["links"]
    n_core = int(d["universe"]["is_core"].sum())
    if L is None or L.empty:
        return dict(n_core=n_core, n_related=0, n_in=0, share_in=0.0, share_est=0.0, n_no_customer=n_core)
    u = L.drop_duplicates("related_key")
    cl = d["quality"]["core_link_counts"]
    return dict(n_core=n_core, n_related=len(u), n_in=int(u["in_universe"].sum()),
                share_in=float(u["in_universe"].mean()), share_est=float((L["source_type"] == "Estimate").mean()),
                n_no_customer=int((cl["customers"] == 0).sum()))

# Greys only, so it prints well in black and white.
INK, DARK, MID, LIGHT, PALE = "#000000", "#3a3a3a", "#7a7a7a", "#c8c8c8", "#efefef"

PAGE_W, PAGE_H = letter
MARGIN = 0.55 * inch
FRAME_W = PAGE_W - 2 * MARGIN
FRAME_H = PAGE_H - 2 * MARGIN - 0.25 * inch   # room for the footer

# Text styles (body text never below 8 pt).
ST = {
    "title": ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=17, leading=21, spaceAfter=2),
    "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12, leading=15, spaceBefore=4, spaceAfter=4),
    "h3": ParagraphStyle("h3", fontName="Helvetica-Bold", fontSize=9.5, leading=12, spaceAfter=2),
    "body": ParagraphStyle("body", fontName="Helvetica", fontSize=9, leading=11.5),
    "small": ParagraphStyle("small", fontName="Helvetica", fontSize=8, leading=10, textColor=colors.HexColor(DARK)),
    "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=8, leading=9.5),
    "bullet": ParagraphStyle("bullet", fontName="Helvetica", fontSize=8.5, leading=10.5, leftIndent=10, bulletIndent=0),
    "note": ParagraphStyle("note", fontName="Helvetica-Bold", fontSize=9, leading=11),
}


# ===========================================================================
# Data
# ===========================================================================
def compute(weights=None, options=None, path=None):
    """Load the cached data and score it. Returns (d, feats, extras, result)."""
    d = loader.load_data(path=path)
    feats, extras = features.build_features(d)
    result = scoring.score_all(feats, d["links"], weights, options)
    return d, feats, extras, result


def caveat_badge(text):
    """Short badge for tables; the full reason is on the company page / p4."""
    if not text:
        return ""
    bits = []
    if "ranked against all companies" in text:
        bits.append("all-company peers")
    if any(v in text for v in config.CONFIG["peer_group_caveats"].values()):
        bits.append("poor peer group")
    if "extreme value" in text:
        bits.append("extreme ratio")
    return ", ".join(bits)


def ranking_frame(feats, result):
    """One row per scored company, sorted by score (best first)."""
    s = result["scores"]
    t = feats[["NAME", "chain", "sector", "industry", "is_core"]].join(s)
    t["ticker"] = [network.short(x) for x in t.index]
    t["linked_coverage"] = np.where(t["is_core"], t["linked_coverage"].str.replace(" scored", ""), "")
    t["caveat_badge"] = t["peer_caveat"].map(caveat_badge)
    return t.sort_values("score", ascending=False)


def filter_table(t, filters=None):
    """Apply the dashboard filters: chain, sector, industry, core_only."""
    f = filters or {}
    if f.get("chain"):
        t = t[t["chain"].isin(f["chain"])]
    if f.get("sector"):
        t = t[t["sector"].isin(f["sector"])]
    if f.get("industry"):
        t = t[t["industry"].isin(f["industry"])]
    if f.get("core_only"):
        t = t[t["is_core"]]
    return t


def describe_filters(filters):
    f = filters or {}
    parts = []
    for k, lab in [("chain", "chain"), ("sector", "sector"), ("industry", "industry")]:
        if f.get(k):
            parts.append("%s: %s" % (lab, ", ".join(f[k])))
    if f.get("core_only"):
        parts.append("core names only")
    return "; ".join(parts)


def weights_text(result, options=None):
    """'Health 20%, Risk 15%, ...' plus the capex setting."""
    opts = options or {}
    capex = opts.get("use_capex_intensity", config.CONFIG["scoring"]["use_capex_intensity"])
    bits = []
    for g, w in result["weights"].items():
        lab = g
        if g == "Demand pipeline":
            lab += " (capex intensity %s)" % ("on" if capex else "off")
        if g == config.LINKED_GROUP:
            lab += " (core names only)"
        bits.append("%s %.0f%%" % (lab, 100 * w))
    return ", ".join(bits)


# ===========================================================================
# Charts (matplotlib, greyscale)
# ===========================================================================
plt.rcParams.update({"font.family": "Helvetica", "font.size": 8, "axes.edgecolor": MID,
                     "axes.labelcolor": DARK, "xtick.color": DARK, "ytick.color": DARK})


def to_image(fig, width_in):
    """Matplotlib figure -> reportlab Image of the given width."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=220, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    img = Image(buf)
    # Natural size (pixels / dpi) so 8 pt chart text prints at 8 pt; only
    # shrink if the picture is wider than the space it must fit in.
    w, h = img.imageWidth / 220.0, img.imageHeight / 220.0
    scale = min(1.0, width_in / w)
    img.drawWidth, img.drawHeight = w * scale * inch, h * scale * inch
    return img


def chart_top(df, width_in=7.2, height_in=None):
    """Horizontal bars: score of the top companies, on the full 1-10 scale.
    height_in: total picture height to aim for (page 1 passes the space left)."""
    df = df.iloc[::-1]
    h = 0.145 * len(df) + 0.55 if height_in is None else max(1.4, height_in - 0.15)
    fig, ax = plt.subplots(figsize=(6.9, h))
    y = np.arange(len(df))
    ax.barh(y, df["score"] - 1, left=1, color=DARK, height=0.62)
    for yi, v in zip(y, df["score"]):
        ax.text(v + 0.06, yi, "%.1f" % v, va="center", fontsize=8, color=INK)
    ax.set_yticks(y)
    ax.set_yticklabels(df["ticker"], fontsize=8)
    ax.set_xlim(1, 10)
    ax.axvline(5.5, color=MID, linestyle="--", linewidth=0.8)
    ax.text(5.55, len(df) - 0.4, "neutral 5.5", fontsize=8, color=MID, va="bottom")
    ax.set_xlabel("Score (1 to 10)", fontsize=8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.grid(axis="x", color=PALE, linewidth=0.6)
    ax.set_axisbelow(True)
    return to_image(fig, width_in)


def chart_groups(points, width_in=2.7):
    """Points each factor group added to / took from a neutral 5.5.
    Up = solid dark bar, down = light hatched bar (readable in B&W)."""
    p = points.dropna()
    labels = [("Linked cos." if g == config.LINKED_GROUP else g) for g in p.index][::-1]
    vals = p.values[::-1]
    fig, ax = plt.subplots(figsize=(1.75, 0.145 * len(p) + 0.3))   # labels add ~0.9 in
    y = np.arange(len(p))
    for yi, v in zip(y, vals):
        if v >= 0:
            ax.barh(yi, v, color=DARK, height=0.6)
        else:
            ax.barh(yi, v, color="white", edgecolor=DARK, hatch="////", height=0.6, linewidth=0.6)
        ax.text(v + (0.02 if v >= 0 else -0.02), yi, "%+.2f" % v, va="center",
                ha="left" if v >= 0 else "right", fontsize=8)
    lim = max(0.3, 1.45 * np.abs(vals).max())
    ax.set_xlim(-lim, lim)
    ax.axvline(0, color=INK, linewidth=0.8)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=8)
    ax.tick_params(axis="x", labelsize=8)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    return to_image(fig, width_in)


def chart_network(G, center, width_in=4.6):
    """Small ego network for one company: greys and line styles only."""
    sub = network.ego_subgraph(G, center)
    pos = network.layout(sub, seed=3)
    fig, ax = plt.subplots(figsize=(4.4, 3.0))
    style = {"COGS": (MID, "-", 1.0), "CAPEX": (INK, "-", 1.6), "SGA": (LIGHT, ":", 1.0), "Missing": (MID, "--", 1.0)}
    for u, v, e in sub.edges(data=True):
        c, ls, lw = style.get(e["cost_type"], style["Missing"])
        ax.plot([pos[u][0], pos[v][0]], [pos[u][1], pos[v][1]], color=c, linestyle=ls, linewidth=lw, zorder=1)
    for n, a in sub.nodes(data=True):
        x, y = pos[n]
        if pd.notna(a["score"]):
            shade = str(0.85 - 0.75 * np.clip((a["score"] - 3.5) / 4, 0, 1))   # darker = higher score
            ax.scatter(x, y, s=90 if n != center else 160, color=shade, edgecolor=INK, zorder=3, linewidth=0.8)
            ax.text(x, y + 0.07, a["label"], ha="center", fontsize=8, zorder=4)
        else:
            ax.scatter(x, y, s=30, facecolor="white", edgecolor=MID, zorder=2, linewidth=0.8)
    for lab, (c, ls, lw) in style.items():
        ax.plot([], [], color=c, linestyle=ls, linewidth=lw, label="%s link" % lab)
    ax.scatter([], [], s=30, facecolor="white", edgecolor=MID, label="not in universe")
    ax.legend(fontsize=8, frameon=False, loc="lower center", bbox_to_anchor=(0.5, -0.18), ncol=5)
    ax.set_axis_off()
    return to_image(fig, width_in)


# ===========================================================================
# PDF pages
# ===========================================================================
def table_style(n_rows, extra=None):
    base = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", 8),
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.black),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor(MID)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 1.6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.6),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]
    for r in range(2, n_rows, 2):
        base.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor(PALE)))
    return TableStyle(base + (extra or []))


def trim(s, n):
    s = str(s) if isinstance(s, str) else ""
    return s if len(s) <= n else s[: n - 1] + "…"


LOWER_WORDS = {"INC", "CO", "LTD", "CORP", "THE", "AND", "OF", "GROUP", "HOLDINGS", "INTL", "SYS", "SVC", "LP"}


def nice_name(s, n=27):
    """Bloomberg names are upper case. Title-case them but keep short
    acronyms (EOG, SLB, GE, BWX, PLC) in capitals."""
    if not isinstance(s, str):
        return ""
    words = []
    for w in s.split():
        core = "".join(ch for ch in w if ch.isalnum())
        if core.upper() in LOWER_WORDS or len(core) > 3:
            words.append(w.title().replace("'S", "'s"))
        else:
            words.append(w.upper())
    return trim(" ".join(words), n)


name_case = nice_name


def score_cell(r):
    return "%.1f%s" % (r["score"], "*" if r["low_data"] else "")


def ticker_cell(r):
    return r["ticker"] + ("†" if r["peer_caveat"] else "")


def page1(t_all, shown, d, result, filters, options):
    out = []
    asof = d["snapshot"]["asof"].max()
    n_scored, n_uni = int(t_all["score"].notna().sum()), len(d["universe"])
    out.append(Paragraph("Energy &amp; Industrials Screener: Results", ST["title"]))
    sub = "Data as of %s &nbsp;·&nbsp; %d of %d companies scored (%d excluded: no price data)" % (
        asof.date(), n_scored, n_uni, n_uni - n_scored)
    fdesc = describe_filters(filters)
    if fdesc:
        sub += "<br/>Filters: %s (%d companies shown). Ranks are among all %d scored companies." % (fdesc, len(shown), n_scored)
    out.append(Paragraph(sub, ST["body"]))
    out.append(Spacer(1, 5))
    box = Table([[Paragraph(NOTE, ST["note"])]], colWidths=[FRAME_W])
    box.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, colors.black),
                             ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    out.append(box)
    out.append(Spacer(1, 3))
    out.append(Paragraph("<b>Backtest result:</b> " + BACKTEST_NOTE.replace("&", "&amp;"), ST["small"]))
    out.append(Spacer(1, 6))

    top = shown.head(15)
    bottom = shown.tail(5) if len(shown) > 20 else shown.iloc[0:0]
    head = ["Rank", "Percentile", "Ticker", "Name", "Industry", "Score", "Data", "Top 3 drivers"]
    rows = [head]
    for _, r in top.iterrows():
        rows.append(row1(r))
    sep = None
    if len(bottom):
        rows.append(["…", "", "", "Bottom 5", "", "", "", ""])
        sep = len(rows) - 1
        for _, r in bottom.iterrows():
            rows.append(row1(r))
    widths = [0.36, 0.7, 0.48, 1.42, 1.3, 0.4, 0.4, 2.34]
    extra = [("ALIGN", (0, 0), (1, -1), "RIGHT"), ("ALIGN", (5, 0), (6, -1), "RIGHT")]
    if sep:
        extra += [("FONT", (0, sep), (-1, sep), "Helvetica-Oblique", 8), ("BACKGROUND", (0, sep), (-1, sep), colors.white),
                  ("LINEABOVE", (0, sep), (-1, sep), 0.5, colors.HexColor(MID)),
                  ("LINEBELOW", (0, sep), (-1, sep), 0.5, colors.HexColor(MID))]
    tb = Table(rows, colWidths=[w * inch for w in widths], repeatRows=1)
    tb.setStyle(table_style(len(rows), extra))
    out.append(tb)
    out.append(Spacer(1, 3))
    out.append(Paragraph(
        "Drivers = the three factor groups that moved the score most (+ pushed it up, - pulled it down). "
        "* built on under 60% of the data. † peer group caveat (see pages 2 and 4). "
        "Percentile = rank among all scored companies (top half shown as top X%, bottom half as bottom X%). " + NOISE, ST["small"]))
    out.append(Spacer(1, 6))
    out.append(Paragraph("Top 15 scores", ST["h3"]))
    tail = [Spacer(1, 4),
            Paragraph("<b>Weights used:</b> %s. Weights are rescaled per company over the groups it has data for. "
                      "<b>Data file:</b> %s." % (weights_text(result, options), config.DATA_FILE), ST["small"])]
    # The chart gets whatever height is left, so page 1 always stays one page.
    left_in = (FRAME_H - 14 - sum(flow_height(f) for f in out + tail)) / 72.0
    out.append(chart_top(top, 7.0, min(left_in, 0.145 * len(top) + 0.55)))
    return out + tail


def row1(r):
    return ["%d" % r["rank"], r["top_pct_label"], ticker_cell(r), nice_name(r["NAME"], 23),
            trim(r["industry"], 23), score_cell(r), "%.0f%%" % (100 * r["completeness"]),
            Paragraph(r["drivers"], ST["cell"])]     # wraps instead of running past the margin


def page2(t_all, shown, d):
    core = shown[shown["is_core"]].sort_values("rank")
    n_scored = int(t_all["score"].notna().sum())
    ls = link_stats(d)
    out = [Paragraph("Core names (%d)" % ls["n_core"], ST["h2"])]
    out.append(Paragraph(
        "The %d core names, ranked among all %d scored companies. Linked coverage = linked suppliers and customers "
        "that have a score, out of all linked companies in their top-20 lists. Linked companies has limited coverage: "
        "only %.0f%% of linked companies are in the universe. " % (ls["n_core"], n_scored, 100 * ls["share_in"])
        + NOISE, ST["body"]))
    out.append(Spacer(1, 6))
    if core.empty:
        out.append(Paragraph("No core names match the current filters.", ST["body"]))
        return out
    rows = [["Rank", "Percentile", "Ticker", "Name", "Industry", "Score", "Data", "Linked cov.", "Caveat"]]
    for _, r in core.iterrows():
        rows.append(["%d" % r["rank"], r["top_pct_label"], r["ticker"], name_case(r["NAME"]),
                     trim(r["industry"], 27), score_cell(r), "%.0f%%" % (100 * r["completeness"]),
                     r["linked_coverage"], Paragraph(r["caveat_badge"], ST["cell"])])
    widths = [0.38, 0.72, 0.5, 1.55, 1.55, 0.42, 0.4, 0.68, 1.2]
    tb = Table(rows, colWidths=[w * inch for w in widths], repeatRows=1)
    tb.setStyle(table_style(len(rows), [("ALIGN", (0, 0), (1, -1), "RIGHT"), ("ALIGN", (5, 0), (7, -1), "RIGHT")]))
    out.append(tb)
    out.append(Spacer(1, 4))
    out.append(Paragraph("* built on under 60% of the data. Caveat details are on page 4. Ranks and percentiles "
                         "move when the weights change.", ST["small"]))
    return out


def page3(shown, feats, result, d):
    out = [Paragraph("Top 5: why they score well", ST["h2"]),
           Paragraph("Bars show how many points each factor group added to (solid) or took from (hatched) a "
                     "neutral 5.5. Reasons are generated from the actual numbers (+ helped the score, – hurt it); "
                     "percentages compare the company with its peer group.", ST["body"]),
           Spacer(1, 6)]
    for i, (t, r) in enumerate(shown.head(5).iterrows(), 1):
        ex = scoring.explain(result, t, n_reasons=3)
        # Two reasons that pushed the score up (+) and one that pulled it down (-).
        reasons = [("+", x) for x in ex["reasons_up"][:2]] + [("–", x) for x in ex["reasons_down"][:1]]
        head = Paragraph("%d. %s &nbsp;%s &nbsp;·&nbsp; %s" % (i, r["ticker"], name_case(r["NAME"]), r["industry"]), ST["h3"])
        sub = Paragraph("Score <b>%.1f</b>%s (rank %d, %s) &nbsp;·&nbsp; data %.0f%%%s" % (
            r["score"], "*" if r["low_data"] else "", r["rank"], r["top_pct_label"], 100 * r["completeness"],
            (" &nbsp;·&nbsp; caveat: " + caveat_badge(r["peer_caveat"])) if r["peer_caveat"] else ""), ST["small"])
        bullets = [Paragraph(x, ST["bullet"], bulletText=sign) for sign, x in reasons] or [Paragraph("(no clear reasons)", ST["body"])]
        right = [head, sub, Spacer(1, 3)] + bullets
        panel = Table([[chart_groups(ex["groups"]["points"].reindex(list(config.METRICS)), 2.6), right]],
                      colWidths=[2.75 * inch, FRAME_W - 2.75 * inch])
        panel.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor(LIGHT)),
                                   ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 4)]))
        out.append(KeepTogether(panel))
    # Network figure for the top company (placed by build_pdf where it fits).
    net = []
    top_t = shown.index[0] if len(shown) else None
    if top_t is not None:
        G = network.build_graph(d["links"], result["scores"], feats)
        if top_t in G and G.degree(top_t) > 0:
            net = [Paragraph("Supply chain links of the top company, %s (top 20 suppliers and customers only)"
                             % network.short(top_t), ST["h3"]),
                   chart_network(G, top_t, 4.2),
                   Paragraph("Filled = in the universe (darker = higher score); hollow = not in the universe. Black "
                             "lines = CAPEX links (the customer's capital spending is the company's revenue).", ST["small"]),
                   Spacer(1, 8)]
        else:
            out.append(Spacer(1, 4))
            out.append(Paragraph("%s has no supply chain links (links were pulled only for the core names), so "
                                 "there is no network figure." % network.short(top_t), ST["small"]))
    return out, net


def page4(t_all, feats, result, d, options, short_version=False):
    q = d["quality"]
    L = d["links"]
    u = L.drop_duplicates("related_key")
    core = t_all[t_all["is_core"]]
    cov = result["scores"].loc[core.index, ["n_related_scored", "n_related"]]
    opts = options or {}
    capex = opts.get("use_capex_intensity", config.CONFIG["scoring"]["use_capex_intensity"])
    cav = t_all[t_all["peer_caveat"] != ""]
    out = [Paragraph("Method and caveats", ST["h2"])]
    out.append(Paragraph(
        "<b>Method.</b> Each metric is ranked as a percentile within the company's GICS industry (its sector when fewer "
        "than 8 industry peers have the number, all companies as a last resort), after clipping outliers at the 5th and "
        "95th percentile; for lower-is-better metrics the rank is flipped. Metrics are averaged into factor groups. "
        "Pass 1 builds a standalone score from every group except Linked companies. Pass 2 sets Linked companies to the "
        "exposure-weighted average standalone score of a core company's scored suppliers and customers (SGA links count "
        "25%). The final score is the weighted average of the groups a company has, scaled to 1 + 9 x average, so 5.5 "
        "is neutral and 10 would mean best in its peer group on every metric. It is a current snapshot: no historical "
        "scores, no backtest, and no claim that it predicts returns.", ST["body"]))
    out.append(Spacer(1, 6))
    items = [
        "<b>Backtest:</b> " + BACKTEST_NOTE.replace("&", "&amp;") + " Details: " + BACKTEST_URL.replace("https://", "") + ".",
        "<b>Excluded:</b> %d tickers with no price data, probably acquired, merged or renamed: %s." % (
            len(q["excluded"]), ", ".join(network.short(x) for x in q["excluded"]["ticker"])),
        "<b>Broken fields dropped:</b> %s (100%% errors). Inventory-to-sales from quarterly data replaces inventory turnover."
        % ", ".join(d["broken_fields"]),
        "<b>Assumptions:</b> missing dividend yield on a priced stock treated as 0 (no dividend) for %d companies; P/E "
        "never filled in; EV/EBITDA, P/E and P/B at or below zero treated as missing; news sentiment left out for %d "
        "companies with under %d news days in 30." % (
            len(q["div_assumed_zero"]), int(feats["sent_low_coverage"].sum()),
            config.CONFIG["features"]["sentiment_min_coverage"]),
        "<b>Capex intensity is %s</b> in this report%s. Its direction is debatable (heavy investment can signal "
        "expected demand or just a capital-hungry business), and the capex estimate field could not be used because "
        "its period is unclear." % ("ON" if capex else "OFF", "" if capex else " (the default); Demand pipeline uses only "
                                    "the two inventory measures"),
        "<b>Supply chain:</b> links are each core company's top 20 suppliers and customers only, so a missing link does "
        "not mean no relationship. %.0f%% of link figures are Bloomberg estimates. Only %d of %d linked companies "
        "(%.0f%%) are in the universe; across the core names a median of %d of %d linked companies has a score. "
        "%d core names have no customer links at all. Treat the Linked companies group as limited coverage." % (
            100 * (L["source_type"] == "Estimate").mean(), u["in_universe"].sum(), len(u), 100 * u["in_universe"].mean(),
            cov["n_related_scored"].median(), cov["n_related"].median(), link_stats(d)["n_no_customer"]),
        "<b>Peer group caveats (%d companies):</b> ranked against all companies because industry and sector are too "
        "small (%s); poor peer group (%s); extreme ratios (%d companies, e.g. %s)." % (
            len(cav),
            ", ".join(network.short(x) for x in cav.index if "ranked against all" in cav.at[x, "peer_caveat"]) or "none",
            ", ".join(network.short(x) for x in config.CONFIG["peer_group_caveats"] if x in cav.index) or "none",
            int(cav["peer_caveat"].str.contains("extreme value").sum()), extreme_example(cav)),
        "<b>Scores cluster</b> between %s because averaging percentiles pulls toward the middle; they are "
        "not stretched. " % score_band(t_all["score"]) + NOISE,
        "<b>Labels:</b> GICS industry and sector come from Bloomberg; many universe names were chosen from memory, "
        "and some peer groups mix very different businesses.",
    ]
    if short_version:
        items = items[:3] + items[4:6] + items[7:8]
    for x in items:
        out.append(Paragraph(x, ST["bullet"], bulletText="•"))
        out.append(Spacer(1, 3))
    return out


def flow_height(f):
    """Height a flowable needs on the page (KeepTogether: its contents)."""
    if isinstance(f, KeepTogether):
        return sum(flow_height(x) for x in f._content)
    _, h = f.wrap(FRAME_W, FRAME_H)
    return h + f.getSpaceBefore() + f.getSpaceAfter()


def extreme_example(cav):
    """First extreme-value caveat, e.g. 'XYZ price / book 400.0x', read from the data."""
    for t, text in cav["peer_caveat"].items():
        for part in text.split("; "):
            if part.startswith("extreme value: "):
                return "%s %s" % (network.short(t), part.replace("extreme value: ", "").lower())
    return "none"


def fits_one_page(flowables):
    """True if the flowables fit in one page frame (small safety margin)."""
    return sum(flow_height(f) for f in flowables) <= FRAME_H - 12


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor(DARK))
    canvas.drawString(MARGIN, 0.4 * inch, "Energy & Industrials Screener  ·  %s  ·  %s" % (NOTE, config.DATA_FILE))
    canvas.drawRightString(PAGE_W - MARGIN, 0.4 * inch, "Page %d" % doc.page)
    canvas.restoreState()


def build_pdf(d, feats, result, filters=None, options=None, out_path=None):
    """Build the PDF and return its bytes (also written to out_path if given)."""
    t_all = ranking_frame(feats, result)
    shown = filter_table(t_all, filters)
    shown = shown[shown["score"].notna()]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=MARGIN, rightMargin=MARGIN,
                            topMargin=MARGIN, bottomMargin=MARGIN + 0.25 * inch,
                            title="Energy & Industrials Screener: Results", author="Screener")
    story = page1(t_all, shown, d, result, filters, options)
    story += [PageBreak()] + page2(t_all, shown, d)
    p3, net = page3(shown, feats, result, d)
    story += [PageBreak()] + p3
    # Page 3 if it still has room, else the top of page 4, else its own page.
    if net and fits_one_page(p3 + net):
        story += net
        net = []
    p4 = page4(t_all, feats, result, d, options)
    if not fits_one_page(net + p4):
        p4 = page4(t_all, feats, result, d, options, short_version=True)
    if net and not fits_one_page(net + p4):
        story += [PageBreak()] + net
        net = []
    if fits_one_page(net + p4):     # caveats page only if it fits on one page
        story += [PageBreak()] + net + p4
    elif net:
        story += [PageBreak()] + net
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    pdf = buf.getvalue()
    if out_path:
        with open(out_path, "wb") as f:
            f.write(pdf)
    return pdf


# ===========================================================================
# scores.csv and methodology.md
# ===========================================================================
def write_scores_csv(feats, result, path=OUT_CSV):
    """Scores, ranks and factor groups only (no raw Bloomberg fields)."""
    t = ranking_frame(feats, result)
    gv = result["group_values"].add_prefix("pct_")          # 0..1 group values
    pts = result["contributions"].add_prefix("points_")     # points vs 5.5
    cols = ["ticker", "NAME", "sector", "industry", "chain", "is_core", "rank", "top_pct", "score", "standalone_score",
            "completeness", "low_data", "drivers", "linked_coverage", "peer_caveat"]
    out = t[cols].join(gv).join(pts).round(4)
    out.index.name = "bloomberg_ticker"
    out.to_csv(path)
    return path


def write_methodology(result, options=None, path=OUT_MD):
    """One page explaining every factor, its direction and its weight."""
    opts = options or {}
    capex = opts.get("use_capex_intensity", config.CONFIG["scoring"]["use_capex_intensity"])
    sc = config.CONFIG["scoring"]
    lines = [
        "# Methodology",
        "",
        "Energy & Industrials Supply Chain Screener. Data: `%s`. **%s** A current snapshot: no historical scores, "
        "no backtest, no prediction." % (config.DATA_FILE, NOTE),
        "",
        "## How a score is built",
        "1. **Clean.** Bloomberg errors and `--` become missing. Missing dividend yield on a priced stock = 0 (flagged). "
        "P/E is never filled in. Multiples at or below zero are missing.",
        "2. **Clip outliers** at the %dth and %dth percentile of all scored companies (winsorizing)."
        % (100 * sc["winsor_pct"][0], 100 * sc["winsor_pct"][1]),
        "3. **Rank** each metric as a percentile (0 = worst, 1 = best) within the GICS industry; sector if fewer than "
        "%d industry peers have the number; all companies as a last resort. Lower-is-better metrics are flipped."
        % sc["min_peers"],
        "4. **Group value** = average percentile of the metrics the company has in that group.",
        "5. **Pass 1, standalone score:** weighted average of every group except Linked companies.",
        "6. **Pass 2, Linked companies:** for the core names, the exposure-weighted average *standalone* score of "
        "their scored suppliers and customers. Weight = exposure %% x cost-category weight (COGS and CAPEX 1.0, "
        "SGA %.2f, missing 1.0 but flagged). Using standalone scores avoids circular scores." % sc["cost_type_weights"]["SGA"],
        "7. **Final score** = 1 + 9 x weighted average of the groups the company has (weights renormalized). "
        "5.5 = neutral. Each group's contribution = 9 x weight x (group value - 0.5) points.",
        "8. **Flags:** data completeness under %d%% gets an asterisk; peer group caveats (all-company ranking, poor "
        "peer group, extreme ratios) get a badge." % (100 * sc["min_completeness"]),
        "",
        "## Factors, directions and weights",
        "",
        "| Group (weight) | Metric | Better when |",
        "|---|---|---|",
    ]
    for g, ms in config.METRICS.items():
        for i, m in enumerate(ms):
            gl = "%s (%.0f%%)" % (g, 100 * result["weights"][g]) if i == 0 else ""
            name = m["label"]
            if m["key"] == "capex_intensity":
                name += " **(%s)**" % ("on" if capex else "off by default")
            better = "higher" if m["direction"] > 0 else "lower"
            if g == config.LINKED_GROUP:
                better = "linked companies score higher"
            lines.append("| %s | %s | %s |" % (gl, name, better))
    lines += [
        "",
        "## Notes",
        "- **Capex intensity is %s.** Capex / last 4 quarters of sales (higher = better) is off by default because "
        "its direction is debatable and the capex estimate field could not be used (unclear period). Turn it on in "
        "`config.py` (`use_capex_intensity`) or with the dashboard toggle." % ("ON" if capex else "OFF"),
        "- **Sentiment** uses only days with news (no news is not zero). Under %d news days in 30 = left out. "
        "Headline scoring (Loughran-McDonald) is off by default and never enters the score unless switched on."
        % config.CONFIG["features"]["sentiment_min_coverage"],
        "- **Supply chain** links are each core name's top 20 only and mostly Bloomberg estimates, and many linked "
        "companies are outside the universe, so Linked companies has limited coverage.",
        "- **Commodity betas**, supplier dependence and news attention are shown on the dashboard but not scored.",
        "- **Backtest:** " + BACKTEST_NOTE + " Rules, code and verdict: " + BACKTEST_URL,
        "- Scores are not stretched to fill 1 to 10, so they cluster near the middle. " + NOISE,
    ]
    path.write_text("\n".join(lines) + "\n")
    return path


def main():
    d, feats, extras, result = compute()
    build_pdf(d, feats, result, out_path=OUT_PDF)
    write_scores_csv(feats, result)
    write_methodology(result)
    for p in (OUT_PDF, OUT_CSV, OUT_MD):
        print("wrote", p)


if __name__ == "__main__":
    main()
