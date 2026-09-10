# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

"""Phase 0d: the database-level guarantee behind
contact_hooks.enforce_unique_mobile_number. That hook is the friendly,
immediate UX layer, but a validate hook alone is not concurrency-safe - two
simultaneous inserts can both pass its "is this number free" check before
either commits (a genuine TOCTOU race, not a theoretical nitpick). This is
the actual, unconditional constraint.

MariaDB has no native partial/conditional unique index (unlike Postgres),
so a plain UNIQUE INDEX on Contact Phone.phone would also reject two
different Contacts legitimately sharing one landline. The workaround: a
STORED generated column, phone_uniqueness_key, that evaluates to `phone`
for non-landline rows and NULL for landline rows - MariaDB unique indexes
don't treat multiple NULLs as colliding, so landline rows are naturally,
unconditionally exempted with no per-row logic needed anywhere.

Hard-gated on Phase 0c: MariaDB is physically incapable of creating a
UNIQUE INDEX over a column that already contains duplicate values, so this
queries both find_duplicate_mobile_contacts() (a number shared across
distinct Contacts) and find_duplicate_phone_rows_within_contact() (the
same number entered twice on one Contact - a different failure mode,
confirmed to happen on real legacy data, and just as fatal to this ALTER
TABLE) immediately before attempting it, aborting loudly and pointing back
at whichever is non-empty, rather than letting a raw duplicate-key DB
error surface - though the DB would refuse regardless, independent of
this check.

Idempotent: safe to re-run once the constraint is already in place (checks
information_schema / SHOW INDEX before adding either the column or the
index), so a second `bench migrate` after resolving a fresh conflict
doesn't error on "column/index already exists".
"""

from contact_enhancements.setup.schema import ensure_contact_phone_uniqueness_constraint


def execute():
	ensure_contact_phone_uniqueness_constraint()
