-- 011_knowledge_suggestions.sql
-- Adds tutor-approved self-learning suggestions and source question context.

ALTER TABLE approvals
ADD COLUMN IF NOT EXISTS student_question TEXT;

CREATE TABLE IF NOT EXISTS knowledge_suggestions (
    id                  BIGSERIAL PRIMARY KEY,
    source_kind         TEXT NOT NULL,
    booking_id          TEXT NULL REFERENCES bookings(booking_id) ON DELETE SET NULL,
    contact_id          BIGINT NULL REFERENCES contacts(id) ON DELETE SET NULL,
    approval_id         TEXT NULL REFERENCES approvals(approval_id) ON DELETE SET NULL,
    escalation_id       BIGINT NULL REFERENCES escalations(id) ON DELETE SET NULL,
    source_question     TEXT NULL,
    answer_text         TEXT NOT NULL,
    title               TEXT NOT NULL,
    rationale           TEXT NULL,
    content_markdown    TEXT NOT NULL,
    suggested_file_path TEXT NULL,
    knowledge_file_path TEXT NULL,
    tutor_message_id    BIGINT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',
    resolved_by         TEXT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at         TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_knowledge_suggestions_status
    ON knowledge_suggestions (status);

CREATE INDEX IF NOT EXISTS idx_knowledge_suggestions_booking
    ON knowledge_suggestions (booking_id);

CREATE INDEX IF NOT EXISTS idx_knowledge_suggestions_contact
    ON knowledge_suggestions (contact_id);

CREATE INDEX IF NOT EXISTS idx_knowledge_suggestions_tutor_msg
    ON knowledge_suggestions (tutor_message_id);
