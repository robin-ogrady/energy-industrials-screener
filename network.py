"""
network.py: the supplier / customer graph (networkx) and its Plotly figure.

Nodes  = the core companies plus every company linked to them.
         A related company that matched the universe uses its universe
         ticker, so a company linked to several core names is ONE node.
Edges  = money flow: supplier -> main company -> customer.
         width = exposure_pct (binned), color + dash = cost category.
Colors = score on a one-hue blue scale (darker = higher score).
         Companies outside the universe have no score: hollow grey circles.
Size   = market cap on a log scale (default size if unknown).

Remember: links are the top 20 per company only.
"""
import math

import networkx as nx
import numpy as np
import pandas as pd
import plotly.graph_objects as go

# Colors (validated reference palette, see the dataviz notes in README).
SCORE_SCALE = [[0.0, "#cde2fb"], [0.25, "#86b6ef"], [0.5, "#3987e5"], [0.75, "#1c5cab"], [1.0, "#0d366b"]]
NO_SCORE = "#a8a7a3"
TEXT = "#0b0b0b"
TEXT_MUTED = "#52514e"
# Cost category -> (color, dash). CAPEX stands out: capex-driven revenue.
EDGE_STYLE = {
    "COGS": ("#52514e", "solid"),
    "CAPEX": ("#eb6834", "solid"),
    "SGA": ("#a8a7a3", "dot"),
    "Missing": ("#4a3aa7", "dash"),
}
# Exposure bins (fractions) -> line width.
WIDTH_BINS = [(0.0, 0.001, 0.6), (0.001, 0.005, 1.2), (0.005, 0.02, 2.2), (0.02, 10.0, 3.6)]


def node_id(row):
    """Universe ticker if matched, else the raw related key."""
    t = row.get("universe_ticker")
    return t if isinstance(t, str) else row["related_key"]


def short(t):
    """'SLB US Equity' -> 'SLB'."""
    return t.replace(" US Equity", "").replace(" Equity", "") if isinstance(t, str) else t


def build_graph(links, scores, feats):
    """Directed graph from the links table.

    scores: DataFrame indexed by ticker with a 'score' column.
    feats:  DataFrame indexed by ticker with NAME and CUR_MKT_CAP.
    """
    G = nx.DiGraph()
    if links is None or links.empty:
        return G
    sc = scores["score"] if scores is not None and "score" in scores else pd.Series(dtype=float)

    def add_node(nid, name, in_uni, is_main):
        if nid in G:
            if is_main:
                G.nodes[nid]["is_core"] = True
            return
        G.add_node(nid, name=name, in_universe=in_uni, is_core=is_main,
                   score=sc.get(nid, np.nan) if in_uni else np.nan,
                   mkt_cap=feats["CUR_MKT_CAP"].get(nid, np.nan) if in_uni and nid in feats.index else np.nan,
                   label=short(nid) if in_uni else (name or nid))

    for _, r in links.iterrows():
        main = r["main_ticker"]
        add_node(main, feats["NAME"].get(main, main) if main in feats.index else main, True, True)
        other = node_id(r)
        add_node(other, r.get("related_name"), bool(r.get("in_universe")), False)
        u, v = (other, main) if r["direction"] == "Supplier" else (main, other)
        if G.has_edge(u, v):
            continue  # same flow listed from both ends: keep the first
        G.add_edge(u, v, direction=r["direction"], main=main,
                   exposure=r.get("exposure_pct"), reverse=r.get("reverse_pct"),
                   cost_type=r["cost_type"] if isinstance(r.get("cost_type"), str) else "Missing",
                   source_type=r.get("source_type"), as_of_year=r.get("as_of_year"),
                   value=r.get("relationship_value_usd_mm"))
    return G


def layout(G, seed=7):
    """Spring layout (fixed seed, so the picture is the same every run)."""
    if len(G) == 0:
        return {}
    k = 3.0 / math.sqrt(len(G))   # larger k = nodes pushed further apart
    return nx.spring_layout(G.to_undirected(), k=k, iterations=80, seed=seed)


def node_size(cap):
    """Marker size from market cap (log scale); 9 px if unknown."""
    if not cap or not np.isfinite(cap) or cap <= 0:
        return 9
    return float(np.clip(6 + 4.5 * (math.log10(cap) - 9), 7, 30))   # $1bn -> 6, $1tn -> 19.5


def width_for(exposure):
    if exposure is None or not np.isfinite(exposure):
        return WIDTH_BINS[0][2]
    for lo, hi, w in WIDTH_BINS:
        if lo <= exposure < hi:
            return w
    return WIDTH_BINS[-1][2]


