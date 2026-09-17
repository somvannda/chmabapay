-- Merge SubMerchants into Stores: a store IS the merchant.
--
-- A platform account creates one store per merchant (with its own external_id and
-- ABA PayWay link) instead of a shadow store behind a SubMerchant row.

ALTER TABLE stores ADD COLUMN IF NOT EXISTS external_id VARCHAR(255);
ALTER TABLE stores ADD COLUMN IF NOT EXISTS whitelabel_css TEXT;
ALTER TABLE stores ALTER COLUMN name TYPE VARCHAR(120);

-- Fold any existing sub-merchant rows into the store each one owns.
UPDATE stores s
SET external_id = sm.external_id,
    whitelabel_css = COALESCE(s.whitelabel_css, sm.whitelabel_css)
FROM sub_merchants sm
WHERE sm.shadow_store_id = s.id
  AND s.external_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_store_account_external
    ON stores (account_id, external_id);

DROP TABLE IF EXISTS sub_merchants;

-- ABA PayWay links are the only supported destination.
ALTER TABLE stores DROP COLUMN IF EXISTS destination_type;
ALTER TABLE stores DROP COLUMN IF EXISTS destination_details;

-- Sub-merchant feature flags are gone.
ALTER TABLE accounts DROP COLUMN IF EXISTS saas_sub_merchants_enabled;
ALTER TABLE plans DROP COLUMN IF EXISTS max_sub_merchants;
ALTER TABLE plans DROP COLUMN IF EXISTS allow_saas_sub_merchants;
