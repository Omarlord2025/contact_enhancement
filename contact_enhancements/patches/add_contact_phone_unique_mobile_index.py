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

import frappe

from contact_enhancements.api.contact_dedupe import (
	find_duplicate_mobile_contacts,
	find_duplicate_phone_rows_within_contact,
)

UNIQUENESS_COLUMN = "phone_uniqueness_key"
UNIQUENESS_INDEX = "phone_uniqueness_key_index"


def execute():
	duplicates = find_duplicate_mobile_contacts()
	if duplicates:
		total_contacts = sum(len(entry["contacts"]) for entry in duplicates)
		frappe.throw(
			f"contact_enhancements: cannot add the mobile-number uniqueness constraint - "
			f"{len(duplicates)} duplicate mobile number(s) still exist across {total_contacts} "
			f"Contacts. Resolve every group first (merge, reclassify as landline, or fix a "
			f"typo) - see contact_enhancements.patches.report_duplicate_mobile_contacts's own "
			f"Error Log entries, or call find_duplicate_mobile_contacts() directly, for exactly "
			f"which Contacts conflict. Re-run `bench migrate` once every group is resolved."
		)

	within_contact_duplicates = find_duplicate_phone_rows_within_contact()
	if within_contact_duplicates:
		frappe.throw(
			f"contact_enhancements: cannot add the mobile-number uniqueness constraint - "
			f"{len(within_contact_duplicates)} Contact(s) have the same mobile number entered "
			f"twice on their own phone_nos table (a different problem from cross-Contact "
			f"duplicates - see find_duplicate_phone_rows_within_contact's own docstring). Call "
			f"contact_dedupe.dedupe_contact_phone_rows(contact, phone) for each one listed, or "
			f"resolve directly on the Contact form. Re-run `bench migrate` once every one is "
			f"resolved: {within_contact_duplicates}"
		)

	if UNIQUENESS_COLUMN not in frappe.db.get_table_columns("Contact Phone"):
		# sql_ddl, not plain sql() - a DDL statement like ALTER TABLE can cause
		# an implicit commit, which frappe.db.sql() refuses to run inside an
		# open transaction (ImplicitCommitError); sql_ddl commits first itself.
		frappe.db.sql_ddl(
			f"ALTER TABLE `tabContact Phone` ADD COLUMN `{UNIQUENESS_COLUMN}` VARCHAR(140) "
			f"GENERATED ALWAYS AS (CASE WHEN `custom_landline` = 0 THEN `phone` ELSE NULL END) STORED"
		)
		print(f"contact_enhancements: added generated column {UNIQUENESS_COLUMN} on Contact Phone.")

	existing_index = frappe.db.sql(
		"SHOW INDEX FROM `tabContact Phone` WHERE Key_name = %s", UNIQUENESS_INDEX
	)
	if not existing_index:
		frappe.db.sql_ddl(
			f"ALTER TABLE `tabContact Phone` ADD UNIQUE INDEX `{UNIQUENESS_INDEX}` (`{UNIQUENESS_COLUMN}`)"
		)
		print(f"contact_enhancements: added unique index {UNIQUENESS_INDEX} on Contact Phone.")
