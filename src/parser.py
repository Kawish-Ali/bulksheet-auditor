"""
parser.py — Load and normalise all sheets from an Amazon PPC bulk file.

Returns a structured dict of DataFrames, one per sheet type, all with
consistent column names so the audit engine can work uniformly.
"""

import pandas as pd
import numpy as np
from typing import Optional, Callable


def open_workbook(source) -> pd.ExcelFile:
    """
    Open an Excel workbook for cheap header-only reads (used by the validator).
    Tries the pandas 'calamine' engine (pandas >= 2.2), else openpyxl.
    """
    try:
        return pd.ExcelFile(source, engine="calamine")
    except Exception:
        try:
            source.seek(0)
        except (AttributeError, OSError):
            pass
        return pd.ExcelFile(source, engine="openpyxl")


def _make_reader(source):
    """
    Build a fast full-sheet reader.

    Returns (sheet_names: list[str], read_fn: Callable[[str], DataFrame]).
    Primary path uses python-calamine directly (Rust-based, ~15x faster than
    openpyxl, works on any pandas version). Falls back to pandas/openpyxl.
    """
    # ── Fast path: python-calamine directly ──────────────────────────────────
    try:
        from python_calamine import CalamineWorkbook
        try:
            source.seek(0)
            wb = CalamineWorkbook.from_filelike(source)
        except (AttributeError, OSError):
            wb = CalamineWorkbook.from_path(source)

        names = list(wb.sheet_names)

        def read_fn(sheet_name: str) -> pd.DataFrame:
            sheet = wb.get_sheet_by_name(sheet_name)
            rows = sheet.to_python()
            if not rows:
                return pd.DataFrame()
            header = [str(c) for c in rows[0]]
            return pd.DataFrame(rows[1:], columns=header)

        return names, read_fn
    except Exception:
        pass

    # ── Fallback: pandas + openpyxl ──────────────────────────────────────────
    xl = open_workbook(source)
    names = list(xl.sheet_names)

    def read_fn(sheet_name: str) -> pd.DataFrame:
        return pd.read_excel(xl, sheet_name=sheet_name, header=0)

    return names, read_fn


SHEET_SP  = "Sponsored Products Campaigns"
SHEET_SB  = "Sponsored Brands Campaigns"
SHEET_SBM = "SB Multi Ad Group Campaigns"
SHEET_SD  = "Sponsored Display Campaigns"
SHEET_SP_STR = "SP Search Term Report"
SHEET_SB_STR = "SB Search Term Report"
SHEET_PORTFOLIOS = "Portfolios"
SHEET_BUDGET_RULES = "Budget Rules"

NUMERIC_COLS = ["Impressions", "Clicks", "Spend", "Sales", "Orders"]


def _derive_portfolio_from_name(campaign_name: str) -> str:
    if pd.isna(campaign_name):
        return "No Portfolio"
    n = str(campaign_name)
    parts = [p.strip() for p in n.split("|")]
    for part in parts[1:]:
        part = part.strip()
        if not part:
            continue
        if part.replace(" ", "").replace("-", "").isdigit():
            continue
        if len(part) <= 6 and part.replace(" ", "").isalnum():
            continue
        lower = part.lower()
        if lower in ("auto", "broad", "phrase", "exact", "amt", "new", "generic",
                     "brand", "tos", "brand defense", "testing"):
            continue
        return part
    return n.split("|")[0].strip() if "|" in n else n


def _coerce_numerics(df: pd.DataFrame) -> pd.DataFrame:
    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        else:
            df[col] = 0.0
    return df


def _add_portfolio(df: pd.DataFrame) -> pd.DataFrame:
    campaign_col = next(
        (c for c in df.columns if "Campaign Name" in c), None
    )
    portfolio_col = next(
        (c for c in df.columns if "Portfolio Name" in c), None
    )
    if portfolio_col:
        df["Portfolio"] = df[portfolio_col].fillna("")
    else:
        df["Portfolio"] = ""
    if campaign_col:
        mask = df["Portfolio"] == ""
        df.loc[mask, "Portfolio"] = df.loc[mask, campaign_col].apply(
            _derive_portfolio_from_name
        )
    return df


