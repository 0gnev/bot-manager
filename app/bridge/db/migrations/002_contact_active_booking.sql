-- 002_contact_active_booking.sql
-- Adds explicit active booking context per contact for multi-booking routing.

ALTER TABLE contacts
ADD COLUMN IF NOT EXISTS active_booking_id TEXT REFERENCES bookings (booking_id);

CREATE INDEX IF NOT EXISTS idx_contacts_active_booking
    ON contacts (active_booking_id)
    WHERE active_booking_id IS NOT NULL;
