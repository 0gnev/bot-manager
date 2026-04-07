-- 005_message_delivery_metadata.sql
-- Expands stored message records with transport, delivery, and model metadata.

ALTER TABLE messages
ADD COLUMN IF NOT EXISTS direction TEXT,
ADD COLUMN IF NOT EXISTS source TEXT,
ADD COLUMN IF NOT EXISTS delivery_status TEXT,
ADD COLUMN IF NOT EXISTS transport_chat_id BIGINT,
ADD COLUMN IF NOT EXISTS transport_message_id BIGINT,
ADD COLUMN IF NOT EXISTS model_output JSONB;

UPDATE messages
SET direction = COALESCE(
        direction,
        CASE
            WHEN role = 'user' THEN 'inbound'
            WHEN role = 'assistant' THEN 'outbound'
            ELSE 'internal'
        END
    ),
    delivery_status = COALESCE(
        delivery_status,
        CASE
            WHEN role = 'user' THEN 'received'
            WHEN role = 'assistant' THEN 'sent'
            ELSE 'recorded'
        END
    )
WHERE direction IS NULL
   OR delivery_status IS NULL;

ALTER TABLE messages
ALTER COLUMN direction SET NOT NULL,
ALTER COLUMN direction SET DEFAULT 'internal',
ALTER COLUMN delivery_status SET NOT NULL,
ALTER COLUMN delivery_status SET DEFAULT 'recorded';

CREATE INDEX IF NOT EXISTS idx_messages_transport_message
    ON messages (transport_message_id)
    WHERE transport_message_id IS NOT NULL;
