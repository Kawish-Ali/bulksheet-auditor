"""
reporter.py — Render the audit report as a self-contained HTML file.
"""

from __future__ import annotations
import os
from pathlib import Path
from typing import Any

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .auditor import AuditReport, Finding
from .recommender import Recommendation


# ── Formatters ─────────────────────────────────────────────────────────────────

def _fmt_currency(v) -> str:
    try:
        f = float(v)
        return f"${f:,.2f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_pct(v) -> str:
    try:
        f = float(v)
        return f"{f:.1%}"
    except (TypeError, ValueError):
        return "—"


def _fmt_int(v) -> str:
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return "—"


def _fmt_ratio(v) -> str:
    try:
        f = float(v)
        return f"{f:.2f}x"
    except (TypeError, ValueError):
        return "—"


def _acos_class(v) -> str:
    try:
        f = float(v)
        if f <= 0.20:
            return "acos-green"
        if f <= 0.35:
            return "acos-amber"
        return "acos-red"
    except (TypeError, ValueError):
        return ""


# ── DataFrame → HTML table ────────────────────────────────────────────────────

_CURRENCY_COLS = {"Spend", "Sales", "Daily Budget", "Spend ($)"}
_PCT_COLS = {"ACoS", "CTR", "CVR"}
_INT_COLS = {"Impressions", "Clicks", "Orders", "Terms", "Keyword Count"}
_RATIO_COLS = {"ROAS"}


def _df_to_html(df: pd.DataFrame, max_rows: int = 50) -> str:
    if df is None or df.empty:
        return ""
    display = df.head(max_rows).copy()

    header = "<table><thead><tr>"
    for col in display.columns:
        header += f"<th>{col}</th>"
    header += "</tr></thead><tbody>"

    rows_html = ""
    for _, row in display.iterrows():
        rows_html += "<tr>"
        for col in display.columns:
            val = row[col]
            css = "num"
            if col in _CURRENCY_COLS:
                cell = _fmt_currency(val)
            elif col == "ACoS":
                cell = _fmt_pct(val) if pd.notna(val) else "—"
                css = f"num {_acos_class(val)}" if pd.notna(val) else "num"
            elif col in _PCT_COLS:
                cell = _fmt_pct(val) if pd.notna(val) else "—"
                css = "num"
            elif col in _INT_COLS:
                cell = _fmt_int(val) if pd.notna(val) else "—"
                css = "num"
            elif col in _RATIO_COLS:
                cell = _fmt_ratio(val) if pd.notna(val) else "—"
                css = "num"
            else:
                cell = str(val) if pd.notna(val) else "—"
                css = ""
            rows_html += f'<td class="{css}">{cell}</td>'
        rows_html += "</tr>"

    return header + rows_html + "</tbody></table>"


# ── Finding → template-ready dict ────────────────────────────────────────────

def _prepare_finding(finding: Finding) -> dict:
    table_html = ""
    list_items = []
    row_count = 0

    if isinstance(finding.affected, pd.DataFrame) and not finding.affected.empty:
        row_count = len(finding.affected)
        table_html = _df_to_html(finding.affected)
    elif isinstance(finding.affected, (list, set)):
        list_items = sorted(str(x) for x in finding.affected)

    return {
        "code": finding.code,
        "severity": finding.severity,
        "title": finding.title,
        "detail": finding.detail,
        "table_html": table_html,
        "list_items": list_items,
        "row_count": row_count,
    }


# ── Main render function ──────────────────────────────────────────────────────

def generate_report(
    report: AuditReport,
    recommendations: list[Recommendation],
    output_path: str,
    account_id: str = "",
    date_range: str = "",
    sheets_found: list[str] | None = None,
    errors: list[str] | None = None,
) -> None:
    templates_dir = Path(__file__).parent.parent / "templates"
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
    )

    env.filters["fmt_currency"] = _fmt_currency
    env.filters["fmt_pct"] = _fmt_pct
    env.filters["fmt_int"] = _fmt_int
    env.filters["fmt_ratio"] = _fmt_ratio

    template = env.get_template("report.html")

    # Group findings by module, skip pure-info findings (INTENT_BREAKDOWN shown inline)
    module_order = ["Campaign Health", "Keyword Quality", "Search Terms", "Structure"]
    findings_by_module: dict[str, list[dict]] = {m: [] for m in module_order}
    for f in report.findings:
        if f.module in findings_by_module:
            findings_by_module[f.module].append(_prepare_finding(f))

    ctx = {
        "score": report.score,
        "summary": report.summary,
        "findings_by_module": findings_by_module,
        "recommendations": recommendations,
        "account_id": account_id,
        "date_range": date_range,
        "sheets_found": [s for s in (sheets_found or []) if s not in ("Config", "Sheet10", "Brand Assets Data")],
        "errors": errors or [],
    }

    html = template.render(**ctx)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
