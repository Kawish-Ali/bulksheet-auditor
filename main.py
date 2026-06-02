#!/usr/bin/env python3
"""
BulkSheet Auditor 2.0
Usage:
    python main.py <bulk_sheet.xlsx> [options]

Options:
    --output FILE       Output HTML path (default: audit_report.html)
    --acos-target FLOAT ACoS target threshold, e.g. 0.25 (default: 0.30)
    --brand TERM        Brand term(s) for branded/non-branded split (repeatable)
    --open              Open the report in the browser after generating
"""

import argparse
import os
import sys
import time
import webbrowser
from pathlib import Path

# Add project root to path so src/ imports work
sys.path.insert(0, str(Path(__file__).parent))

import config as cfg
from src.parser import load_bulk_file
from src.auditor import run_audit
from src.recommender import generate_recommendations
from src.reporter import generate_report


def _parse_date_range(sheets_found: list[str], filename: str) -> str:
    """Extract date range from bulk sheet filename if present."""
    name = Path(filename).stem
    parts = name.split("-")
    # bulk-ACCOUNTID-YYYYMMDD-YYYYMMDD-timestamp → indices 2 and 3
    if len(parts) >= 5:
        try:
            start = f"{parts[2][:4]}-{parts[2][4:6]}-{parts[2][6:8]}"
            end   = f"{parts[3][:4]}-{parts[3][4:6]}-{parts[3][6:8]}"
            return f"{start} → {end}"
        except Exception:
            pass
    return ""


def _account_id(filename: str) -> str:
    name = Path(filename).stem
    parts = name.split("-")
    # bulk-ACCOUNTID-... → index 1
    if len(parts) >= 2:
        return parts[1].upper()
    return Path(filename).stem


def _score_label(score: int) -> str:
    if score >= 75:
        return "GOOD"
    if score >= 50:
        return "NEEDS WORK"
    return "CRITICAL"


def _severity_icon(sev: str) -> str:
    return {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(sev, "⚪")


def main():
    parser = argparse.ArgumentParser(description="Amazon PPC Bulk Sheet Auditor 2.0")
    parser.add_argument("file", help="Path to the Amazon PPC bulk sheet (.xlsx)")
    parser.add_argument("--output", default="audit_report.html", help="Output HTML file path")
    parser.add_argument("--acos-target", type=float, default=None, help="ACoS target (e.g. 0.25)")
    parser.add_argument("--brand", action="append", default=[], help="Brand term(s) for classification")
    parser.add_argument("--open", action="store_true", dest="open_browser", help="Open report in browser")
    args = parser.parse_args()

    # Override config threshold if supplied
    if args.acos_target is not None:
        cfg.ACOS_TARGET = args.acos_target

    input_path = args.file
    if not os.path.isfile(input_path):
        print(f"❌  File not found: {input_path}")
        sys.exit(1)

    output_path = args.output
    if not os.path.isabs(output_path):
        output_path = os.path.join(os.path.dirname(os.path.abspath(input_path)), output_path)

    print(f"\n{'─'*56}")
    print(f"  BulkSheet Auditor 2.0")
    print(f"{'─'*56}")
    print(f"  File   : {os.path.basename(input_path)}")
    print(f"  Output : {output_path}")
    if args.brand:
        print(f"  Brand  : {', '.join(args.brand)}")
    print(f"  ACoS target : {cfg.ACOS_TARGET:.0%}")
    print(f"{'─'*56}\n")

    # ── Step 1: Parse ──────────────────────────────────────────────────────────
    print("  [1/3] Parsing bulk sheet...", end=" ", flush=True)
    t0 = time.time()
    data = load_bulk_file(input_path)
    elapsed = time.time() - t0

    sheets = data["sheets_found"]
    print(f"done ({elapsed:.1f}s)")
    print(f"        Sheets loaded: {len(sheets)}")
    for key in ("sp_campaigns", "sb_campaigns", "sd_campaigns", "sp_str", "sb_str"):
        df = data.get(key)
        if df is not None and not df.empty:
            print(f"          ✓ {key}: {len(df):,} rows")
        else:
            print(f"          – {key}: not found")

    if data["errors"]:
        print()
        for e in data["errors"]:
            print(f"        ⚠  {e}")

    # ── Step 2: Audit ──────────────────────────────────────────────────────────
    print(f"\n  [2/3] Running audit modules...", end=" ", flush=True)
    t0 = time.time()
    report = run_audit(data, brand_terms=args.brand if args.brand else None)
    elapsed = time.time() - t0
    print(f"done ({elapsed:.1f}s)")

    # Print findings summary
    by_sev = {"critical": 0, "warning": 0, "info": 0}
    for f in report.findings:
        by_sev[f.severity] = by_sev.get(f.severity, 0) + 1

    print(f"\n  ── Account Summary ─────────────────────")
    s = report.summary
    print(f"     Spend   : ${s.total_spend:>10,.2f}")
    print(f"     Sales   : ${s.total_sales:>10,.2f}")
    print(f"     ACoS    : {s.acos:.1%}" if s.acos else "     ACoS    : —")
    print(f"     ROAS    : {s.roas:.2f}x" if s.roas else "     ROAS    : —")
    print(f"     Clicks  : {s.total_clicks:>10,}")
    print(f"     Orders  : {s.total_orders:>10,}")
    print(f"\n  ── Findings ────────────────────────────")
    print(f"     🔴 Critical : {by_sev['critical']}")
    print(f"     🟡 Warning  : {by_sev['warning']}")
    print(f"     🔵 Info     : {by_sev['info']}")
    print(f"\n  ── Health Score ────────────────────────")
    label = _score_label(report.score)
    print(f"     {report.score}/100  {label}")

    # ── Step 3: Recommendations + Report ──────────────────────────────────────
    print(f"\n  [3/3] Generating recommendations and report...", end=" ", flush=True)
    t0 = time.time()

    recommendations = generate_recommendations(report)

    account_id = _account_id(input_path)
    date_range = _parse_date_range(sheets, input_path)

    generate_report(
        report=report,
        recommendations=recommendations,
        output_path=output_path,
        account_id=account_id,
        date_range=date_range,
        sheets_found=sheets,
        errors=data["errors"],
    )
    elapsed = time.time() - t0
    print(f"done ({elapsed:.1f}s)")

    print(f"\n{'─'*56}")
    print(f"  ✅  Report saved: {output_path}")
    print(f"      {len(recommendations)} recommendations generated")
    print(f"{'─'*56}\n")

    if args.open_browser:
        webbrowser.open(f"file://{os.path.abspath(output_path)}")


if __name__ == "__main__":
    main()
