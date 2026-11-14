-- Manual accounts take files (docs/import-plan.md phase 4, user's request 2026-11-13): an import's running Balance column
-- gives a manual account a value for each day it covers (that day's closing balance, imports/engine.py). The import that
-- wrote a value, so undoing it removes the value too (NULL: a value you entered, or from positions / money lent).
ALTER TABLE manual_valuations ADD COLUMN import_batch_id INTEGER REFERENCES import_batches(id) ON DELETE SET NULL;
