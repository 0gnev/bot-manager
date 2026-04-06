-- 003_contact_scoped_conversations.sql
-- Moves chat state to contacts and makes booking context optional for messages/escalations.

ALTER TABLE contacts
ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'auto',
ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active',
ADD COLUMN IF NOT EXISTS automation_enabled BOOLEAN NOT NULL DEFAULT TRUE,
ADD COLUMN IF NOT EXISTS assigned_human TEXT,
ADD COLUMN IF NOT EXISTS escalation_state TEXT NOT NULL DEFAULT 'none',
ADD COLUMN IF NOT EXISTS scenario_type TEXT NOT NULL DEFAULT 'general_support',
ADD COLUMN IF NOT EXISTS current_stage TEXT NOT NULL DEFAULT 'new',
ADD COLUMN IF NOT EXISTS confidence DOUBLE PRECISION,
ADD COLUMN IF NOT EXISTS escalation_reason TEXT,
ADD COLUMN IF NOT EXISTS draft JSONB;

UPDATE contacts c
SET mode = src.mode,
    status = src.status,
    automation_enabled = src.automation_enabled,
    assigned_human = src.assigned_human,
    escalation_state = src.escalation_state,
    scenario_type = src.scenario_type,
    current_stage = src.current_stage,
    confidence = src.confidence,
    escalation_reason = src.escalation_reason,
    draft = src.draft,
    updated_at = GREATEST(c.updated_at, src.updated_at)
FROM (
    SELECT DISTINCT ON (contact_id)
        contact_id,
        mode,
        status,
        automation_enabled,
        assigned_human,
        escalation_state,
        scenario_type,
        current_stage,
        confidence,
        escalation_reason,
        draft,
        updated_at
    FROM bookings
    WHERE contact_id IS NOT NULL
    ORDER BY contact_id, updated_at DESC, created_at DESC
) AS src
WHERE c.id = src.contact_id;

ALTER TABLE messages
ADD COLUMN IF NOT EXISTS contact_id BIGINT REFERENCES contacts (id);

UPDATE messages m
SET contact_id = b.contact_id
FROM bookings b
WHERE m.booking_id = b.booking_id
  AND m.contact_id IS NULL;

ALTER TABLE messages
ALTER COLUMN booking_id DROP NOT NULL;

CREATE INDEX IF NOT EXISTS idx_messages_contact
    ON messages (contact_id)
    WHERE contact_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_messages_contact_ts
    ON messages (contact_id, created_at)
    WHERE contact_id IS NOT NULL;

ALTER TABLE attachments
ADD COLUMN IF NOT EXISTS contact_id BIGINT REFERENCES contacts (id);

UPDATE attachments a
SET contact_id = b.contact_id
FROM bookings b
WHERE a.booking_id = b.booking_id
  AND a.contact_id IS NULL;

ALTER TABLE attachments
ALTER COLUMN booking_id DROP NOT NULL;

CREATE INDEX IF NOT EXISTS idx_attachments_contact
    ON attachments (contact_id)
    WHERE contact_id IS NOT NULL;

ALTER TABLE escalations
ADD COLUMN IF NOT EXISTS contact_id BIGINT REFERENCES contacts (id);

UPDATE escalations e
SET contact_id = b.contact_id
FROM bookings b
WHERE e.booking_id = b.booking_id
  AND e.contact_id IS NULL;

ALTER TABLE escalations
ALTER COLUMN booking_id DROP NOT NULL;

CREATE INDEX IF NOT EXISTS idx_escalations_contact
    ON escalations (contact_id)
    WHERE contact_id IS NOT NULL;
