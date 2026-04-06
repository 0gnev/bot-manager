-- 004_contact_scoped_approvals.sql
-- Makes approvals compatible with contact-first conversations.

ALTER TABLE approvals
ADD COLUMN IF NOT EXISTS contact_id BIGINT REFERENCES contacts (id);

UPDATE approvals a
SET contact_id = b.contact_id
FROM bookings b
WHERE a.booking_id = b.booking_id
  AND a.contact_id IS NULL;

ALTER TABLE approvals
ALTER COLUMN booking_id DROP NOT NULL;

CREATE INDEX IF NOT EXISTS idx_approvals_contact
    ON approvals (contact_id)
    WHERE contact_id IS NOT NULL;
