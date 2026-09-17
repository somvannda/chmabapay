-- KYC is not part of the product. Drop every identity-document column the
-- Milestone 1 schema carried on accounts, plus the dead plan-level gate.
ALTER TABLE accounts
    DROP COLUMN IF EXISTS khmer_id_number,
    DROP COLUMN IF EXISTS khmer_id_front_url,
    DROP COLUMN IF EXISTS khmer_id_back_url,
    DROP COLUMN IF EXISTS company_name_registered,
    DROP COLUMN IF EXISTS company_registration_number,
    DROP COLUMN IF EXISTS mo_certificate_url,
    DROP COLUMN IF EXISTS vat_tin_number,
    DROP COLUMN IF EXISTS director_name,
    DROP COLUMN IF EXISTS director_id_number,
    DROP COLUMN IF EXISTS kyc_status,
    DROP COLUMN IF EXISTS kyc_approved_at,
    DROP COLUMN IF EXISTS kyc_reject_reason,
    DROP COLUMN IF EXISTS kyc_live_blocked;

ALTER TABLE plans
    DROP COLUMN IF EXISTS kyc_required_for_live;
