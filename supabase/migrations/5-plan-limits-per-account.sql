-- Plan limits are account-wide: a workspace holds one account, not one account
-- per store, so `max_*_per_store` was a misnomer. `allow_whitelabel` is dropped
-- entirely — whitelabel (logo / custom checkout CSS) is available on every plan.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'plans' AND column_name = 'max_keys_per_store'
    ) THEN
        ALTER TABLE plans RENAME COLUMN max_keys_per_store TO max_keys_per_account;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'plans' AND column_name = 'max_webhooks_per_store'
    ) THEN
        ALTER TABLE plans RENAME COLUMN max_webhooks_per_store TO max_webhooks_per_account;
    END IF;
END $$;

ALTER TABLE plans DROP COLUMN IF EXISTS allow_whitelabel;
