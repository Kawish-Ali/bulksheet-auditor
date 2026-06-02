"""
pivots.py — Dynamic pivot/breakdown tables for Amazon bulk files.

Reproduces the standard agency "Bulk File Analysis" pivots (SP / SB / SB-Multi /
SP-STR / SB-STR). Each pivot:
  1. (optionally) filters the source sheet by Entity (Campaign / Bidding
     adjustment / etc.) — needed for fields Amazon denormalises onto every row,
  2. groups by one or more row fields, dropping blank keys,
  3. sums the 5 base metrics,
  4. derives CTR, CNVR, CPC, CPA, ACOS from those sums.

Fully data-driven via SPEC, so adding/removing a table is one line. Tables whose
source sheet or row field is missing in the uploaded file are skipped gracefully.
"""
from __future__ import annotations
import io
import pandas as pd

from .parser import _make_reader

BASE = ["Impressions", "Clicks", "Spend", "Sales", "Orders"]
DISPLAY_COLS = ["Impressions", "Clicks", "CTR", "Orders", "CNVR",
                "CPC", "CPA", "Spend", "Sales", "ACOS"]

# sheet key -> case-insensitive substring of the real sheet name
SHEETS = {
    "sp_campaigns": "sponsored products campaigns",
    "sb_campaigns": "sponsored brands campaigns",
    "sb_multi":     "sb multi ad group campaigns",
    "sp_str":       "sp search term report",
    "sb_str":       "sb search term report",
}
SOURCE_LABELS = {
    "sp_campaigns": "SP Campaigns",
    "sb_campaigns": "SB Campaigns",
    "sb_multi":     "SB Multi Ad Group",
    "sp_str":       "SP Search Terms",
    "sb_str":       "SB Search Terms",
}
SOURCE_ORDER = ["sp_campaigns", "sb_campaigns", "sb_multi", "sp_str", "sb_str"]
SRC_ABBR = {
    "SP Campaigns": "SP", "SB Campaigns": "SBC", "SB Multi Ad Group": "SBM",
    "SP Search Terms": "SP-STR", "SB Search Terms": "SB-STR",
}

# (label, sheet_key, row_fields, entity_filter, extra)
SPEC = [
    ("Bidding Strategy", "sp_campaigns", ["Bidding strategy"], "Campaign", None),
    ("Placement", "sp_campaigns", ["Placement"], "Bidding adjustment", None),
    ("Targeting Type", "sp_campaigns", ["Targeting type"], None, None),
    ("Product Targeting Expression", "sp_campaigns", ["Product targeting expression"], None, None),
    ("Match Type", "sp_campaigns", ["Match type"], None, None),
    ("Resolved Targeting Expression", "sp_campaigns", ["Resolved product targeting expression (Informational only)"], None, None),
    ("Audience Segment", "sp_campaigns", ["Segment Name (Informational only)"], None, None),
    ("Sites", "sp_campaigns", ["Sites"], None, None),
    ("Portfolio", "sp_campaigns", ["Portfolio name (Informational only)"], "Campaign", None),
    ("SKU", "sp_campaigns", ["SKU"], None, None),
    ("Campaign Name", "sp_campaigns", ["Campaign name"], "Campaign", None),
    ("SKU × Campaign", "sp_campaigns", ["SKU", "Campaign name (Informational only)"], None, None),
    ("Placement × Percentage", "sp_campaigns", ["Placement", "Percentage"], "Bidding adjustment", None),
    ("Campaign × Placement", "sp_campaigns", ["Campaign name (Informational only)", "Placement"], None, None),

    ("Match Type", "sb_campaigns", ["Match type"], None, None),
    ("Ad Format", "sb_campaigns", ["Ad format"], None, None),
    ("Landing Page Type", "sb_campaigns", ["Landing page type (Informational only)"], None, None),
    ("Creative Headline", "sb_campaigns", ["Creative headline"], None, None),
    ("Creative ASINs", "sb_campaigns", ["Creative ASINs"], None, None),
    ("Campaign Name", "sb_campaigns", ["Campaign name"], None, None),

    ("Match Type", "sb_multi", ["Match type"], None, None),
    ("Resolved Targeting Expression", "sb_multi", ["Resolved product targeting expression (Informational only)"], None, None),
    ("Landing Page URL", "sb_multi", ["Landing page URL"], None, None),
    ("Landing Page Type", "sb_multi", ["Landing page type"], None, None),
    ("Creative Headline", "sb_multi", ["Creative headline"], None, None),
    ("Creative ASINs", "sb_multi", ["Creative ASINs"], None, None),
    ("Campaign Name", "sb_multi", ["Campaign name"], None, None),
    ("Headline × Campaign", "sb_multi", ["Creative headline", "Campaign name (Informational only)"], None, None),

    ("Match Type", "sp_str", ["Match type"], None, None),
    ("Resolved Targeting Expression", "sp_str", ["Resolved product targeting expression (Informational only)"], None, None),
    ("Orders Distribution", "sp_str", ["Orders"], None, None),
    ("Campaign Name", "sp_str", ["Campaign name (Informational only)"], None, None),
    ("Campaign Name (0 orders)", "sp_str", ["Campaign name (Informational only)"], None, "zero_orders"),

    ("Match Type", "sb_str", ["Match type"], None, None),
    ("Campaign Name", "sb_str", ["Campaign name (Informational only)"], None, None),
    ("Orders Distribution", "sb_str", ["Orders"], None, None),
    ("Customer Search Term", "sb_str", ["Customer search term"], None, None),
]


