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


def ensure_contact_phone_uniqueness_constraint(throw_if_duplicates=True):
	"""Ensure Contact Phone.phone_uniqueness_key generated column and its
	unique index exist. Safe to run repeatedly (idempotent).

	Invoked from:
	- contact_enhancements.install.after_install (fresh install / reinstall)
	- after_migrate hook in hooks.py (bench migrate)
	- add_contact_phone_unique_mobile_index patch (historical migrations)
	"""
	duplicates = find_duplicate_mobile_contacts()
	within_contact_duplicates = find_duplicate_phone_rows_within_contact()

	if duplicates or within_contact_duplicates:
		total_contacts = sum(len(entry["contacts"]) for entry in duplicates)
		msg = (
			f"contact_enhancements: cannot add the mobile-number uniqueness constraint - "
			f"{len(duplicates)} duplicate mobile number(s) still exist across {total_contacts} Contacts, "
			f"and {len(within_contact_duplicates)} Contact(s) have duplicated phone rows. "
			f"Resolve every group first (merge, reclassify as landline, or fix a typo) via the "
			f"Duplicate Mobile Contacts page or Error Log entries. Re-run `bench migrate` once resolved."
		)
		if throw_if_duplicates and not getattr(frappe.flags, "in_install", False):
			frappe.throw(msg)
		else:
			print(f"\n[WARNING] {msg}\n")
			frappe.log_error(title="contact_enhancements: unique mobile index deferred due to duplicates", message=msg)
			return

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
