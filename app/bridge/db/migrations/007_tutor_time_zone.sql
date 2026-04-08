-- 007_tutor_time_zone.sql
-- Stores the tutor's preferred display time zone for tutor-facing views.

ALTER TABLE runtime_controls
ADD COLUMN IF NOT EXISTS tutor_time_zone TEXT;
