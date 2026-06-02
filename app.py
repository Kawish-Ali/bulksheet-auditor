"""
BulkSheet Auditor 2.0 — Interactive web dashboard (Streamlit).

Public flow:
  1. User uploads a file
  2. We VALIDATE it's a real Amazon Ads bulk sheet (reject anything else)
  3. We parse + audit + score
  4. We render an interactive dashboard: KPI cards, charts, filterable tables,
     prioritized recommendations, and CSV exports of flagged data.

Run locally:   streamlit run app.py
Deploy free:   push to GitHub → share.streamlit.io → New app
"""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

import config as cfg
from src.validator import validate_bulk_file
from src.parser import load_bulk_file
from src.auditor import run_audit
from src.recommender import generate_recommendations
from src import dashboard as dash

st.set_page_config(page_title="BulkSheet Auditor 2.0", page_icon="📊", layout="wide")


# ── Styling ──────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  .block-container{padding-top:2rem;max-width:1200px}
  .score-badge{text-align:center;padding:14px;border-radius:12px;color:#fff}
  .sev-critical{border-left:4px solid #e74c3c;background:#fff5f5;padding:10px 14px;border-radius:6px;margin-bottom:8px}
  .sev-warning{border-left:4px solid #f39c12;background:#fffbf0;padding:10px 14px;border-radius:6px;margin-bottom:8px}
  .sev-info{border-left:4px solid #4361ee;background:#f0f7ff;padding:10px 14px;border-radius:6px;margin-bottom:8px}
  .rec-box{border:1px solid #e8ecf0;border-radius:8px;padding:14px 16px;margin-bottom:10px}
</style>
""", unsafe_allow_html=True)


# ── Cached heavy work (keyed on file bytes) ──────────────────────────────────
@st.cache_data(show_spinner=False)
def _process(file_bytes: bytes, acos_target: float, brand_terms: tuple):
    import io
    cfg.ACOS_TARGET = acos_target
    buf = io.BytesIO(file_bytes)
    data = load_bulk_file(buf)
    report = run_audit(data, brand_terms=list(brand_terms) if brand_terms else None)
    recs = generate_recommendations(report)
    return data, report, recs


def _fmt_pct(v):
    return f"{v:.1%}" if v is not None else "—"


def _fmt_money(v):
    return f"${v:,.2f}" if v is not None else "—"


def _finding_affected(report, code):
    for f in report.findings:
        if f.code == code and isinstance(f.affected, pd.DataFrame):
            return f.affected
    return pd.DataFrame()


def _csv_download(label, df, filename):
    if df is None or df.empty:
        return
    st.download_button(label, df.to_csv(index=False).encode("utf-8"),
                       file_name=filename, mime="text/csv", use_container_width=True)


# Defaults (sidebar removed — runs with sensible defaults)
acos_target = cfg.ACOS_TARGET   # 30% target for scoring + chart thresholds
brand_terms = ()                # no branded-term classification by default


# ── Header ───────────────────────────────────────────────────────────────────
st.title("📊 Amazon PPC BulkSheet Auditor")
st.caption("Drop your Amazon Ads bulk file. Get an instant health score, findings, and a prioritized action plan.")

uploaded = st.file_uploader(
    "Upload your Amazon Ads bulk sheet (.xlsx)",
    type=["xlsx"],
    help="Download it from Amazon Ads Console → Sponsored ads → Bulk operations.",
)

if uploaded is None:
    st.info("👆 Upload a bulk sheet to begin. The file is processed in memory and never stored.")
    st.stop()

file_bytes = uploaded.getvalue()

# ── Step 1: Validate ─────────────────────────────────────────────────────────
import io
with st.spinner("Verifying the file is an Amazon bulk sheet..."):
    vres = validate_bulk_file(io.BytesIO(file_bytes))

if not vres.is_valid:
    st.error(f"❌ **Not a valid bulk sheet** — detected: *{vres.file_type}*")
    st.warning(vres.reason)
    if vres.sheets_found:
        with st.expander("Sheets we found in your file"):
            st.write(vres.sheets_found)
    st.stop()

st.success(f"✅ Valid Amazon Ads bulk sheet — campaign sheets: {', '.join(vres.campaign_sheets)}")
for w in vres.warnings:
    st.caption(f"⚠️ {w}")

# ── Step 2: Parse + Audit ────────────────────────────────────────────────────
with st.spinner("Parsing and auditing your account... (large files take a moment)"):
    data, report, recs = _process(file_bytes, acos_target, brand_terms)

if data["errors"]:
    with st.expander("Parser notes"):
        for e in data["errors"]:
            st.caption(e)

s = report.summary

# ── Score + KPI cards ────────────────────────────────────────────────────────
score_color = "#16A34A" if report.score >= 75 else "#D97706" if report.score >= 50 else "#DC2626"
score_label = "GOOD" if report.score >= 75 else "NEEDS WORK" if report.score >= 50 else "CRITICAL"

top = st.columns([1, 3])
with top[0]:
    st.markdown(
        f"<div class='score-badge' style='background:{score_color}'>"
        f"<div style='font-size:46px;font-weight:800;line-height:1'>{report.score}</div>"
        f"<div style='font-size:12px;letter-spacing:1px'>HEALTH SCORE</div>"
        f"<div style='font-size:13px;margin-top:4px'>{score_label}</div></div>",
        unsafe_allow_html=True,
    )
with top[1]:
    k = st.columns(4)
    k[0].metric("Spend", _fmt_money(s.total_spend))
    k[1].metric("Sales", _fmt_money(s.total_sales))
    k[2].metric("ACoS", _fmt_pct(s.acos))
    k[3].metric("ROAS", f"{s.roas:.2f}x" if s.roas else "—")
    k2 = st.columns(4)
    k2[0].metric("Impressions", f"{s.total_impressions:,}")
    k2[1].metric("Clicks", f"{s.total_clicks:,}")
    k2[2].metric("Orders", f"{s.total_orders:,}")
    k2[3].metric("CVR", _fmt_pct(s.cvr))

st.markdown("---")

# ── Tabs ─────────────────────────────────────────────────────────────────────
tab_overview, tab_findings, tab_terms, tab_actions = st.tabs(
    ["📈 Overview", "🔎 Findings", "🔍 Search Terms", "💡 Action Plan"]
)

# ---- Overview (charts) -------------------------------------------------------
with tab_overview:
    type_agg = dash.by_sponsored_type(data)
    c1, c2 = st.columns(2)
    if not type_agg.empty:
        with c1:
            st.subheader("Spend vs Sales by Ad Type")
            st.plotly_chart(dash.spend_vs_sales(type_agg, "Sponsored Type"), use_container_width=True)
        with c2:
            st.subheader("ACoS by Ad Type")
            st.plotly_chart(dash.acos_bar(type_agg, "Sponsored Type", acos_target * 100), use_container_width=True)

    port_agg = dash.by_portfolio(data)
    mt = dash.match_type_spend(data)
    c3, c4 = st.columns(2)
    if not port_agg.empty:
        with c3:
            st.subheader("ACoS by Portfolio (top spenders)")
            st.plotly_chart(dash.acos_bar(port_agg, "Portfolio", acos_target * 100), use_container_width=True)
    if not mt.empty:
        with c4:
            st.subheader("Spend by Match Type")
            st.plotly_chart(dash.match_type_donut(mt), use_container_width=True)

    # Intent donut from the audit finding
    intent_df = _finding_affected(report, "INTENT_BREAKDOWN")
    if not intent_df.empty:
        st.subheader("Search Term Intent (by spend)")
        st.plotly_chart(dash.intent_donut(intent_df), use_container_width=True)

# ---- Findings (filterable) ---------------------------------------------------
with tab_findings:
    fcol = st.columns(2)
    sev_filter = fcol[0].multiselect("Severity", ["critical", "warning", "info"],
                                     default=["critical", "warning"])
    modules = sorted({f.module for f in report.findings})
    mod_filter = fcol[1].multiselect("Module", modules, default=modules)

    shown = [f for f in report.findings if f.severity in sev_filter and f.module in mod_filter]
    if not shown:
        st.info("No findings match the current filters.")
    for f in shown:
        st.markdown(
            f"<div class='sev-{f.severity}'><b>[{f.module}] {f.title}</b><br>"
            f"<span style='font-size:13px;color:#555'>{f.detail}</span></div>",
            unsafe_allow_html=True,
        )
        if isinstance(f.affected, pd.DataFrame) and not f.affected.empty:
            with st.expander(f"View {len(f.affected)} affected rows"):
                st.dataframe(f.affected, use_container_width=True, hide_index=True)
        elif isinstance(f.affected, (list, set)) and f.affected:
            with st.expander(f"View {len(f.affected)} items"):
                st.write(sorted(str(x) for x in f.affected))

# ---- Search Terms explorer ---------------------------------------------------
with tab_terms:
    st_table = dash.search_term_table(data)
    if st_table.empty:
        st.info("No Search Term Report data found in this file.")
    else:
        f = st.columns(4)
        type_opts = sorted(st_table["Sponsored Type"].dropna().unique()) if "Sponsored Type" in st_table else []
        sel_type = f[0].multiselect("Ad type", type_opts, default=type_opts)
        min_spend = f[1].number_input("Min spend ($)", 0.0, value=0.0, step=1.0)
        only_zero_orders = f[2].checkbox("Zero-order only (negatives)")
        sort_by = f[3].selectbox("Sort by", ["Spend", "ACoS", "Sales", "Orders", "Clicks"], index=0)

        view = st_table.copy()
        if sel_type and "Sponsored Type" in view:
            view = view[view["Sponsored Type"].isin(sel_type)]
        view = view[view["Spend"] >= min_spend]
        if only_zero_orders:
            view = view[view["Orders"] == 0]
        view = view.sort_values(sort_by, ascending=False)

        st.caption(f"{len(view):,} search terms · ${view['Spend'].sum():,.2f} spend")
        st.dataframe(view, use_container_width=True, hide_index=True, height=440)
        _csv_download("⬇️ Download this view (CSV)", view, "search_terms.csv")

# ---- Action Plan + exports ---------------------------------------------------
with tab_actions:
    st.subheader("Prioritized Action Plan")
    if not recs:
        st.info("No recommendations — the account looks healthy on the audited dimensions.")
    for r in recs:
        st.markdown(
            f"<div class='rec-box'><b>{r.priority}. {r.title}</b>"
            f"<div style='font-size:12px;color:#888'>{r.affected_summary}</div>"
            f"<div style='font-size:13px;color:#555;margin:6px 0'>{r.issue}</div></div>",
            unsafe_allow_html=True,
        )
        with st.expander("How to fix it"):
            st.markdown(r.action.replace("\n", "  \n"))
            st.success(f"Expected impact: {r.expected_impact}")

    st.markdown("---")
    st.subheader("Export flagged data")
    e = st.columns(3)
    with e[0]:
        _csv_download("Negative-keyword candidates",
                      _finding_affected(report, "WASTED_SEARCH_TERMS"), "negative_candidates.csv")
    with e[1]:
        _csv_download("Quick-win terms (promote to Exact)",
                      _finding_affected(report, "QUICK_WIN_EXACT_PROMOTE"), "quick_wins.csv")
    with e[2]:
        _csv_download("High-ACoS keywords",
                      _finding_affected(report, "HIGH_ACOS_KEYWORDS"), "high_acos_keywords.csv")
