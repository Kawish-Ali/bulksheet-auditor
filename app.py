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


@st.cache_data(show_spinner=False)
def _raw_sheets(file_bytes: bytes):
    return pv.load_raw_sheets(io.BytesIO(file_bytes))


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


def _chart(fig, msg="No data available for this chart."):
    if fig is not None:
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.caption(msg)


def _format_pivot_display(df, cur):
    """Format a pivot table for display: thousands separators, currency, percent,
    and blank (not 'None'/'NaN') for missing values."""
    out = df.copy()

    def f_int(v):
        if pd.isna(v):
            return ""
        try:
            return f"{int(round(float(v))):,}"
        except (TypeError, ValueError):
            return ""

    def f_money(v):
        if pd.isna(v):
            return ""
        try:
            return f"{cur}{float(v):,.2f}"
        except (TypeError, ValueError):
            return ""

    def f_pct(v):
        if pd.isna(v):
            return ""
        try:
            return f"{float(v):.2f}%"
        except (TypeError, ValueError):
            return ""

    def f_dim(v):
        if v is None:
            return ""
        if isinstance(v, float) and pd.isna(v):
            return ""
        if isinstance(v, (int, float)):
            fv = float(v)
            return f"{int(fv):,}" if fv == int(fv) else f"{fv:,.2f}"
        sv = str(v)
        return "" if sv.strip().lower() in ("nan", "none") else sv

    for c in out.columns:
        if c in ("Impressions", "Clicks", "Orders"):
            out[c] = out[c].map(f_int)
        elif c in ("Spend", "Sales", "CPC", "CPA"):
            out[c] = out[c].map(f_money)
        elif c in ("CTR", "CNVR", "ACOS"):
            out[c] = out[c].map(f_pct)
        else:
            out[c] = out[c].map(f_dim)
    return out


_METRIC_COLS = ("Impressions", "Clicks", "CTR", "Orders", "CNVR", "CPC", "CPA", "Spend", "Sales", "ACOS")


