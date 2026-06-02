"""
auditor.py — 4-module rule-based audit engine for Amazon PPC bulk files.

Modules:
  1. Campaign Health
  2. Keyword Quality
  3. Search Term Analysis
  4. Structure Audit

Each module returns a list of Finding dicts. The engine also computes an
overall account health score (0–100).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import numpy as np

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config as cfg


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class Finding:
    code: str           # machine-readable key used by recommender
    module: str         # Campaign Health / Keyword Quality / Search Terms / Structure
    severity: str       # critical / warning / info
    title: str
    detail: str
    affected: Any = None   # DataFrame or list of strings


@dataclass
class SummaryMetrics:
    total_spend: float = 0.0
    total_sales: float = 0.0
    total_clicks: int = 0
    total_impressions: int = 0
    total_orders: int = 0
    acos: float | None = None
    roas: float | None = None
    ctr: float | None = None
    cvr: float | None = None
    cpc: float | None = None


@dataclass
class AuditReport:
    score: int = 100
    summary: SummaryMetrics = field(default_factory=SummaryMetrics)
    findings: list[Finding] = field(default_factory=list)
    date_range: str = ""
    account_id: str = ""


# ── Helpers ────────────────────────────────────────────────────────────────────

def _safe_div(num: float, den: float) -> float | None:
    if den and den > 0:
        return num / den
    return None


def _campaign_level(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate keyword/entity rows up to campaign level."""
    if df.empty or "Campaign Name" not in df.columns:
        return pd.DataFrame(columns=["Campaign Name", "Impressions", "Clicks",
                                     "Spend", "Sales", "Orders", "State",
                                     "Portfolio", "ACoS"])
    agg_spec: dict = {
        "Impressions": ("Impressions", "sum"),
        "Clicks": ("Clicks", "sum"),
        "Spend": ("Spend", "sum"),
        "Sales": ("Sales", "sum"),
        "Orders": ("Orders", "sum"),
    }
    if "State" in df.columns:
        agg_spec["State"] = ("State", "first")
    if "Portfolio" in df.columns:
        agg_spec["Portfolio"] = ("Portfolio", "first")

    grp = df.groupby("Campaign Name", as_index=False).agg(**agg_spec)

    if "State" not in grp.columns:
        grp["State"] = "enabled"
    if "Portfolio" not in grp.columns:
        grp["Portfolio"] = ""

    grp["ACoS"] = grp.apply(
        lambda r: _safe_div(r["Spend"], r["Sales"]), axis=1
    )
    return grp


