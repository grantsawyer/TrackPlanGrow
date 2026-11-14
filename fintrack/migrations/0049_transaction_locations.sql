-- Where a transaction happened (user's request 2026-10-28): Plaid's `location` object, as sent by /transactions/sync
-- (checked in Plaid's docs 2026-10-28: address, city, region, postal_code, country = ISO 3166-1 alpha-2, lat, lon,
-- store_number, all nullable). Plaid fills it only for purchases at physical places, most often large retail chains;
-- online purchases and transfers have none. Stored as sent (sync.location_fields); a value once known is kept when a
-- later update leaves it out. History synced before this: `fintrack locations --backfill`.
ALTER TABLE transactions ADD COLUMN location_address TEXT;
ALTER TABLE transactions ADD COLUMN location_city TEXT;
ALTER TABLE transactions ADD COLUMN location_region TEXT;
ALTER TABLE transactions ADD COLUMN location_postal_code TEXT;
ALTER TABLE transactions ADD COLUMN location_country TEXT;
ALTER TABLE transactions ADD COLUMN location_lat REAL;
ALTER TABLE transactions ADD COLUMN location_lon REAL;
ALTER TABLE transactions ADD COLUMN location_store_number TEXT;
