"""
BulkSheet Auditor 2.0 — Interactive web dashboard (Streamlit).

Upload an Amazon Ads bulk sheet, then: validate, parse, audit, and explore an
interactive dashboard with headline metrics, findings, a search-term explorer,
dynamic pivots, and a prioritized action plan. Fully offline; the uploaded file
is processed in memory and never stored.
"""

import sys
import io
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

import config as cfg
from src.validator import validate_bulk_file
from src.parser import load_bulk_file, detect_currency
from src.auditor import run_audit
from src.recommender import generate_recommendations
from src import dashboard as dash
from src import pivots as pv

st.set_page_config(page_title="BulkSheet Auditor", layout="wide")

st.markdown("""
<style>
  .block-container{padding-top:2rem;max-width:1200px}
  .sev-critical{border-left:4px solid #b91c1c;background:#fdf2f2;padding:10px 14px;border-radius:6px;margin-bottom:8px}
  .sev-warning{border-left:4px solid #b45309;background:#fffaf0;padding:10px 14px;border-radius:6px;margin-bottom:8px}
  .sev-info{border-left:4px solid #1d4ed8;background:#f1f5fd;padding:10px 14px;border-radius:6px;margin-bottom:8px}
  .rec-box{border:1px solid #e5e7eb;border-radius:8px;padding:14px 16px;margin-bottom:10px}
</style>
""", unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def _process(file_bytes: bytes, acos_target: float, brand_terms: tuple):
    cfg.ACOS_TARGET = acos_target
    data = load_bulk_file(io.BytesIO(file_bytes))
    report = run_audit(data, brand_terms=list(brand_terms) if brand_terms else None)
    recs = generate_recommendations(report)
    return data, report, recs


@st.cache_data(show_spinner=False)
def _build_pivots(file_bytes: bytes):
    return pv.build_all(io.BytesIO(file_bytes))


def _money(v, cur):
    try:
        return f"{cur}{float(v):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _pct(v):
    try:
        return f"{float(v) * 100:.2f}%"
    except (TypeError, ValueError):
        return "—"


def _intf(v):
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return "—"


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


def _pivot_colcfg(df, cur):
    out = {}
    for c in df.columns:
        if c in ("Impressions", "Clicks", "Orders"):
            out[c] = st.column_config.NumberColumn(format="%d")
        elif c in ("Spend", "Sales", "CPC", "CPA"):
            out[c] = st.column_config.NumberColumn(format=f"{cur}%.2f")
        elif c in ("CTR", "CNVR", "ACOS"):
            out[c] = st.column_config.NumberColumn(format="%.2f%%")
    return out


acos_target = cfg.ACOS_TARGET
brand_terms = ()

st.title("Amazon PPC BulkSheet Auditor")
st.caption("Upload an Amazon Ads bulk sheet to get account metrics, findings, breakdowns, and a prioritized action plan.")

uploaded = st.file_uploader(
    "Upload your Amazon Ads bulk sheet (.xlsx)",
    type=["xlsx"],
    help="Download it from Amazon Ads Console, Sponsored ads, Bulk operations.",
)

if uploaded is None:
    st.info("Upload a bulk sheet to begin. The file is processed in memory and never stored.")
    st.stop()

file_bytes = uploaded.getvalue()

with st.spinner("Verifying the file is an Amazon bulk sheet..."):
    vres = validate_bulk_file(io.BytesIO(file_bytes))

if not vres.is_valid:
    st.error(f"Not a valid bulk sheet — detected: {vres.file_type}")
    st.warning(vres.reason)
    if vres.sheets_found:
        with st.expander("Sheets found in your file"):
            st.write(vres.sheets_found)
    st.stop()

st.success(f"Valid Amazon Ads bulk sheet. Campaign sheets: {', '.join(vres.campaign_sheets)}")
for w in vres.warnings:
    st.caption(w)

with st.spinner("Parsing and auditing your account..."):
    data, report, recs = _process(file_bytes, acos_target, brand_terms)

cur, cur_code, cur_mixed = detect_currency(data.get("portfolios"))
if cur_mixed:
    st.warning(
        f"Multiple currencies detected in this file; showing {cur_code}. "
        "Amazon bulk files are normally single-currency — verify the source. "
        "Cross-currency conversion is not applied."
    )

s = report.summary
cpa = (s.total_spend / s.total_orders) if s.total_orders else None

# Headline metrics — fixed order: Impressions, Clicks, CTR, Orders, CNVR, CPC, CPA, Spend, Sales, ACOS
r1 = st.columns(5)
r1[0].metric("Impressions", _intf(s.total_impressions))
r1[1].metric("Clicks", _intf(s.total_clicks))
r1[2].metric("CTR", _pct(s.ctr))
r1[3].metric("Orders", _intf(s.total_orders))
r1[4].metric("CNVR", _pct(s.cvr))
r2 = st.columns(5)
r2[0].metric("CPC", _money(s.cpc, cur))
r2[1].metric("CPA", _money(cpa, cur))
r2[2].metric("Spend", _money(s.total_spend, cur))
r2[3].metric("Sales", _money(s.total_sales, cur))
r2[4].metric("ACOS", _pct(s.acos))

st.markdown("---")

tab_overview, tab_pivots = st.tabs(["Overview", "Pivots"])

with tab_overview:
    type_agg = dash.by_sponsored_type(data)
    c1, c2 = st.columns(2)
    if not type_agg.empty:
        with c1:
            st.subheader("Spend vs Sales by Ad Type")
            st.plotly_chart(dash.spend_vs_sales(type_agg, "Sponsored Type", cur), use_container_width=True)
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
    intent_df = _finding_affected(report, "INTENT_BREAKDOWN")
    if not intent_df.empty:
        st.subheader("Search Term Intent (by spend)")
        st.plotly_chart(dash.intent_donut(intent_df), use_container_width=True)

with tab_pivots:
    st.caption("Standard bulk-file breakdowns, computed live from your upload. Filter any table and its Grand Total updates with the filter.")
    with st.spinner("Building pivot tables..."):
        all_pivots = _build_pivots(file_bytes)
    if not all_pivots:
        st.info("No pivot-able source sheets found in this file.")
    else:
        st.download_button(
            "Download all pivots (Excel workbook)",
            data=pv.to_excel_bytes(all_pivots),
            file_name="bulk_pivots.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
        src_tabs = st.tabs(list(all_pivots.keys()))
        for stab, (src_label, tables) in zip(src_tabs, all_pivots.items()):
            with stab:
                for tbl_label, tdf in tables:
                    dim_cols = [c for c in tdf.columns if c not in pv.DISPLAY_COLS]
                    st.markdown(f"**{tbl_label}**")
                    q = st.text_input(
                        f"Filter {dim_cols[0]}",
                        key=f"flt_{src_label}_{tbl_label}",
                        placeholder=f"Filter by {dim_cols[0]}",
                        label_visibility="collapsed",
                    )
                    view = tdf
                    if q:
                        view = view[view[dim_cols[0]].astype(str).str.contains(q, case=False, na=False)]
                    disp = pd.concat([view, pv.grand_total(view, dim_cols)], ignore_index=True)
                    st.dataframe(disp, use_container_width=True, hide_index=True,
                                 column_config=_pivot_colcfg(disp, cur))
                    fn = f"{src_label}_{tbl_label}.csv".replace(" ", "_").replace("×", "x")
                    _csv_download(f"Download {tbl_label} (CSV)", disp, fn)

# Parser notes — kept at the very end
if data["errors"]:
    with st.expander("Parser notes"):
        for err in data["errors"]:
            st.caption(err)
