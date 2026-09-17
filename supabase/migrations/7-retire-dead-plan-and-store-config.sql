-- Retire dead plan configuration and the duplicated store redirect columns.
--
-- `success_redirect_url` / `failure_redirect_url` on stores were a second copy of
-- `redirect_success_url` / `redirect_failure_url` — the pair the hosted checkout
-- actually reads (routers/checkout.py). Nothing read the copies, so they only
-- served to make the dashboard's redirect fields silently do nothing.
ALTER TABLE stores
    DROP COLUMN IF EXISTS success_redirect_url,
    DROP COLUMN IF EXISTS failure_redirect_url;

-- Plan fields no code reads: overage pricing was never applied to invoices,
-- account-scoped keys are allowed on every tier, and trials are not offered.
ALTER TABLE plans
    DROP COLUMN IF EXISTS overage_fee_cents_per_payment,
    DROP COLUMN IF EXISTS allowed_account_types,
    DROP COLUMN IF EXISTS allow_account_scope_keys,
    DROP COLUMN IF EXISTS trial_days;

-- The old Starter/Growth/Scale/Enterprise matrix left three hidden plans behind.
-- Only drop them if no subscription (past or present) points at them.
DELETE FROM plans
WHERE code IN ('growth', 'scale', 'enterprise')
  AND id NOT IN (SELECT plan_id FROM plan_subscriptions);
