-- 001_initial_schema.sql
-- PostgreSQL migration: replaces JSON-file state storage.

-- ── Contacts ───────���────────────────────────────────────────────────────────

CREATE TABLE contacts (
    id                  BIGSERIAL PRIMARY KEY,
    telegram_user_id    BIGINT UNIQUE,
    telegram_username   TEXT,
    name                TEXT,
    email               TEXT,
    phone               TEXT,
    time_zone           TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_contacts_tg_user ON contacts (telegram_user_id)
    WHERE telegram_user_id IS NOT NULL;
CREATE INDEX idx_contacts_tg_username ON contacts (lower(telegram_username))
    WHERE telegram_username IS NOT NULL;

-- ── Bookings ───────────────────────────────────────��────────────────────────

CREATE TABLE bookings (
    booking_id          TEXT PRIMARY KEY,
    contact_id          BIGINT REFERENCES contacts (id),
    event               TEXT,
    title               TEXT,
    description         TEXT,
    start_time          TIMESTAMPTZ,
    end_time            TIMESTAMPTZ,
    organizer           JSONB,
    attendee            JSONB,
    location            JSONB,
    meeting_url         TEXT,
    custom_inputs       JSONB DEFAULT '[]'::jsonb,
    status              TEXT NOT NULL DEFAULT 'active',
    -- chat metadata (formerly in conversation JSON)
    mode                TEXT NOT NULL DEFAULT 'auto',
    automation_enabled  BOOLEAN NOT NULL DEFAULT TRUE,
    assigned_human      TEXT,
    escalation_state    TEXT NOT NULL DEFAULT 'none',
    scenario_type       TEXT NOT NULL DEFAULT 'general_support',
    current_stage       TEXT NOT NULL DEFAULT 'new',
    confidence          DOUBLE PRECISION,
    escalation_reason   TEXT,
    draft               JSONB,
    telegram_user_id    BIGINT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_bookings_contact ON bookings (contact_id);
CREATE INDEX idx_bookings_status ON bookings (status);
CREATE INDEX idx_bookings_tg_user ON bookings (telegram_user_id)
    WHERE telegram_user_id IS NOT NULL;

-- ── Messages ────────────────────────────────────────────────────────────────

CREATE TABLE messages (
    id                  BIGSERIAL PRIMARY KEY,
    booking_id          TEXT NOT NULL REFERENCES bookings (booking_id),
    role                TEXT NOT NULL,
    content             TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_messages_booking ON messages (booking_id);
CREATE INDEX idx_messages_booking_ts ON messages (booking_id, created_at);

-- ── Attachments ───────��──────────────────────────────────���──────────────────

CREATE TABLE attachments (
    id                  BIGSERIAL PRIMARY KEY,
    message_id          BIGINT REFERENCES messages (id),
    booking_id          TEXT NOT NULL REFERENCES bookings (booking_id),
    file_id             TEXT NOT NULL,
    file_type           TEXT NOT NULL DEFAULT 'photo',
    mime_type           TEXT,
    local_path          TEXT,
    caption             TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_attachments_booking ON attachments (booking_id);
CREATE INDEX idx_attachments_message ON attachments (message_id);

-- ── Escalations ────────────────────────────��───────────────────────────────��

CREATE TABLE escalations (
    id                  BIGSERIAL PRIMARY KEY,
    booking_id          TEXT NOT NULL REFERENCES bookings (booking_id),
    status              TEXT NOT NULL DEFAULT 'pending',
    reason              TEXT,
    question            TEXT NOT NULL,
    tutor_message_id    BIGINT,
    tutor_reply         TEXT,
    resolved_by         TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at         TIMESTAMPTZ
);

CREATE INDEX idx_escalations_booking ON escalations (booking_id);
CREATE INDEX idx_escalations_tutor_msg ON escalations (tutor_message_id)
    WHERE status = 'pending';
CREATE INDEX idx_escalations_status ON escalations (status);

-- ── Approvals ───────���────────────────────────────────────���──────────────────

CREATE TABLE approvals (
    approval_id         TEXT PRIMARY KEY,
    booking_id          TEXT NOT NULL REFERENCES bookings (booking_id),
    student_chat_id     BIGINT NOT NULL,
    draft_content       TEXT NOT NULL,
    action              TEXT NOT NULL,
    confidence          DOUBLE PRECISION,
    status              TEXT NOT NULL DEFAULT 'pending',
    reviewer            TEXT,
    review_channel      TEXT DEFAULT 'api',
    tutor_message_id    BIGINT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at         TIMESTAMPTZ
);

CREATE INDEX idx_approvals_booking ON approvals (booking_id);
CREATE INDEX idx_approvals_tutor_msg ON approvals (tutor_message_id)
    WHERE status = 'pending';
CREATE INDEX idx_approvals_status ON approvals (status);

-- ── Runtime controls ──────��─────────────────────────────────────────────────

CREATE TABLE runtime_controls (
    id                          INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    global_automation_enabled   BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by                  TEXT NOT NULL DEFAULT 'system',
    reason                      TEXT
);

INSERT INTO runtime_controls (id) VALUES (1);

-- ── Idempotency keys ────────���───────────────────────────────────────────────

CREATE TABLE idempotency_keys (
    key                 TEXT PRIMARY KEY,
    expires_at          TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_idempotency_expires ON idempotency_keys (expires_at);

-- ── Knowledge updates (audit) ───────────────────────────────────────────────

CREATE TABLE knowledge_updates (
    id                  BIGSERIAL PRIMARY KEY,
    file_path           TEXT NOT NULL,
    action              TEXT NOT NULL,
    content_before      TEXT,
    content_after       TEXT,
    requested_by        TEXT NOT NULL,
    approved            BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
