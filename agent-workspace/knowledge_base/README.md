# knowledge_base

Turns the workbook into a fact table and a comparison page recruiters can use instead of the spreadsheet.
No web or LLM calls: it reads files that already exist and finishes in seconds.

```bash
cd agent-workspace
uv run --with openpyxl --with beautifulsoup4 --with requests --with pypdf \
  python -m knowledge_base "../EDIT Competitive Analysis Research 2026-2027.xlsx"
```

Each (program, field) value comes from, in order:

1. this year's `field_pipeline` results (`<workbook>_field_pipeline/decisions.csv`): source URL and quote kept
2. a value already typed into this year's workbook
3. last year's workbook (`OLD *.xlsx`, or `--prior`). Schools are matched by name ("Purdue University" =
   "PURDUE UNIVERSITY - WEST LAFAYETTE"); schools that were only in last year's peer set are kept and marked.

## Output (`<workbook>_kb/`)

- `competitive_analysis.html`: one self-contained file (data embedded). Open it in a browser or share it.
  Pick a program type, schools (Purdue is always pinned), field groups and filters ("Out-of-State Tuition
  Total ≤ 60000"). Numbers show the difference from Purdue. Hover a value to see its quote; ↗ opens the source.
  The address bar holds the current view, so a copied link reopens the same comparison. Export CSV downloads it.
- `kb.sqlite`: `programs`, `fields`, `facts` (value, number, cycle, status, source_url, quote), for SQL queries
  or a future question box that turns plain-English questions into filters.
