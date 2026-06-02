# BulkSheet Auditor 2.0 — Tool Context

## Purpose
Public, free web tool. A user drops an Amazon Ads **bulk sheet**, the app verifies
it's genuine, audits the whole account, scores it 0–100, and shows an interactive
dashboard with a prioritized action plan and CSV exports. No external APIs — fully
offline, file processed in memory only.

## Stack
- **Streamlit** web app (`app.py`) — deployed free on Streamlit Community Cloud
- **python-calamine** for fast parsing (~2s on 40k rows; openpyxl fallback)
- **Plotly** charts, **pandas** data, **Jinja2** for the CLI HTML report
- Rule-based throughout — no LLM calls

## Flow
upload → `src/validator.py` (gatekeeper) → `src/parser.py` → `src/auditor.py`
(4 modules + score) → `src/recommender.py` (ranked templates) → `app.py` dashboard

## Files
- `app.py` — Streamlit UI (validate, KPI cards, charts, filterable tables, exports)
- `main.py` — CLI mode → standalone HTML report
- `config.py` — ALL audit thresholds (tune here)
- `src/validator.py` — confirms it's a real bulk sheet, rejects everything else
- `src/parser.py` — loads/normalizes all sheets; `_make_reader` = calamine fast-path
- `src/auditor.py` — Campaign Health / Keyword Quality / Search Terms / Structure
- `src/recommender.py` — finding code → expert action template, ranked by spend impact
- `src/dashboard.py` — aggregations + Plotly chart builders
- `src/reporter.py` + `templates/report.html` — CLI HTML output

## Dev notes
- Run: `python -m streamlit run app.py` (CLI `streamlit` not on PATH here)
- Local pandas is 2.1.4 (no `engine="calamine"`) — parser uses python-calamine
  directly so it's fast anyway. Cloud uses pandas≥2.2 per requirements.txt.
- Sample bulk file for testing lives in `../07_Audit-Automation/`
- Reuses chart patterns from `../04_Amazon-PPC/src/charts.py`

## Status (Jun 2026)
Working end-to-end: validation, fast parse (2.3s), audit (score/findings), 7
recommendations, interactive dashboard, CSV exports. Streamlit app boots clean.
Not yet deployed to Streamlit Cloud (needs GitHub push).
