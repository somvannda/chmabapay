ALTER TABLE payments
ADD COLUMN IF NOT EXISTS reissued_from_id INTEGER REFERENCES payments(id);

CREATE INDEX IF NOT EXISTS ix_payments_reissued_from_id
ON payments (reissued_from_id);
