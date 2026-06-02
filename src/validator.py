"""
validator.py — Verify that an uploaded file is a genuine Amazon Ads bulk sheet.

A real bulk sheet (downloaded from Amazon Ads > Bulk Operations) is an .xlsx
with one or more campaign-management sheets and a recognisable column
signature (Entity / Operation / Campaign ID etc.). This rejects random
spreadsheets, Business Reports, Search Term Reports exported on their own,
Brand Analytics files, CSVs renamed to xlsx, etc.
"""

from __future__ import annotations
from dataclasses import dataclass, field

import pandas as pd

from .parser import open_workbook

# Sheets that only appear in a real bulk file
CAMPAIGN_SHEETS = {
    "Sponsored Products Campaigns",
    "Sponsored Brands Campaigns",
    "SB Multi Ad Group Campaigns",
    "Sponsored Display Campaigns",
}

# Column tokens that are characteristic of bulk sheets (not STR-only exports)
SIGNATURE_COLUMNS = {
    "Product", "Entity", "Operation", "Campaign ID",
}

MIN_SIGNATURE_HITS = 2  # need at least this many signature columns to pass


@dataclass
class ValidationResult:
    is_valid: bool
    file_type: str = "Unknown"
    reason: str = ""
    sheets_found: list[str] = field(default_factory=list)
    campaign_sheets: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _guess_file_type(sheet_names: list[str]) -> str:
    s = set(sheet_names)
    if s & CAMPAIGN_SHEETS:
        return "Amazon Ads Bulk Sheet"
    joined = " ".join(sheet_names).lower()
    if "search term" in joined:
        return "Search Term Report (not a full bulk sheet)"
    if "business report" in joined or "detail page sales" in joined:
        return "Business Report"
    if "search query" in joined or "brand analytics" in joined:
        return "Brand Analytics export"
    return "Generic spreadsheet"


def validate_bulk_file(source) -> ValidationResult:
    """
    Inspect an uploaded file (path or file-like) WITHOUT fully parsing it.
    Reads only sheet names + the header row of each candidate sheet, so it
    stays fast even on large files.
    """
    # 1. Must open as an Excel workbook
    try:
        xl = open_workbook(source)
    except Exception as e:
        return ValidationResult(
            is_valid=False,
            file_type="Not an Excel file",
            reason=(
                "This file couldn't be opened as an Excel workbook (.xlsx). "
                "Amazon bulk sheets are downloaded from Ads Console → Bulk Operations "
                "and come as .xlsx files. If you have a .csv, re-download the bulk file as Excel."
            ),
        )

    sheet_names = list(xl.sheet_names)
    file_type = _guess_file_type(sheet_names)

    # 2. Must contain at least one campaign-management sheet
    present_campaign_sheets = [s for s in sheet_names if s in CAMPAIGN_SHEETS]
    if not present_campaign_sheets:
        return ValidationResult(
            is_valid=False,
            file_type=file_type,
            reason=(
                f"This looks like a “{file_type}”, not a full Amazon Ads bulk sheet. "
                "A bulk sheet must contain at least one campaign sheet such as "
                "“Sponsored Products Campaigns”. Please download the bulk file from "
                "Amazon Ads Console → Sponsored ads → Bulk operations."
            ),
            sheets_found=sheet_names,
        )

    # 3. The campaign sheet must carry the bulk-sheet column signature
    warnings: list[str] = []
    signature_ok = False
    for sheet in present_campaign_sheets:
        try:
            head = pd.read_excel(xl, sheet_name=sheet, nrows=0)
        except Exception:
            continue
        cols = set(str(c) for c in head.columns)
        hits = sum(1 for token in SIGNATURE_COLUMNS
                   if any(token.lower() in c.lower() for c in cols))
        if hits >= MIN_SIGNATURE_HITS:
            signature_ok = True
            break

    if not signature_ok:
        return ValidationResult(
            is_valid=False,
            file_type=file_type,
            reason=(
                "A campaign sheet was found, but it's missing the expected bulk-sheet "
                "columns (Entity, Operation, Campaign ID, etc.). The file may be edited, "
                "truncated, or not a genuine Amazon bulk export."
            ),
            sheets_found=sheet_names,
            campaign_sheets=present_campaign_sheets,
        )

    # Passed — note any missing-but-useful sheets
    if "SP Search Term Report" not in sheet_names:
        warnings.append("No SP Search Term Report sheet — search-term analysis will be limited.")

    return ValidationResult(
        is_valid=True,
        file_type="Amazon Ads Bulk Sheet",
        reason="Valid Amazon Ads bulk sheet.",
        sheets_found=sheet_names,
        campaign_sheets=present_campaign_sheets,
        warnings=warnings,
    )
