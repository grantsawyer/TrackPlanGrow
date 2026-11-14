"""Importing bank files of any layout into any account (docs/import-plan.md; user's request and decisions 2026-11-13).

    readers.py   a file -> rows of text cells (CSV now; .xlsx, OFX / QFX, QIF in phase 3)
    layout.py    detection: header row, what each column holds, dates, amounts, sign -> rows in Plaid's terms
    engine.py    per-account profiles, ids, overlap and duplicates, categories, the plan; apply as a batch; undo

Used by `fintrack import` (cli.py). Replaces csv_import.py (2026-10-29, one bank's layout), whose ids it keeps.
"""
