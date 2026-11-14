-- A tag's own dates (user's request 2026-10-28): when the trip / project ran, set on the Tags page. With both, the Tags
-- calendar draws one bar from start to end and shows each tagged transaction outside them as a single day; without,
-- it joins consecutive days (tags.calendar_items). ISO dates; either may be blank (a range needs both); start <= end.
ALTER TABLE tags ADD COLUMN start_date TEXT;
ALTER TABLE tags ADD COLUMN end_date TEXT;
