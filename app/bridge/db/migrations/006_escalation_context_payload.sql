-- 006_escalation_context_payload.sql
-- Stores richer escalation package context for tutor review.

ALTER TABLE escalations
ADD COLUMN IF NOT EXISTS summary TEXT,
ADD COLUMN IF NOT EXISTS relevant_history JSONB,
ADD COLUMN IF NOT EXISTS draft_reply TEXT;
