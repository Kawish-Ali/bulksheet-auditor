# BulkSheet Auditor 2.0

Public web tool: a user drops their Amazon Ads **bulk sheet**, the app verifies
it's a genuine bulk file, audits the whole account against PPC best-practice
rules, scores it, and renders an **interactive dashboard** with a prioritized
action plan and CSV exports. Fully offline — no external APIs, no Claude calls.
The uploaded file is processed in memory and never stored.

## What it does

1. **Validate** — confirms the upload is a real Amazon Ads bulk sheet (rejects
   Business Reports, lone Search Term Reports, random spreadsheets, CSVs, etc.)
2. **Parse** — reads every sheet (SP / SB / SB Multi / SD campaigns, both Search
   Term Reports, Portfolios, Budget Rules) with python-calamine (~2s on a 40k-row file)
3. **Audit** — 4 rule modules: Campaign Health, Keyword Quality, Search Terms, Structure
4. **Score** — 0–100 account health score
5. **Recommend** — rule-based, expert-written action templates ranked by spend impact
6. **Dashboard** — KPI cards, charts, filterable/sortable tables, CSV exports

## Run locally

```bash
pip install -r requirements.txt
python -m streamlit run app.py
```

Open http://localhost:8501 and upload a bulk sheet.

### CLI mode (HTML report instead of dashboard)

```bash
python main.py path/to/bulk_sheet.xlsx --output report.html --open
```

## Deploy free & public (Streamlit Community Cloud)

1. Push this folder to a GitHub repo.
2. Go to https://share.streamlit.io → **New app**.
3. Point it at the repo, set **Main file path** to `app.py`.
4. Deploy. You get a public `*.streamlit.app` URL, free.

`requirements.txt` pins `pandas>=2.2` so the cloud build uses the fast calamine
engine automatically. `.streamlit/config.toml` sets the theme and a 300 MB upload cap.

## Layout

```
app.py              Streamlit web app (upload → validate → dashboard)
main.py             CLI entrypoint (→ HTML report)
config.py           All audit thresholds
src/validator.py    Bulk-file verification (the gatekeeper)
src/parser.py       Sheet loading + normalization (calamine fast-path)
src/auditor.py      4-module audit engine + scoring
src/recommender.py  Findings → ranked action templates
src/dashboard.py    Aggregations + Plotly charts
src/reporter.py     HTML report renderer (CLI mode)
templates/report.html
```

## Tuning

All thresholds live in `config.py` (ACoS targets, waste limits, broad-match cap,
etc.). Adjust there — every module reads from it.
