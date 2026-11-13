-- Calendar events become idempotent to import.
--
-- Without a uniqueness rule, re-running a holiday sync for an overlapping year
-- duplicates every event, and duplicated events double-count the impact flag on
-- that day. The key includes the date, type and name so re-importing the same
-- source is a no-op while a genuinely different event on the same day (a sale
-- during Eid, say) still lands.
--
-- organization_id is nullable for global events, and NULL does not compare
-- equal in a unique index, so the constraint is built on an expression that
-- maps the global scope to 0.

CREATE UNIQUE INDEX IF NOT EXISTS ml_calendar_events_unique
    ON ml_calendar_events (
        COALESCE(organization_id, 0),
        event_date,
        event_type,
        event_name
    );

-- The sync filters by scope and date range.
CREATE INDEX IF NOT EXISTS idx_ml_calendar_events_lookup
    ON ml_calendar_events (event_date)
    WHERE organization_id IS NOT NULL;
