# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

"""Move the per-Contact Country field down to a per-row field on Contact
Phone.

country was originally added directly on Contact (add_contact_country_field
.py / backfill_contact_country.py) - wrong: a Contact can carry more than
one number from more than one country, and "pick a country, its rules
apply" only makes sense applied to the number sitting in that row, not the
whole Contact (see contact_hooks.py's own module docstring above
normalize_and_validate_contact_phone for the fuller reasoning, including
the ERPNext-native-Lead-creation gap this design still has to keep closed).

This patch:

1. Creates the new Contact Phone.country field (create_contact_enhancements
   _custom_fields, already updated to define it there instead of on Contact).
2. Backfills every existing Contact Phone row with "Egypt" - every existing
   Contact.country value on this site was already "Egypt" (confirmed via a
   direct query before writing this patch), so there is nothing more
   specific to carry over row by row; this is the same default this app
   already uses everywhere else a country choice is needed.
3. Deletes the old Contact.country Custom Field record and drops its now-
   orphaned database column - frappe.custom.doctype.custom_field.
   CustomField.on_trash() removes the field from Contact's meta/forms but
   deliberately does not touch the schema itself (confirmed by reading it),
   so the column would otherwise sit there, unused and invisible to every
   future SHOW COLUMNS/meta check, unless dropped explicitly.
"""

import frappe

from contact_enhancements.setup.custom_fields import create_contact_enhancements_custom_fields

BATCH_SIZE = 500


def execute():
	create_contact_enhancements_custom_fields()

	rows = frappe.get_all("Contact Phone", filters={"country": ["in", ("", None)]}, pluck="name")
	for index, name in enumerate(rows, start=1):
		# update_modified=False so this backfill doesn't make every Contact
		# in the system look freshly edited.
		frappe.db.set_value("Contact Phone", name, "country", "Egypt", update_modified=False)
		if index % BATCH_SIZE == 0:
			frappe.db.commit()
	if rows:
		frappe.db.commit()
		print(f"contact_enhancements: backfilled country='Egypt' on {len(rows)} Contact Phone row(s).")

	if frappe.db.exists("Custom Field", "Contact-country"):
		frappe.delete_doc("Custom Field", "Contact-country", ignore_permissions=True)

	if "country" in frappe.db.get_table_columns("Contact"):
		# sql_ddl, not plain sql() - a DDL statement like ALTER TABLE can
		# cause an implicit commit, which frappe.db.sql() refuses to run
		# inside an open transaction (ImplicitCommitError); sql_ddl commits
		# first itself.
		frappe.db.sql_ddl("ALTER TABLE `tabContact` DROP COLUMN `country`")
		print("contact_enhancements: dropped the now-unused Contact.country column.")
