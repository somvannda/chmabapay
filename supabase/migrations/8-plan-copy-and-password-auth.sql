-- Two additions:
--   1. Optional password sign-in on accounts (the platform admin console signs in
--      with email + password; Google OAuth keeps working).
--   2. Pricing-page presentation on plans, so the website and the user portal render
--      plan names, prices, taglines and feature bullets from the database instead of
--      hardcoded frontend constants.
--
-- Every statement is idempotent, so this file can be re-run safely.

ALTER TABLE accounts
    ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255);

ALTER TABLE plans
    ADD COLUMN IF NOT EXISTS tagline VARCHAR(160),
    ADD COLUMN IF NOT EXISTS features JSON,
    ADD COLUMN IF NOT EXISTS is_featured BOOLEAN NOT NULL DEFAULT FALSE;

-- One-time backfill: keep the "Most popular" badge on the tier that carried it in
-- the hardcoded pricing page, but only while no plan claims it yet.
UPDATE plans
SET is_featured = TRUE
WHERE code = 'starter'
  AND NOT EXISTS (SELECT 1 FROM plans WHERE is_featured);
