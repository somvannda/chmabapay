ALTER TABLE accounts
ADD COLUMN account_type_explicitly_set BOOLEAN NOT NULL DEFAULT FALSE;
