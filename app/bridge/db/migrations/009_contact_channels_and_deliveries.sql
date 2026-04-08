-- 009_contact_channels_and_deliveries.sql
-- Normalizes contact channels and message delivery events into dedicated tables.

CREATE TABLE contact_channels (
    id                  BIGSERIAL PRIMARY KEY,
    contact_id          BIGINT NOT NULL REFERENCES contacts (id) ON DELETE CASCADE,
    channel_type        TEXT NOT NULL,
    channel_value       TEXT NOT NULL,
    normalized_value    TEXT NOT NULL,
    is_primary          BOOLEAN NOT NULL DEFAULT FALSE,
    verified_at         TIMESTAMPTZ,
    metadata            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (contact_id, channel_type, normalized_value)
);

CREATE INDEX idx_contact_channels_lookup
    ON contact_channels (channel_type, normalized_value);

CREATE INDEX idx_contact_channels_contact
    ON contact_channels (contact_id);

INSERT INTO contact_channels (
    contact_id,
    channel_type,
    channel_value,
    normalized_value,
    is_primary,
    verified_at,
    metadata
)
SELECT
    c.id,
    'telegram_user_id',
    c.telegram_user_id::text,
    c.telegram_user_id::text,
    TRUE,
    c.updated_at,
    jsonb_build_object('source', 'contacts_backfill')
FROM contacts c
WHERE c.telegram_user_id IS NOT NULL
ON CONFLICT (contact_id, channel_type, normalized_value) DO NOTHING;

INSERT INTO contact_channels (
    contact_id,
    channel_type,
    channel_value,
    normalized_value,
    is_primary,
    metadata
)
SELECT
    c.id,
    'telegram_username',
    c.telegram_username,
    lower(ltrim(c.telegram_username, '@')),
    TRUE,
    jsonb_build_object('source', 'contacts_backfill')
FROM contacts c
WHERE c.telegram_username IS NOT NULL
  AND btrim(c.telegram_username) <> ''
ON CONFLICT (contact_id, channel_type, normalized_value) DO NOTHING;

INSERT INTO contact_channels (
    contact_id,
    channel_type,
    channel_value,
    normalized_value,
    is_primary,
    metadata
)
SELECT
    c.id,
    'email',
    c.email,
    lower(c.email),
    TRUE,
    jsonb_build_object('source', 'contacts_backfill')
FROM contacts c
WHERE c.email IS NOT NULL
  AND btrim(c.email) <> ''
ON CONFLICT (contact_id, channel_type, normalized_value) DO NOTHING;

INSERT INTO contact_channels (
    contact_id,
    channel_type,
    channel_value,
    normalized_value,
    is_primary,
    metadata
)
SELECT
    c.id,
    'phone',
    c.phone,
    regexp_replace(c.phone, '[^0-9+]+', '', 'g'),
    TRUE,
    jsonb_build_object('source', 'contacts_backfill')
FROM contacts c
WHERE c.phone IS NOT NULL
  AND btrim(c.phone) <> ''
ON CONFLICT (contact_id, channel_type, normalized_value) DO NOTHING;

CREATE TABLE deliveries (
    id                  BIGSERIAL PRIMARY KEY,
    message_id          BIGINT NOT NULL REFERENCES messages (id) ON DELETE CASCADE,
    contact_id          BIGINT REFERENCES contacts (id) ON DELETE SET NULL,
    booking_id          TEXT REFERENCES bookings (booking_id) ON DELETE SET NULL,
    direction           TEXT NOT NULL,
    transport           TEXT NOT NULL DEFAULT 'internal',
    source              TEXT,
    status              TEXT NOT NULL DEFAULT 'recorded',
    chat_id             BIGINT,
    transport_message_id BIGINT,
    attempts            INTEGER NOT NULL DEFAULT 1,
    recipient           TEXT,
    error_text          TEXT,
    payload             JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (message_id, transport)
);

CREATE INDEX idx_deliveries_message
    ON deliveries (message_id);

CREATE INDEX idx_deliveries_contact
    ON deliveries (contact_id)
    WHERE contact_id IS NOT NULL;

CREATE INDEX idx_deliveries_booking
    ON deliveries (booking_id)
    WHERE booking_id IS NOT NULL;

CREATE INDEX idx_deliveries_transport_message
    ON deliveries (transport_message_id)
    WHERE transport_message_id IS NOT NULL;

INSERT INTO deliveries (
    message_id,
    contact_id,
    booking_id,
    direction,
    transport,
    source,
    status,
    chat_id,
    transport_message_id,
    attempts,
    recipient,
    created_at,
    updated_at
)
SELECT
    m.id,
    m.contact_id,
    m.booking_id,
    COALESCE(m.direction, 'internal'),
    CASE
        WHEN m.transport_chat_id IS NOT NULL OR m.transport_message_id IS NOT NULL THEN 'telegram'
        ELSE 'internal'
    END,
    m.source,
    COALESCE(m.delivery_status, 'recorded'),
    m.transport_chat_id,
    m.transport_message_id,
    1,
    CASE
        WHEN m.transport_chat_id IS NOT NULL THEN m.transport_chat_id::text
        ELSE NULL
    END,
    m.created_at,
    m.created_at
FROM messages m
WHERE m.direction IS NOT NULL
   OR m.source IS NOT NULL
   OR m.delivery_status IS NOT NULL
   OR m.transport_chat_id IS NOT NULL
   OR m.transport_message_id IS NOT NULL
ON CONFLICT (message_id, transport) DO NOTHING;