def _table_filters(tdf, key):
    """Google Sheets-style per-column filters: each chosen column can be filtered
    'by values' (searchable checklist) or 'by condition' (operator + value).
    Conditions combine with AND. Returns the filtered detail DataFrame."""
    out = tdf
    with st.expander("Filters"):
        chosen = st.multiselect("Columns to filter", list(tdf.columns), key=f"{key}_cols")
        if not chosen:
            st.caption("Pick one or more columns. Each can be filtered by values or by a "
                       "condition (like Google Sheets); all conditions combine with AND.")
        for col in chosen:
            st.markdown(f"**{col}**")
            full = tdf[col]
            is_num = col in _METRIC_COLS or pd.api.types.is_numeric_dtype(full)
            mode = st.radio("Filter type", ["By values", "By condition"], horizontal=True,
                            key=f"{key}_{col}_mode", label_visibility="collapsed")

            if mode == "By values":
                vals = sorted(full.dropna().astype(str).unique())
                default = vals if len(vals) <= 100 else []
                if not default:
                    st.caption(f"{len(vals):,} values — pick specific ones to filter, or leave empty to show all.")
                sel = st.multiselect("Show values", vals, default=default,
                                     key=f"{key}_{col}_vals", label_visibility="collapsed")
                if sel and set(sel) != set(vals):
                    out = out[out[col].astype(str).isin(sel)]

            elif is_num:
                s_all = pd.to_numeric(full, errors="coerce")
                lo = float(s_all.min()) if s_all.notna().any() else 0.0
                hi = float(s_all.max()) if s_all.notna().any() else 0.0
                op = st.selectbox("Condition",
                    ["Greater than", "Greater than or equal to", "Less than",
                     "Less than or equal to", "Is equal to", "Is not equal to",
                     "Is between", "Is not between"],
                    key=f"{key}_{col}_op", label_visibility="collapsed")
                s = pd.to_numeric(out[col], errors="coerce")
                if op in ("Is between", "Is not between"):
                    c1, c2 = st.columns(2)
                    a = c1.number_input("Min", value=lo, key=f"{key}_{col}_a")
                    b = c2.number_input("Max", value=hi, key=f"{key}_{col}_b")
                    if a != lo or b != hi:
                        m = s.between(a, b)
                        out = out[m if op == "Is between" else (~m & s.notna())]
                else:
                    v = st.number_input("Value", value=0.0, key=f"{key}_{col}_v")
                    ops = {
                        "Greater than": s > v, "Greater than or equal to": s >= v,
                        "Less than": s < v, "Less than or equal to": s <= v,
                        "Is equal to": s == v, "Is not equal to": (s != v) & s.notna(),
                    }
                    out = out[ops[op]]

            else:
                op = st.selectbox("Condition",
                    ["Contains", "Does not contain", "Starts with", "Ends with",
                     "Is exactly", "Is empty", "Is not empty"],
                    key=f"{key}_{col}_op", label_visibility="collapsed")
                cs = out[col].astype(str)
                blank = cs.str.strip().str.lower().isin(["", "nan", "none"])
                if op == "Is empty":
                    out = out[blank]
                elif op == "Is not empty":
                    out = out[~blank]
                else:
                    v = st.text_input("Value", key=f"{key}_{col}_tv", placeholder="value")
                    if v:
                        low = cs.str.lower(); vl = v.lower()
                        if op == "Contains":
                            out = out[cs.str.contains(v, case=False, na=False)]
                        elif op == "Does not contain":
                            out = out[~cs.str.contains(v, case=False, na=False)]
                        elif op == "Starts with":
                            out = out[low.str.startswith(vl)]
                        elif op == "Ends with":
                            out = out[low.str.endswith(vl)]
                        elif op == "Is exactly":
                            out = out[low == vl]
            st.divider()
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
    raw = _raw_sheets(file_bytes)
    sp_raw = raw.get("sp_campaigns", pd.DataFrame())
    sb_parts = [raw.get("sb_campaigns"), raw.get("sb_multi")]
    sb_parts = [f for f in sb_parts if f is not None and not f.empty]
    sb_all = pd.concat(sb_parts, ignore_index=True) if sb_parts else pd.DataFrame()
    sd_raw = raw.get("sd_campaigns", pd.DataFrame())
    adtype_frames = {"SP": sp_raw, "SB": sb_all, "SD": sd_raw}
    tgt = acos_target * 100

    st.subheader("ACoS vs Spend by Campaign")
    st.caption("Bubble size = Sales. Lower-left is efficient; upper-right (high spend, high ACoS) is where to cut or fix.")
    _chart(dash.fig_acos_spend_bubble(sp_raw, tgt, cur), "No Sponsored Products campaign data.")

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Spend Concentration (Pareto)")
        _chart(dash.fig_pareto(sp_raw, cur), "No portfolio data.")
    with c2:
        st.subheader("Conversion Funnel")
        _chart(dash.fig_funnel(s.total_impressions, s.total_clicks, s.total_orders))

    c3, c4 = st.columns(2)
    with c3:
        st.subheader("Placement Performance")
        _chart(dash.fig_placement(sp_raw, cur), "No placement data.")
    with c4:
        st.subheader("Match Type Efficiency")
        _chart(dash.fig_match_type(sp_raw, cur), "No match-type data.")

    c5, c6 = st.columns(2)
    with c5:
        st.subheader("SP vs SB vs SD")
        _chart(dash.fig_adtype_comparison(adtype_frames, cur), "No ad-type data.")
    with c6:
        st.subheader("Wasted vs Converting Spend")
        _chart(dash.fig_wasted(adtype_frames, cur), "No ad-type data.")

    st.subheader("Top Campaigns by Spend")
    st.caption("Bars colored by ACoS: green \u2264 20%, amber \u2264 35%, red > 35%.")
    _chart(dash.fig_top_campaigns(sp_raw, 15, cur), "No campaign data.")

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
                    view = _table_filters(tdf, f"flt_{src_label}_{tbl_label}")
                    disp = pd.concat([view, pv.grand_total(view, dim_cols)], ignore_index=True)
                    st.dataframe(_format_pivot_display(disp, cur),
                                 use_container_width=True, hide_index=True)
                    fn = f"{src_label}_{tbl_label}.csv".replace(" ", "_").replace("×", "x")
                    _csv_download(f"Download {tbl_label} (CSV)", disp, fn)

# Parser notes — kept at the very end
if data["errors"]:
    with st.expander("Parser notes"):
        for err in data["errors"]:
            st.caption(err)
