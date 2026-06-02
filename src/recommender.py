"""
recommender.py — Rule-based recommendation engine.

Each audit finding code maps to an expert-written action template.
Recommendations are ranked by spend impact × severity and the top 7 returned.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any

import pandas as pd

from .auditor import AuditReport, Finding


@dataclass
class Recommendation:
    priority: int
    title: str
    issue: str
    action: str
    expected_impact: str
    affected_summary: str = ""   # short human-readable summary of what's affected


# ── Templates keyed by finding code ───────────────────────────────────────────

TEMPLATES: dict[str, dict] = {
    "WASTED_SEARCH_TERMS": {
        "title": "Add negatives for zero-order search terms",
        "issue": "A significant portion of search term spend is producing clicks but zero orders.",
        "action": (
            "1. Open the flagged search terms table in the report.\n"
            "2. Sort by Spend descending.\n"
            "3. For each term with Spend > $2 and zero orders: add as Exact Negative at campaign level.\n"
            "4. For ambiguous queries (e.g. generic category terms), consider Phrase Negative at account level."
        ),
        "expected_impact": "Immediate spend savings; budget redirected to converting terms; ACoS improvement within 7–14 days.",
        "base_priority": 1,
    },
    "HIGH_ACOS_CAMPAIGN": {
        "title": "Reduce bids on high-ACoS campaigns",
        "issue": "One or more campaigns are running at ACoS above the critical threshold.",
        "action": (
            "1. Review each flagged campaign's keyword breakdown.\n"
            "2. Identify the top-spending keywords driving the high ACoS.\n"
            "3. Lower CPC bids by 20–30% on non-converting keywords.\n"
            "4. If the campaign has no orders in 30 days, consider pausing."
        ),
        "expected_impact": "Reduces wasted spend; improves blended ACoS across the account.",
        "base_priority": 2,
    },
    "WASTED_CAMPAIGN_SPEND": {
        "title": "Pause or restructure zero-order campaigns",
        "issue": "Campaigns are spending budget without generating any orders.",
        "action": (
            "1. Check the date range — if 0 orders in 14+ days with significant spend, pause the campaign.\n"
            "2. Before pausing, review if the issue is search term relevance (check STR tab).\n"
            "3. If targeting is too broad, restructure into tighter ad groups with Exact match."
        ),
        "expected_impact": "Stops budget hemorrhage; frees budget for proven campaigns.",
        "base_priority": 3,
    },
    "WASTED_KEYWORD_SPEND": {
        "title": "Lower bids on zero-order keywords",
        "issue": "Keywords are consuming budget without converting.",
        "action": (
            "1. Export the flagged keywords list.\n"
            "2. For keywords with Spend > $5 and 0 orders: reduce bid by 30–40%.\n"
            "3. For keywords with Spend > $15 and 0 orders: pause the keyword and review search terms."
        ),
        "expected_impact": "Reduces wasted spend at keyword level; improves CVR.",
        "base_priority": 4,
    },
    "HIGH_ACOS_KEYWORDS": {
        "title": "Reduce bids on critical-ACoS keywords",
        "issue": "Individual keywords are running at ACoS above the critical threshold.",
        "action": (
            "1. Review flagged keywords in bulk sheet.\n"
            "2. Lower CPC bids by 25–35% on each.\n"
            "3. Check if these are Broad match — if so, add exact negatives for the wasted search terms driving the ACoS."
        ),
        "expected_impact": "Direct ACoS reduction on the most expensive keywords.",
        "base_priority": 5,
    },
    "QUICK_WIN_EXACT_PROMOTE": {
        "title": "Promote winning search terms to Exact match keywords",
        "issue": "High-converting search terms are not yet controlled with Exact match bids.",
        "action": (
            "1. Open the Quick Win search terms table.\n"
            "2. Add each term as an Exact match keyword in a dedicated campaign or tight ad group.\n"
            "3. Set an aggressive bid (start at 1.2× current CPC).\n"
            "4. Add these terms as Exact Negatives in the Auto/Broad campaigns to prevent overlap."
        ),
        "expected_impact": "Scale proven search terms; prevent overbidding in Auto; improve ROAS.",
        "base_priority": 6,
    },
    "BROAD_MATCH_DOMINANCE": {
        "title": "Rebalance match type distribution toward Exact/Phrase",
        "issue": "More than 50% of keyword spend is on Broad match, leading to low-quality traffic.",
        "action": (
            "1. Identify top-spending Broad match keywords.\n"
            "2. For each Broad keyword: review its search terms (STR).\n"
            "3. Promote converting search terms to Exact match; add wasted terms as negatives.\n"
            "4. Lower Broad match bids by 20% and increase Exact match bids by 10–15%."
        ),
        "expected_impact": "Better traffic quality; higher CVR; lower wasted spend.",
        "base_priority": 7,
    },
    "ZERO_IMP_KEYWORDS": {
        "title": "Investigate zero-impression keywords",
        "issue": "A large share of enabled keywords are receiving no impressions.",
        "action": (
            "1. Check if bids are below the suggested bid range in Campaign Manager.\n"
            "2. Check keyword relevance — are these terms actually searched on Amazon?\n"
            "3. Review competition level — very competitive terms may need higher bids.\n"
            "4. Consider adding these as Phrase match if only running as Exact."
        ),
        "expected_impact": "Activates dormant keywords; expands reach without new budget.",
        "base_priority": 8,
    },
    "ZERO_IMP_CAMPAIGN": {
        "title": "Diagnose zero-impression campaigns",
        "issue": "Enabled campaigns are receiving zero impressions.",
        "action": (
            "1. Check if daily budget was exhausted early in the day.\n"
            "2. Check if all keywords in the campaign are below threshold bid.\n"
            "3. Review targeting — Product Attribute Targeting campaigns may have strict category filters.\n"
            "4. If budget-limited: increase budget or use dynamic bidding."
        ),
        "expected_impact": "Restores traffic flow to active campaigns.",
        "base_priority": 9,
    },
    "BLOATED_AD_GROUPS": {
        "title": "Split over-sized ad groups into focused groups",
        "issue": "Ad groups contain too many keywords, making bid management imprecise.",
        "action": (
            "1. Download the flagged ad group's keyword list.\n"
            "2. Group keywords by theme or intent (brand vs. generic, match type).\n"
            "3. Create separate ad groups per theme.\n"
            "4. Use Single Keyword Ad Groups (SKAGs) for your top 10–20 converting terms."
        ),
        "expected_impact": "More precise bidding; better Quality Score; easier reporting.",
        "base_priority": 10,
    },
    "NO_NEGATIVES": {
        "title": "Add negative keyword lists to campaigns without negatives",
        "issue": "Campaigns are running without any negative keywords, exposing budget to irrelevant queries.",
        "action": (
            "1. Create a shared negative keyword list with your brand's standard exclusions (competitor names, irrelevant categories, non-buyer intent terms).\n"
            "2. Apply to all flagged campaigns.\n"
            "3. Run the Search Term Report after 7 days and add new wasted terms as exact negatives."
        ),
        "expected_impact": "Immediate traffic quality improvement; lower wasted spend.",
        "base_priority": 11,
    },
    "HIGH_ACOS_SEARCH_TERMS": {
        "title": "Negate or bid down high-ACoS search terms",
        "issue": "Search terms with high ACoS are inflating the account-level ACoS.",
        "action": (
            "1. Review flagged terms in the High-ACoS Search Terms table.\n"
            "2. For terms with ACoS > 80% and multiple orders: lower the keyword bid by 25%.\n"
            "3. For terms with ACoS > 80% and zero orders: add as Exact Negative."
        ),
        "expected_impact": "Reduces ACoS drag from low-quality search queries.",
        "base_priority": 12,
    },
}

SEVERITY_WEIGHT = {"critical": 3, "warning": 2, "info": 0}


def _spend_from_finding(finding: Finding) -> float:
    if finding.affected is None:
        return 0.0
    if isinstance(finding.affected, pd.DataFrame) and "Spend" in finding.affected.columns:
        return float(finding.affected["Spend"].sum())
    return 0.0


def _affected_summary(finding: Finding) -> str:
    if finding.affected is None:
        return ""
    if isinstance(finding.affected, pd.DataFrame):
        n = len(finding.affected)
        if "Spend" in finding.affected.columns:
            spend = finding.affected["Spend"].sum()
            return f"{n:,} rows · ${spend:,.2f} total spend"
        return f"{n:,} rows"
    if isinstance(finding.affected, (list, set)):
        return f"{len(finding.affected):,} affected"
    return ""


def generate_recommendations(report: AuditReport) -> list[Recommendation]:
    """
    Convert audit findings to ranked, actionable recommendations.
    Returns top 7 sorted by priority score (spend impact × severity weight).
    """
    recs: list[tuple[float, Recommendation]] = []

    for finding in report.findings:
        template = TEMPLATES.get(finding.code)
        if not template:
            continue

        spend = _spend_from_finding(finding)
        sev_weight = SEVERITY_WEIGHT.get(finding.severity, 0)
        if sev_weight == 0:
            continue

        # Priority score: higher spend + higher severity = higher rank
        priority_score = sev_weight * 100 + spend
        base_priority = template["base_priority"]

        rec = Recommendation(
            priority=base_priority,
            title=template["title"],
            issue=template["issue"],
            action=template["action"],
            expected_impact=template["expected_impact"],
            affected_summary=_affected_summary(finding),
        )
        recs.append((priority_score, rec))

    # Sort by score descending, then re-number priorities 1..N
    recs.sort(key=lambda x: x[0], reverse=True)
    result = []
    for i, (_, rec) in enumerate(recs[:7], start=1):
        rec.priority = i
        result.append(rec)

    return result
