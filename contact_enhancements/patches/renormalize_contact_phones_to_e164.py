# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

"""Re-run every existing Contact Phone row through
contact_hooks.normalize_and_validate_contact_phone, now that it returns
international E.164 form ("+201012345678") instead of the local dialing
form ("01012345678") this app stored until now.

Only the stored value's format changes here - the number itself doesn't.
This is a one-time backfill, not a change to the validation rules
themselves; every row that already parses validly under its own country
continues to parse validly, just formatted differently on the way back out.

A row whose phone value doesn't parse (genuine legacy junk that predates
this app's own validation, or a value that was already flagged/left in
place by contact_hooks.py's background-job leniency - see that module's
own docstring) is reported via frappe.log_error and left untouched, not
silently dropped - unlike normalize_and_validate_contact_phones's own
background-job leniency, there is no live save() here to protect and no
reason to lose data a human may still need to look at and fix by hand.
See docs/e164_impact_audit.md for the bench-wide review of what this
format change could affect before it shipped.
"""

import frappe

from contact_enhancements.contact_hooks import normalize_and_validate_contact_phone

BATCH_SIZE = 500


def execute():
	rows = frappe.get_all(
		"Contact Phone",
		filters={"phone": ["not in", ("", None)]},
		fields=["name", "phone", "country", "custom_landline"],
	)

	converted = 0
	unchanged = 0
	failed = []

	for index, row in enumerate(rows, start=1):
		try:
			new_value = normalize_and_validate_contact_phone(
				row.phone, row.country, is_landline=row.custom_landline
			)
		except frappe.ValidationError as e:
			failed.append((row.name, row.phone, row.country, str(e)))
			continue

		if new_value != row.phone:
			# update_modified=False so this backfill doesn't make every
			# Contact in the system look freshly edited.
			frappe.db.set_value("Contact Phone", row.name, "phone", new_value, update_modified=False)
			converted += 1
		else:
			unchanged += 1

		if index % BATCH_SIZE == 0:
			frappe.db.commit()

	if rows:
		frappe.db.commit()

	print(
		f"contact_enhancements: renormalize_contact_phones_to_e164 - "
		f"{converted} row(s) converted to E.164, {unchanged} already correct, "
		f"{len(failed)} row(s) could not be parsed and were left untouched."
	)

	for name, phone, country, error in failed:
		frappe.log_error(
			title="contact_enhancements: legacy Contact Phone row could not be renormalized to E.164",
			message=(
				f"Contact Phone {name}: phone={phone!r} country={country!r} - {error}\n\n"
				"Left untouched by renormalize_contact_phones_to_e164 - this row predates "
				"this app's own phone validation, or was intentionally left in place by "
				"contact_hooks.py's background-job leniency. Needs a human to look at it "
				"directly (correct the number, or confirm the Country) before it will "
				"validate on its own next save."
			),
		)
