ALTER TABLE stores
ADD COLUMN telegram_chat_id VARCHAR(64),
ADD COLUMN brand_color VARCHAR(16),
ADD COLUMN success_redirect_url TEXT,
ADD COLUMN failure_redirect_url TEXT,
ADD COLUMN logo_image_url TEXT;