def figure(G, pos, highlight=None, height=720, label_all=False):
    """Plotly figure. highlight = a node id: it and its neighbors stay
    bright, everything else is faded. Labels: core names always; scored
    neighbors of the highlighted company; every node if label_all (small
    graphs). Unlabeled nodes still show their name on hover."""
    fig = go.Figure()
    if len(G) == 0:
        fig.add_annotation(text="No supply chain links", showarrow=False)
        return fig
    keep = None
    if highlight in G:
        keep = {highlight} | set(G.predecessors(highlight)) | set(G.successors(highlight))

    # Edges: one trace per (cost type, width bin) so the legend stays short.
    groups = {}
    for u, v, e in G.edges(data=True):
        groups.setdefault((e["cost_type"], width_for(e["exposure"])), []).append((u, v))
    shown_legend = set()
    for (ct, w), edges in sorted(groups.items(), key=lambda x: (x[0][0], x[0][1])):
        color, dash = EDGE_STYLE.get(ct, EDGE_STYLE["Missing"])
        xs, ys = [], []
        for u, v in edges:
            xs += [pos[u][0], pos[v][0], None]
            ys += [pos[u][1], pos[v][1], None]
        faded = keep is not None
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="lines", hoverinfo="skip",
            line=dict(color=color, width=w, dash=dash), opacity=0.12 if faded else 0.55,
            name="%s link" % ct, legendgroup=ct, showlegend=False))
        if ct not in shown_legend:
            # Legend sample at full strength (the real lines may be faded).
            fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines", name="%s link" % ct, legendgroup=ct,
                                     line=dict(color=color, width=2.5, dash=dash)))
        shown_legend.add(ct)
        if keep is not None:
            hx, hy = [], []
            for u, v in edges:
                if highlight in (u, v):
                    hx += [pos[u][0], pos[v][0], None]
                    hy += [pos[u][1], pos[v][1], None]
            if hx:
                fig.add_trace(go.Scatter(x=hx, y=hy, mode="lines", hoverinfo="skip", showlegend=False,
                                         line=dict(color=color, width=w + 0.8, dash=dash), opacity=0.95,
                                         legendgroup=ct))

    # Edge hover: invisible markers at edge midpoints.
    mx, my, mt = [], [], []
    for u, v, e in G.edges(data=True):
        if keep is not None and highlight not in (u, v):
            continue
        mx.append((pos[u][0] + pos[v][0]) / 2)
        my.append((pos[u][1] + pos[v][1]) / 2)
        exp = "%.2f%%" % (100 * e["exposure"]) if pd.notna(e["exposure"]) else "n/a"
        rev = "%.2f%%" % (100 * e["reverse"]) if pd.notna(e["reverse"]) else "n/a"
        what = "share of %s's costs" % short(v) if e["direction"] == "Supplier" else "share of %s's revenue" % short(u)
        mt.append("%s → %s<br>%s, %s: %s<br>reverse share: %s<br>%s, as of %s" % (
            G.nodes[u]["label"], G.nodes[v]["label"], e["cost_type"], what, exp, rev,
            e.get("source_type") or "", int(e["as_of_year"]) if pd.notna(e.get("as_of_year")) else "n/a"))
    fig.add_trace(go.Scatter(x=mx, y=my, mode="markers", marker=dict(size=10, opacity=0),
                             hovertext=mt, hoverinfo="text", showlegend=False))

    # Nodes: scored (blue scale) and not in universe (hollow grey).
    def node_trace(nodes, scored):
        xs = [pos[n][0] for n in nodes]
        ys = [pos[n][1] for n in nodes]
        attrs = [G.nodes[n] for n in nodes]
        hover = []
        for n, a in zip(nodes, attrs):
            s = "score %.1f" % a["score"] if pd.notna(a["score"]) else ("not scored" if a["in_universe"] else "not in universe (no score)")
            hover.append("<b>%s</b><br>%s<br>%s<br>%d suppliers, %d customers in graph" % (
                a["label"], a["name"] if isinstance(a["name"], str) else "", s, G.in_degree(n), G.out_degree(n)))
        op = [1.0 if keep is None or n in keep else 0.15 for n in nodes]
        core_line = [2.5 if a["is_core"] else 0.8 for a in attrs]
        if scored:
            marker = dict(size=[node_size(a["mkt_cap"]) for a in attrs], color=[a["score"] for a in attrs],
                          colorscale=SCORE_SCALE, cmin=3.5, cmax=7.5, opacity=op,
                          line=dict(color=TEXT, width=core_line),
                          colorbar=dict(title="Score", thickness=12, len=0.5, x=1.0))
        else:
            marker = dict(size=7, symbol="circle-open", color=NO_SCORE, opacity=op, line=dict(width=1.2))
        def lab(n, a):
            if label_all:
                return a["label"] if a["in_universe"] else short_name(a["name"])
            if a["is_core"] or (keep is not None and n in keep and a["in_universe"]):
                return a["label"]
            return ""
        text = [lab(n, a) for n, a in zip(nodes, attrs)]
        return go.Scatter(x=xs, y=ys, mode="markers+text", text=text, textposition="top center",
                          textfont=dict(size=10, color=TEXT), hovertext=hover, hoverinfo="text",
                          marker=marker, name="Scored company" if scored else "Not in universe (no score)")

    scored_nodes = [n for n, a in G.nodes(data=True) if pd.notna(a["score"])]
    other_nodes = [n for n, a in G.nodes(data=True) if pd.isna(a["score"])]
    fig.add_trace(node_trace(other_nodes, False))
    fig.add_trace(node_trace(scored_nodes, True))

    fig.update_layout(
        height=height, plot_bgcolor="#fcfcfb", paper_bgcolor="#fcfcfb", margin=dict(l=10, r=10, t=10, b=10),
        xaxis=dict(visible=False), yaxis=dict(visible=False, scaleanchor="x"),
        legend=dict(orientation="h", y=-0.02, x=0, font=dict(size=11, color=TEXT_MUTED)),
        hoverlabel=dict(bgcolor="white", font_size=12),
    )
    return fig


def short_name(name, n=16):
    """Trim a long company name for a label."""
    name = name if isinstance(name, str) else ""
    return name if len(name) <= n else name[: n - 1] + "…"


def ego_subgraph(G, center):
    """Center company plus its direct suppliers and customers."""
    if center not in G:
        return nx.DiGraph()
    nodes = {center} | set(G.predecessors(center)) | set(G.successors(center))
    return G.subgraph(nodes).copy()
