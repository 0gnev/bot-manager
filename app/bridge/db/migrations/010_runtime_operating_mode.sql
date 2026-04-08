-- 010_runtime_operating_mode.sql
-- Adds explicit emergency operating modes for tutor-driven runtime control.

ALTER TABLE runtime_controls
ADD COLUMN IF NOT EXISTS operating_mode TEXT NOT NULL DEFAULT 'normal',
ADD COLUMN IF NOT EXISTS incident_reason TEXT,
ADD COLUMN IF NOT EXISTS incident_started_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS incident_started_by TEXT;

UPDATE runtime_controls
SET operating_mode = CASE
        WHEN global_automation_enabled THEN 'normal'
        ELSE 'degraded'
    END
WHERE operating_mode IS NULL
   OR operating_mode NOT IN ('normal', 'degraded', 'frozen');
