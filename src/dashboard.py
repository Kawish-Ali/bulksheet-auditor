"""
dashboard.py — Aggregations and Plotly charts for the interactive dashboard.

Built on top of the parsed `data` dict (from parser.load_bulk_file) and the
AuditReport (from auditor.run_audit). Keeps all chart styling in one place.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

PALETTE = ["#2563EB", "#16A34A", "#D97706", "#DC2626", "#7C3AED", "#0891B2", "#BE185D"]
CAMPAIGN_KEYS = ("sp_campaigns", "sb_campaigns", "sd_campaigns")
_AGG_COLS = ["Sponsored Type", "Portfolio", "Campaign Name", "Match Type",
             "Impressions", "Clicks", "Spend", "Sales", "Orders"]


def _safe_div(num, den):
    return (num / den) if den else None


def _combined_campaigns(data: dict) -> pd.DataFrame:
    frames = []
    for key in CAMPAIGN_KEYS:
        df = data.get(key)
        if df is not None and not df.empty:
            clean = df.loc[:, ~df.columns.duplicated()].copy()
            cols = [c for c in _AGG_COLS if c in clean.columns]
            frames.append(clean[cols])
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _add_metrics(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ACoS"] = df.apply(lambda r: (_safe_div(r["Spend"], r["Sales"]) or 0) * 100, axis=1)
    df["CTR"] = df.apply(lambda r: (_safe_div(r["Clicks"], r["Impressions"]) or 0) * 100, axis=1)
    df["CVR"] = df.apply(lambda r: (_safe_div(r["Orders"], r["Clicks"]) or 0) * 100, axis=1)
    return df


# ── Aggregations ───────────────────────────────────────────────────────────────

def by_sponsored_type(data: dict) -> pd.DataFrame:
    df = _combined_campaigns(data)
    if df.empty or "Sponsored Type" not in df.columns:
        return pd.DataFrame()
    grp = df.groupby("Sponsored Type", as_index=False).agg(
        Impressions=("Impressions", "sum"),
        Clicks=("Clicks", "sum"),
        Spend=("Spend", "sum"),
        Sales=("Sales", "sum"),
        Orders=("Orders", "sum"),
    )
    return _add_metrics(grp)


def by_portfolio(data: dict, top_n: int = 12) -> pd.DataFrame:
    df = _combined_campaigns(data)
    if df.empty or "Portfolio" not in df.columns:
        return pd.DataFrame()
    grp = df.groupby("Portfolio", as_index=False).agg(
        Impressions=("Impressions", "sum"),
        Clicks=("Clicks", "sum"),
        Spend=("Spend", "sum"),
        Sales=("Sales", "sum"),
        Orders=("Orders", "sum"),
    )
    grp = _add_metrics(grp).sort_values("Spend", ascending=False).head(top_n)
    return grp


def match_type_spend(data: dict) -> pd.DataFrame:
    """Spend share by match type from SP search-term / keyword data."""
    df = _combined_campaigns(data)
    if df.empty or "Match Type" not in df.columns:
        return pd.DataFrame()
    grp = df.groupby("Match Type", as_index=False).agg(Spend=("Spend", "sum"))
    grp = grp[grp["Spend"] > 0]
    return grp.sort_values("Spend", ascending=False)


_STR_COLS = ["Customer Search Term", "Keyword Text", "Campaign Name", "Match Type",
             "Sponsored Type", "Impressions", "Clicks", "Spend", "Sales", "Orders"]


def search_term_table(data: dict) -> pd.DataFrame:
    """Full combined search-term table with metrics, for the interactive explorer."""
    frames = []
    for key in ("sp_str", "sb_str"):
        df = data.get(key)
        if df is not None and not df.empty:
            clean = df.loc[:, ~df.columns.duplicated()].copy()
            cols = [c for c in _STR_COLS if c in clean.columns]
            frames.append(clean[cols])
    if not frames:
        return pd.DataFrame()
    st = pd.concat(frames, ignore_index=True)

    # Unify the term column
    if "Customer Search Term" in st.columns:
        st["Search Term"] = st["Customer Search Term"].fillna(st.get("Keyword Text"))
    else:
        st["Search Term"] = st.get("Keyword Text")

    st = _add_metrics(st)
    keep = ["Search Term", "Campaign Name", "Sponsored Type", "Match Type",
            "Impressions", "Clicks", "Spend", "Sales", "Orders", "ACoS", "CTR", "CVR"]
    keep = [c for c in keep if c in st.columns]
    return st[keep].sort_values("Spend", ascending=False).reset_index(drop=True)


# ── Charts ───────────────────────────────────────────────────────────────────

def spend_vs_sales(df: pd.DataFrame, group_col: str, cur: str = "$") -> go.Figure:
    fig = go.Figure()
    fig.add_bar(name="Spend", x=df[group_col], y=df["Spend"], marker_color="#DC2626",
                text=[f"{cur}{v:,.0f}" for v in df["Spend"]], textposition="outside")
    fig.add_bar(name="Sales", x=df[group_col], y=df["Sales"], marker_color="#16A34A",
                text=[f"{cur}{v:,.0f}" for v in df["Sales"]], textposition="outside")
    fig.update_layout(
        barmode="group", plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        yaxis=dict(tickprefix=cur, gridcolor="#e5e7eb"), xaxis=dict(gridcolor="#e5e7eb"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(t=40, b=20, l=10, r=10), height=360,
    )
    return fig


def acos_bar(df: pd.DataFrame, group_col: str, threshold: float = 35.0) -> go.Figure:
    d = df.dropna(subset=["ACoS"]).sort_values("ACoS")
    colors = ["#2ecc71" if v <= 20 else "#f39c12" if v <= threshold else "#e74c3c"
              for v in d["ACoS"]]
    fig = go.Figure(go.Bar(
        x=d["ACoS"], y=d[group_col], orientation="h", marker_color=colors,
        text=[f"{v:.1f}%" for v in d["ACoS"]], textposition="outside",
    ))
    fig.add_vline(x=threshold, line_dash="dash", line_color="#6b7280",
                  annotation_text=f"Target {threshold:.0f}%", annotation_position="top right")
    fig.update_layout(
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(ticksuffix="%", gridcolor="#e5e7eb"), yaxis=dict(gridcolor="#e5e7eb"),
        margin=dict(t=20, b=20, l=10, r=80), height=max(280, len(d) * 42),
    )
    return fig


def match_type_donut(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure(go.Pie(
        labels=df["Match Type"], values=df["Spend"], hole=0.5,
        marker_colors=PALETTE, textinfo="label+percent",
    ))
    fig.update_layout(
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=20, b=20, l=10, r=10), height=320, showlegend=False,
    )
    return fig


def intent_donut(intent_df: pd.DataFrame) -> go.Figure:
    """intent_df: columns Intent, Spend (from the INTENT_BREAKDOWN finding)."""
    fig = go.Figure(go.Pie(
        labels=intent_df["Intent"], values=intent_df["Spend"], hole=0.5,
        marker_colors=PALETTE, textinfo="label+percent",
    ))
    fig.update_layout(
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=20, b=20, l=10, r=10), height=320, showlegend=False,
    )
    return fig