def _normalise_campaign_name(df: pd.DataFrame) -> pd.DataFrame:
    if "Campaign Name" not in df.columns:
        match = None
        for col in df.columns:
            if "campaign name" in str(col).lower():
                match = col
                break
        if match is not None:
            df = df.rename(columns={match: "Campaign Name"})
        else:
            df["Campaign Name"] = ""
    return df.loc[:, ~df.columns.duplicated()]


def _normalise_ad_group(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.columns:
        if "Ad Group Name" in col and col != "Ad Group Name":
            df = df.rename(columns={col: "Ad Group Name"})
            break
    if "Ad Group Name" not in df.columns:
        df["Ad Group Name"] = ""
    return df


def _load_campaign_sheet(
    sheet_names: list,
    read_fn: Callable,
    sheet_name: str,
    sponsored_type: str,
) -> Optional[pd.DataFrame]:
    if sheet_name not in sheet_names:
        return None
    df = read_fn(sheet_name)
    if df.empty:
        return None

    # Deduplicate column names (some bulk sheets repeat column headers)
    df.columns = [str(c).strip() for c in df.columns]
    df = df.loc[:, ~df.columns.duplicated()]

    df["Sponsored Type"] = sponsored_type
    df = _coerce_numerics(df)
    df = _add_portfolio(df)
    df = _normalise_campaign_name(df)
    df = _normalise_ad_group(df)

    if "Match Type" not in df.columns:
        df["Match Type"] = "Auto/Product"
    else:
        df["Match Type"] = df["Match Type"].fillna("Auto/Product")

    if "Keyword Text" not in df.columns:
        df["Keyword Text"] = np.nan

    if "State" not in df.columns:
        df["State"] = "enabled"
    else:
        df["State"] = (
            df["State"].astype(str).str.strip().str.lower()
            .replace({"": "enabled", "nan": "enabled", "none": "enabled"})
        )

    if "Daily Budget" not in df.columns:
        df["Daily Budget"] = np.nan
    else:
        df["Daily Budget"] = pd.to_numeric(df["Daily Budget"], errors="coerce")

    # Final dedup — catches any duplicates introduced by rename steps above
    df = df.loc[:, ~df.columns.duplicated()]
    return df


def _load_str_sheet(
    sheet_names: list,
    read_fn: Callable,
    sheet_name: str,
    sponsored_type: str,
) -> Optional[pd.DataFrame]:
    if sheet_name not in sheet_names:
        return None
    df = read_fn(sheet_name)
    if df.empty:
        return None

    df.columns = [str(c).strip() for c in df.columns]
    df = df.loc[:, ~df.columns.duplicated()]

    df["Sponsored Type"] = sponsored_type
    df = _coerce_numerics(df)
    df = _add_portfolio(df)
    df = _normalise_campaign_name(df)
    df = _normalise_ad_group(df)

    if "Match Type" not in df.columns:
        df["Match Type"] = "Auto/Product"
    else:
        df["Match Type"] = df["Match Type"].fillna("Auto/Product")

    if "Customer Search Term" not in df.columns:
        df["Customer Search Term"] = np.nan
    if "Keyword Text" not in df.columns:
        df["Keyword Text"] = np.nan

    df = df.loc[:, ~df.columns.duplicated()]
    return df


def _classify_sbv(df: pd.DataFrame) -> pd.DataFrame:
    def reclassify(row):
        name = str(row.get("Campaign Name", ""))
        if "Headline" in name:
            return "SBV - Headline"
        if name.upper().startswith("SBV"):
            return "SBV"
        return "SB"
    df["Sponsored Type"] = df.apply(reclassify, axis=1)
    return df


def load_bulk_file(source) -> dict:
    """
    Load all sheets from an Amazon PPC bulk XLSX file.

    Returns dict with keys:
        sp_campaigns  : SP keyword/ad group/campaign rows (all states)
        sb_campaigns  : SB/SBV rows (SB Multi Ad Group or standard SB)
        sd_campaigns  : SD rows
        sp_str        : SP Search Term Report rows
        sb_str        : SB Search Term Report rows
        portfolios    : Portfolios sheet (or empty DataFrame)
        budget_rules  : Budget Rules sheet (or empty DataFrame)
        sheets_found  : list of sheet names in the file
        errors        : non-fatal warnings
    """
    errors = []

    try:
        sheets_found, read_fn = _make_reader(source)
    except Exception as e:
        empty = pd.DataFrame()
        return {k: empty for k in ("sp_campaigns", "sb_campaigns", "sd_campaigns",
                                   "sp_str", "sb_str", "portfolios", "budget_rules",
                                   "sheets_found", "errors")} | {
            "sheets_found": [], "errors": [str(e)]
        }

    sp_campaigns = _load_campaign_sheet(sheets_found, read_fn, SHEET_SP, "SP")
    if sp_campaigns is None:
        errors.append(f"Sheet '{SHEET_SP}' not found or empty.")
        sp_campaigns = pd.DataFrame()

    sb_campaigns = _load_campaign_sheet(sheets_found, read_fn, SHEET_SBM, "SBV")
    if sb_campaigns is None:
        sb_campaigns = _load_campaign_sheet(sheets_found, read_fn, SHEET_SB, "SB")
    if sb_campaigns is None:
        errors.append(f"Neither '{SHEET_SBM}' nor '{SHEET_SB}' found.")
        sb_campaigns = pd.DataFrame()
    else:
        sb_campaigns = _classify_sbv(sb_campaigns)

    sd_campaigns = _load_campaign_sheet(sheets_found, read_fn, SHEET_SD, "SD")
    if sd_campaigns is None:
        errors.append(f"Sheet '{SHEET_SD}' not found or empty.")
        sd_campaigns = pd.DataFrame()

    sp_str = _load_str_sheet(sheets_found, read_fn, SHEET_SP_STR, "SP")
    if sp_str is None:
        errors.append(f"Sheet '{SHEET_SP_STR}' not found or empty.")
        sp_str = pd.DataFrame()

    sb_str = _load_str_sheet(sheets_found, read_fn, SHEET_SB_STR, "SB")
    if sb_str is None:
        errors.append(f"Sheet '{SHEET_SB_STR}' not found or empty.")
        sb_str = pd.DataFrame()

    portfolios = pd.DataFrame()
    if SHEET_PORTFOLIOS in sheets_found:
        try:
            portfolios = read_fn(SHEET_PORTFOLIOS)
        except Exception:
            pass

    budget_rules = pd.DataFrame()
    if SHEET_BUDGET_RULES in sheets_found:
        try:
            budget_rules = read_fn(SHEET_BUDGET_RULES)
        except Exception:
            pass

    return {
        "sp_campaigns": sp_campaigns,
        "sb_campaigns": sb_campaigns,
        "sd_campaigns": sd_campaigns,
        "sp_str":       sp_str,
        "sb_str":       sb_str,
        "portfolios":   portfolios,
        "budget_rules": budget_rules,
        "sheets_found": sheets_found,
        "errors":       errors,
    }


CURRENCY_SYMBOLS = {
    "USD": "$", "GBP": "\u00a3", "EUR": "\u20ac", "CAD": "C$", "AUD": "A$",
    "JPY": "\u00a5", "INR": "\u20b9", "MXN": "MX$", "BRL": "R$", "SEK": "kr",
    "PLN": "z\u0142", "TRY": "\u20ba", "SGD": "S$", "AED": "AED ", "SAR": "SAR ",
    "NOK": "kr", "DKK": "kr", "CHF": "CHF ", "CNY": "\u00a5", "HKD": "HK$",
}


def detect_currency(portfolios):
    """Detect account currency from the Portfolios 'Budget currency code' column.
    Returns (symbol, code, mixed_flag). Defaults to USD when no signal exists;
    cross-currency conversion is not performed offline, so the dominant code is used."""
    if portfolios is None or len(portfolios) == 0:
        return "$", "USD", False
    col = next((c for c in portfolios.columns if "currency code" in str(c).lower()), None)
    if not col:
        return "$", "USD", False
    vals = [str(v).strip().upper() for v in portfolios[col]
            if str(v).strip() and str(v).strip().lower() != "nan"]
    if not vals:
        return "$", "USD", False
    from collections import Counter
    code = Counter(vals).most_common(1)[0][0]
    mixed = len(set(vals)) > 1
    return CURRENCY_SYMBOLS.get(code, code + " "), code, mixed