def _num(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in BASE:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
        else:
            df[c] = 0.0
    return df


def _blank_mask(s: pd.Series):
    t = s.astype(str).str.strip()
    return t.ne("") & t.str.lower().ne("nan")


def _ratios(frame: pd.DataFrame) -> pd.DataFrame:
    f = frame.copy()
    f["CTR"] = (f["Clicks"] / f["Impressions"] * 100).where(f["Impressions"] > 0)
    f["CNVR"] = (f["Orders"] / f["Clicks"] * 100).where(f["Clicks"] > 0)
    f["CPC"] = (f["Spend"] / f["Clicks"]).where(f["Clicks"] > 0)
    f["CPA"] = (f["Spend"] / f["Orders"]).where(f["Orders"] > 0)
    f["ACOS"] = (f["Spend"] / f["Sales"] * 100).where(f["Sales"] > 0)
    return f


def build(df: pd.DataFrame, rows: list, entity=None, extra=None):
    """Build one pivot table; returns a DataFrame (rows + 10 metric cols + Grand Total) or None."""
    d = _num(df)
    if entity and "Entity" in d.columns:
        d = d[d["Entity"].astype(str).str.strip().str.lower() == entity.lower()]
    if extra == "zero_orders":
        d = d[d["Orders"] == 0]
    for f in rows:
        if f not in d.columns:
            return None
        d = d[_blank_mask(d[f])]
    if d.empty:
        return None

    # Rename any grouping key that collides with a metric column (e.g. "Orders"
    # used as the row dimension in the orders-distribution pivots).
    d = d.copy()
    group_cols = []
    for f in rows:
        if f in BASE or f in DISPLAY_COLS:
            gc = f"{f} (value)"
            d[gc] = d[f]
            group_cols.append(gc)
        else:
            group_cols.append(f)

    g = d.groupby(group_cols, as_index=False)[BASE].sum()
    # drop completely-inactive groups (e.g. negative-keyword rows: 0 impr/clicks/spend)
    g = g[~((g["Impressions"] == 0) & (g["Clicks"] == 0) & (g["Spend"] == 0))]
    if g.empty:
        return None
    g = _ratios(g).sort_values("Spend", ascending=False)

    gt = {r: ("Grand Total" if i == 0 else "") for i, r in enumerate(group_cols)}
    for c in BASE:
        gt[c] = g[c].sum()
    gt_row = _ratios(pd.DataFrame([gt]))

    out = pd.concat([g, gt_row], ignore_index=True)
    return out[group_cols + DISPLAY_COLS]


def load_raw_sheets(source) -> dict:
    """Read each needed source sheet with ORIGINAL column names (no normalization)."""
    sheet_names, read_fn = _make_reader(source)
    lower = {n.lower(): n for n in sheet_names}
    out = {}
    for key, frag in SHEETS.items():
        match = next((real for low, real in lower.items() if frag in low), None)
        if match:
            try:
                df = read_fn(match)
                df = df.loc[:, ~df.columns.duplicated()]
                df.columns = [str(c).strip() for c in df.columns]
                out[key] = df
            except Exception:
                out[key] = pd.DataFrame()
        else:
            out[key] = pd.DataFrame()
    return out


def build_all(source) -> dict:
    """Return {source_label: [(table_label, DataFrame), ...]} for every available pivot."""
    sheets = load_raw_sheets(source)
    result = {SOURCE_LABELS[k]: [] for k in SOURCE_ORDER}
    for label, key, rows, entity, extra in SPEC:
        df = sheets.get(key)
        if df is None or df.empty:
            continue
        table = build(df, rows, entity, extra)
        if table is not None:
            result[SOURCE_LABELS[key]].append((label, table))
    return {k: v for k, v in result.items() if v}


def to_excel_bytes(all_pivots: dict) -> bytes:
    """Pack every pivot table into a single .xlsx (one sheet per table)."""
    import io
    buf = io.BytesIO()
    used = set()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        wrote = False
        for src_label, tables in all_pivots.items():
            abbr = SRC_ABBR.get(src_label, src_label[:6])
            for tbl_label, df in tables:
                name = f"{abbr} {tbl_label}"
                for ch in '[]:*?/\\':
                    name = name.replace(ch, "-")
                name = (name[:31].strip() or "Sheet")
                base, i = name, 1
                while name.lower() in used:
                    sfx = f"~{i}"
                    name = base[:31 - len(sfx)] + sfx
                    i += 1
                used.add(name.lower())
                df.to_excel(xw, sheet_name=name, index=False)
                wrote = True
        if not wrote:
            pd.DataFrame({"info": ["No pivots available"]}).to_excel(xw, sheet_name="Empty", index=False)
    return buf.getvalue()
