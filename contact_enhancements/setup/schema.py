# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

"""Schema management for custom database objects not defined in Frappe DocType JSON.

Specifically handles:
- Contact Phone.phone_uniqueness_key (STORED generated column)
- phone_uniqueness_key_index (UNIQUE INDEX)

MariaDB has no native partial/conditional unique index, so a plain UNIQUE
index on Contact Phone.phone would reject legitimate duplicate landlines.
The solution is a STORED generated column that evaluates to NULL for landlines
(custom_landline = 1) and phone for mobiles (custom_landline = 0).
Since NULL != NULL in MariaDB unique indexes, landlines are exempted while
mobiles are strictly enforced as unique.
"""

import frappe

from contact_enhancements.api.contact_dedupe import (
	find_duplicate_mobile_contacts,
	find_duplicate_phone_rows_within_contact,
)

UNIQUENESS_COLUMN = "phone_uniqueness_key"
UNIQUENESS_INDEX = "phone_uniqueness_key_index"


def ensure_contact_phone_uniqueness_constraint():
	"""Ensure Contact Phone.phone_uniqueness_key generated column and its
	unique index exist. Safe to run repeatedly (idempotent).

	Invoked from:
	- contact_enhancements.install.after_install (fresh install / reinstall)
	- after_migrate hook in hooks.py (bench migrate)
	- add_contact_phone_unique_mobile_index patch (historical migrations)
	"""
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