def _add_acos(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ACoS"] = df.apply(lambda r: _safe_div(r["Spend"], r["Sales"]), axis=1)
    return df


# ── Module 1: Campaign Health ──────────────────────────────────────────────────

def audit_campaigns(
    sp_df: pd.DataFrame,
    sb_df: pd.DataFrame,
    sd_df: pd.DataFrame,
) -> list[Finding]:
    findings = []
    _CAMP_COLS = ["Campaign Name", "Portfolio", "Impressions", "Clicks", "Spend", "Sales", "Orders", "State"]
    frames = []
    for df in (sp_df, sb_df, sd_df):
        if not df.empty:
            df_clean = df.loc[:, ~df.columns.duplicated()].copy()
            available = [c for c in _CAMP_COLS if c in df_clean.columns]
            frames.append(df_clean[available])

    if not frames:
        return findings

    all_campaigns = pd.concat(frames, ignore_index=True)
    camps = _campaign_level(all_campaigns)
    if camps.empty:
        return findings

    enabled = camps[camps["State"] == "enabled"].copy()

    # Zero-impression enabled campaigns
    zero_imp = enabled[enabled["Impressions"] == 0]
    if not zero_imp.empty:
        findings.append(Finding(
            code="ZERO_IMP_CAMPAIGN",
            module="Campaign Health",
            severity="warning",
            title=f"{len(zero_imp)} enabled campaign(s) have zero impressions",
            detail="These campaigns are active but receiving no traffic. Likely budget exhausted early, bid too low, or targeting too narrow.",
            affected=zero_imp[["Campaign Name", "Portfolio", "Spend"]],
        ))

    # High ACoS campaigns (critical)
    wasted = enabled[(enabled["ACoS"].notna()) & (enabled["ACoS"] > cfg.ACOS_CRITICAL)]
    if not wasted.empty:
        findings.append(Finding(
            code="HIGH_ACOS_CAMPAIGN",
            module="Campaign Health",
            severity="critical",
            title=f"{len(wasted)} campaign(s) with ACoS > {cfg.ACOS_CRITICAL:.0%}",
            detail=f"Campaigns spending heavily with poor return. ACoS above {cfg.ACOS_CRITICAL:.0%} is a critical signal — reduce bids or pause.",
            affected=wasted[["Campaign Name", "Portfolio", "Spend", "Sales", "ACoS"]].sort_values("Spend", ascending=False),
        ))

    # Campaigns with spend but zero orders
    spend_no_orders = enabled[(enabled["Spend"] > cfg.MIN_SPEND_FILTER) & (enabled["Orders"] == 0)]
    if not spend_no_orders.empty:
        findings.append(Finding(
            code="WASTED_CAMPAIGN_SPEND",
            module="Campaign Health",
            severity="warning",
            title=f"{len(spend_no_orders)} campaign(s) spending with zero orders",
            detail=f"Total wasted spend: ${spend_no_orders['Spend'].sum():,.2f}. Consider pausing or restructuring these campaigns.",
            affected=spend_no_orders[["Campaign Name", "Portfolio", "Spend", "Impressions", "Clicks"]].sort_values("Spend", ascending=False),
        ))

    return findings


# ── Module 2: Keyword Quality ──────────────────────────────────────────────────

def audit_keywords(sp_df: pd.DataFrame) -> list[Finding]:
    findings = []
    if sp_df.empty:
        return findings

    # Only keyword entity rows — exclude campaign-header, ad-group, product-ad rows
    kw_df = sp_df.copy()
    if "Entity" in kw_df.columns:
        kw_df = kw_df[kw_df["Entity"].str.lower().isin(["keyword", "negative keyword"])].copy()

    if kw_df.empty:
        kw_df = sp_df.copy()

    enabled_kw = kw_df[kw_df["State"] == "enabled"].copy()
    if enabled_kw.empty:
        return findings

    total_kw = len(enabled_kw)

    # Zero-impression keywords with non-zero spend
    zero_imp = enabled_kw[(enabled_kw["Impressions"] == 0) & (enabled_kw["Spend"] == 0)]
    if total_kw > 0:
        zero_pct = len(zero_imp) / total_kw
        if zero_pct > cfg.ZERO_IMP_THRESH:
            findings.append(Finding(
                code="ZERO_IMP_KEYWORDS",
                module="Keyword Quality",
                severity="warning",
                title=f"{zero_pct:.0%} of enabled keywords have zero impressions ({len(zero_imp):,}/{total_kw:,})",
                detail="Most keywords are not getting any traffic. Check bids, match types, and keyword relevance.",
                affected=zero_imp[["Campaign Name", "Ad Group Name", "Keyword Text", "Match Type"]].head(50),
            ))

    # Wasted keyword spend (spend but zero orders)
    enabled_kw = _add_acos(enabled_kw)
    total_spend = enabled_kw["Spend"].sum()
    wasted_kw = enabled_kw[(enabled_kw["Spend"] > cfg.MIN_SPEND_FILTER) & (enabled_kw["Orders"] == 0)]
    wasted_spend = wasted_kw["Spend"].sum()
    if total_spend > 0:
        wasted_pct = wasted_spend / total_spend
        if wasted_pct > cfg.WASTED_KW_THRESH:
            findings.append(Finding(
                code="WASTED_KEYWORD_SPEND",
                module="Keyword Quality",
                severity="warning",
                title=f"{wasted_pct:.0%} of keyword spend wasted (${wasted_spend:,.2f} with zero orders)",
                detail="These keywords are consuming budget without converting. Lower bids or add as negatives.",
                affected=wasted_kw[["Campaign Name", "Ad Group Name", "Keyword Text", "Match Type", "Spend", "Clicks"]].sort_values("Spend", ascending=False).head(30),
            ))

    # Match type distribution
    match_spend = enabled_kw.groupby("Match Type")["Spend"].sum()
    if total_spend > 0 and "Broad" in match_spend.index:
        broad_pct = match_spend.get("Broad", 0) / total_spend
        if broad_pct > cfg.BROAD_MATCH_THRESH:
            findings.append(Finding(
                code="BROAD_MATCH_DOMINANCE",
                module="Keyword Quality",
                severity="warning",
                title=f"{broad_pct:.0%} of keyword spend is on Broad match",
                detail="Heavy Broad match reliance leads to irrelevant traffic. Shift budget toward Exact and Phrase.",
                affected=match_spend.reset_index().rename(columns={"Spend": "Spend ($)"}),
            ))

    # High ACoS keywords
    high_acos_kw = enabled_kw[(enabled_kw["ACoS"].notna()) & (enabled_kw["ACoS"] > cfg.ACOS_CRITICAL)]
    if not high_acos_kw.empty:
        findings.append(Finding(
            code="HIGH_ACOS_KEYWORDS",
            module="Keyword Quality",
            severity="critical",
            title=f"{len(high_acos_kw)} keyword(s) with ACoS > {cfg.ACOS_CRITICAL:.0%}",
            detail="Individual keywords burning spend at critical ACoS. Reduce bids on these immediately.",
            affected=high_acos_kw[["Campaign Name", "Keyword Text", "Match Type", "Spend", "Sales", "ACoS"]].sort_values("Spend", ascending=False).head(30),
        ))

    return findings


# ── Module 3: Search Term Analysis ────────────────────────────────────────────

def _word_count(term: str) -> int:
    if pd.isna(term):
        return 0
    return len(str(term).split())


def _classify_intent(term: str, brand_terms: list[str] | None = None) -> str:
    if pd.isna(term):
        return "Unknown"
    t = str(term).lower()
    if brand_terms:
        for b in brand_terms:
            if b.lower() in t:
                return "Brand"
    wc = _word_count(term)
    if wc >= 5:
        return "Long-tail (5+w)"
    if wc == 4:
        return "Long-tail (4w)"
    if wc in (2, 3):
        return "Mid-tail"
    return "Short-tail (1w)"


def audit_search_terms(
    sp_str: pd.DataFrame,
    sb_str: pd.DataFrame,
    brand_terms: list[str] | None = None,
) -> list[Finding]:
    findings = []

    _STR_COLS = ["Customer Search Term", "Keyword Text", "Campaign Name", "Match Type",
                 "Impressions", "Clicks", "Spend", "Sales", "Orders", "Portfolio"]
    frames = []
    for df in (sp_str, sb_str):
        if not df.empty:
            df_clean = df.loc[:, ~df.columns.duplicated()].copy()
            available = [c for c in _STR_COLS if c in df_clean.columns]
            frames.append(df_clean[available])
    if not frames:
        return findings

    str_df = pd.concat(frames, ignore_index=True)
    str_df = _add_acos(str_df)

    # Use Customer Search Term column if available, else Keyword Text
    term_col = "Customer Search Term" if "Customer Search Term" in str_df.columns else "Keyword Text"

    total_spend = str_df["Spend"].sum()

    # Wasted search terms (spend, zero orders)
    wasted_st = str_df[(str_df["Spend"] > cfg.MIN_SPEND_FILTER) & (str_df["Orders"] == 0)].copy()
    wasted_st_spend = wasted_st["Spend"].sum()
    if total_spend > 0:
        wasted_pct = wasted_st_spend / total_spend
        if wasted_pct > cfg.WASTED_ST_THRESH:
            findings.append(Finding(
                code="WASTED_SEARCH_TERMS",
                module="Search Terms",
                severity="critical",
                title=f"{wasted_pct:.0%} of search term spend wasted (${wasted_st_spend:,.2f} — {len(wasted_st):,} terms)",
                detail="These search terms received clicks and spend but zero orders. Add as exact negative keywords.",
                affected=wasted_st[[term_col, "Campaign Name", "Spend", "Clicks", "Match Type"]].sort_values("Spend", ascending=False).head(50),
            ))
        elif not wasted_st.empty:
            findings.append(Finding(
                code="WASTED_SEARCH_TERMS",
                module="Search Terms",
                severity="warning",
                title=f"{len(wasted_st):,} search terms with spend but no orders (${wasted_st_spend:,.2f})",
                detail="Add high-spend zero-order terms as negative keywords.",
                affected=wasted_st[[term_col, "Campaign Name", "Spend", "Clicks"]].sort_values("Spend", ascending=False).head(50),
            ))

    # Quick-win terms (low ACoS, has orders) — promote to Exact/Phrase
    quick_wins = str_df[
        (str_df["ACoS"].notna()) &
        (str_df["ACoS"] < cfg.QUICK_WIN_ACOS_MAX) &
        (str_df["Orders"] >= cfg.QUICK_WIN_MIN_ORDERS)
    ].copy()
    if not quick_wins.empty:
        findings.append(Finding(
            code="QUICK_WIN_EXACT_PROMOTE",
            module="Search Terms",
            severity="info",
            title=f"{len(quick_wins):,} converting search terms with ACoS < {cfg.QUICK_WIN_ACOS_MAX:.0%} — promote to Exact",
            detail="These terms are converting well. Add them as Exact match keywords to control bidding and capture more volume.",
            affected=quick_wins[[term_col, "Campaign Name", "Spend", "Sales", "Orders", "ACoS"]].sort_values("Orders", ascending=False).head(50),
        ))

    # High-ACoS search terms
    high_acos_st = str_df[
        (str_df["ACoS"].notna()) &
        (str_df["ACoS"] > cfg.ACOS_HIGH) &
        (str_df["Spend"] > cfg.MIN_SPEND_FILTER)
    ]
    if not high_acos_st.empty:
        findings.append(Finding(
            code="HIGH_ACOS_SEARCH_TERMS",
            module="Search Terms",
            severity="warning",
            title=f"{len(high_acos_st):,} search terms with ACoS > {cfg.ACOS_HIGH:.0%}",
            detail="High-cost search terms with poor conversion. Consider negating or lowering bids on these.",
            affected=high_acos_st[[term_col, "Campaign Name", "Spend", "Sales", "ACoS"]].sort_values("Spend", ascending=False).head(30),
        ))

    # Intent distribution
    str_df["Intent"] = str_df[term_col].apply(lambda t: _classify_intent(t, brand_terms))
    intent_breakdown = str_df.groupby("Intent").agg(
        Terms=("Spend", "count"),
        Spend=("Spend", "sum"),
        Orders=("Orders", "sum"),
    ).reset_index().sort_values("Spend", ascending=False)
    findings.append(Finding(
        code="INTENT_BREAKDOWN",
        module="Search Terms",
        severity="info",
        title="Search term intent distribution",
        detail="Breakdown of search terms by query type (brand, long-tail, mid-tail, short-tail).",
        affected=intent_breakdown,
    ))

    return findings


# ── Module 4: Structure Audit ──────────────────────────────────────────────────

def audit_structure(sp_df: pd.DataFrame) -> list[Finding]:
    findings = []
    if sp_df.empty:
        return findings

    kw_df = sp_df.copy()
    if "Entity" in kw_df.columns:
        keywords = kw_df[kw_df["Entity"].str.lower() == "keyword"]
        negatives = kw_df[kw_df["Entity"].str.lower() == "negative keyword"]
    else:
        keywords = kw_df
        negatives = pd.DataFrame()

    # Ad groups with too many keywords
    if "Ad Group Name" in keywords.columns and "Campaign Name" in keywords.columns:
        ag_kw_count = (
            keywords.groupby(["Campaign Name", "Ad Group Name"])
            .size()
            .reset_index(name="Keyword Count")
        )
        bloated_ags = ag_kw_count[ag_kw_count["Keyword Count"] > cfg.SINGLE_KW_AG_LIMIT]
        if not bloated_ags.empty:
            findings.append(Finding(
                code="BLOATED_AD_GROUPS",
                module="Structure",
                severity="warning",
                title=f"{len(bloated_ags)} ad group(s) with > {cfg.SINGLE_KW_AG_LIMIT} keywords",
                detail="Ad groups with too many keywords make bid management difficult. Split into tighter, more focused ad groups.",
                affected=bloated_ags.sort_values("Keyword Count", ascending=False),
            ))

    # Campaigns with no negatives
    if not negatives.empty and "Campaign Name" in keywords.columns:
        campaigns_with_negs = set(negatives["Campaign Name"].dropna().unique())
        all_campaigns = set(keywords["Campaign Name"].dropna().unique())
        no_neg_camps = all_campaigns - campaigns_with_negs
        if no_neg_camps:
            findings.append(Finding(
                code="NO_NEGATIVES",
                module="Structure",
                severity="warning",
                title=f"{len(no_neg_camps)} campaign(s) have zero negative keywords",
                detail="Campaigns without negatives waste spend on irrelevant searches. Add negative keyword lists.",
                affected=sorted(no_neg_camps),
            ))

    return findings


# ── Score calculation ──────────────────────────────────────────────────────────

def compute_score(findings: list[Finding], summary: SummaryMetrics) -> int:
    score = 100

    codes = {f.code for f in findings}
    severity_map = {f.code: f.severity for f in findings}

    deductions = {
        "WASTED_SEARCH_TERMS":   {"critical": 20, "warning": 10},
        "HIGH_ACOS_CAMPAIGN":    {"critical": 15, "warning": 8},
        "WASTED_CAMPAIGN_SPEND": {"critical": 10, "warning": 5},
        "ZERO_IMP_CAMPAIGN":     {"warning": 5},
        "HIGH_ACOS_KEYWORDS":    {"critical": 10, "warning": 5},
        "WASTED_KEYWORD_SPEND":  {"warning": 5},
        "ZERO_IMP_KEYWORDS":     {"warning": 10},
        "BROAD_MATCH_DOMINANCE": {"warning": 5},
        "NO_NEGATIVES":          {"warning": 5},
        "BLOATED_AD_GROUPS":     {"warning": 5},
    }

    for code, sev_map in deductions.items():
        if code in codes:
            sev = severity_map.get(code, "info")
            score -= sev_map.get(sev, 0)

    # Extra penalty if overall ACoS is above target
    if summary.acos and summary.acos > cfg.ACOS_TARGET:
        score -= 15

    return max(0, min(100, score))


# ── Summary metrics ────────────────────────────────────────────────────────────

def compute_summary(
    sp_campaigns: pd.DataFrame,
    sb_campaigns: pd.DataFrame,
    sd_campaigns: pd.DataFrame,
    sp_str: pd.DataFrame,
    sb_str: pd.DataFrame,
) -> SummaryMetrics:
    all_frames = [
        df for df in (sp_campaigns, sb_campaigns, sd_campaigns) if not df.empty
    ]
    if not all_frames:
        return SummaryMetrics()

    # Sum account totals robustly across pandas versions. Prefer "Campaign"
    # entity rows (campaign aggregates) to avoid double-counting ad-group/
    # keyword rows; fall back to all rows. Every column access is guarded so
    # a missing or renamed column can never raise KeyError.
    parts = []
    for df in all_frames:
        d = df.loc[:, ~df.columns.duplicated()].copy()
        if "Entity" in d.columns:
            camp_rows = d[d["Entity"].astype(str).str.strip().str.lower() == "campaign"]
            if not camp_rows.empty:
                d = camp_rows
        parts.append(d)
    combined = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()

    def _col_sum(col):
        if col in combined.columns:
            return float(pd.to_numeric(combined[col], errors="coerce").fillna(0).sum())
        return 0.0

    spend = _col_sum("Spend")
    sales = _col_sum("Sales")
    clicks = int(_col_sum("Clicks"))
    impressions = int(_col_sum("Impressions"))
    orders = int(_col_sum("Orders"))

    return SummaryMetrics(
        total_spend=spend,
        total_sales=sales,
        total_clicks=clicks,
        total_impressions=impressions,
        total_orders=orders,
        acos=_safe_div(spend, sales),
        roas=_safe_div(sales, spend),
        ctr=_safe_div(clicks, impressions),
        cvr=_safe_div(orders, clicks),
        cpc=_safe_div(spend, clicks),
    )


# ── Main entry point ───────────────────────────────────────────────────────────

def run_audit(data: dict, brand_terms: list[str] | None = None) -> AuditReport:
    """
    Run all 4 audit modules on the parsed bulk file data.

    Args:
        data: dict returned by parser.load_bulk_file()
        brand_terms: list of brand name strings for branded/non-branded classification

    Returns:
        AuditReport with score, summary metrics, and all findings
    """
    sp = data.get("sp_campaigns", pd.DataFrame())
    sb = data.get("sb_campaigns", pd.DataFrame())
    sd = data.get("sd_campaigns", pd.DataFrame())
    sp_str = data.get("sp_str", pd.DataFrame())
    sb_str = data.get("sb_str", pd.DataFrame())

    summary = compute_summary(sp, sb, sd, sp_str, sb_str)

    findings: list[Finding] = []
    findings.extend(audit_campaigns(sp, sb, sd))
    findings.extend(audit_keywords(sp))
    findings.extend(audit_search_terms(sp_str, sb_str, brand_terms))
    findings.extend(audit_structure(sp))

    score = compute_score(findings, summary)

    return AuditReport(
        score=score,
        summary=summary,
        findings=findings,
    )
