# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

"""Add Contact Phone.custom_phone_national (setup/custom_fields.py) and
backfill it on every existing row, from that row's own already-E.164
phone value (contact_hooks._national_digits).

Needed because renormalize_contact_phones_to_e164 already converted every
existing row to E.164 before this field existed - this app's own validate
hook only populates custom_phone_national on a row's *next* save, so a
row nobody touches again would otherwise stay unsearchable by local-format
number indefinitely without this one-time backfill.

Must run after renormalize_contact_phones_to_e164, for the same reason
that patch itself has to run before anything groups/derives from the
final phone value - _national_digits assumes its input is already E.164.
"""

import frappe

from contact_enhancements.contact_hooks import _national_digits
from contact_enhancements.setup.custom_fields import create_contact_enhancements_custom_fields

BATCH_SIZE = 500


def execute():
	create_contact_enhancements_custom_fields()

	rows = frappe.get_all(
		"Contact Phone",
		filters={"phone": ["not in", ("", None)]},
		fields=["name", "phone"],
	)

	updated = 0
	skipped = []
	for index, row in enumerate(rows, start=1):
		try:
			national = _national_digits(row.phone)
		except Exception as e:
			skipped.append((row.name, row.phone, str(e)))
			continue

		# update_modified=False so this backfill doesn't make every
		# Contact in the system look freshly edited.
		frappe.db.set_value(
			"Contact Phone", row.name, "custom_phone_national", national, update_modified=False
		)
		updated += 1
		if index % BATCH_SIZE == 0:
			frappe.db.commit()

	if rows:
		frappe.db.commit()

	print(
		f"contact_enhancements: backfill_contact_phone_national - "
		f"{updated} row(s) backfilled, {len(skipped)} row(s) skipped (not valid E.164 - "
		f"likely one of the legacy rows renormalize_contact_phones_to_e164 already "
		f"flagged and left untouched)."
	)

	for name, phone, error in skipped:
		frappe.log_error(
			title="contact_enhancements: could not backfill custom_phone_national",
			message=f"Contact Phone {name}: phone={phone!r} - {error}. Left blank - this "
			"row is not valid E.164, so it's also one of the rows "
			"renormalize_contact_phones_to_e164 already logged as needing a human look.",
		)
