# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

"""Phase 0c: report every pre-existing Contact sharing a mobile number with
another Contact, so a human can resolve each one - merge (Frappe's native
"Merge with existing"), reclassify a row custom_landline=1 if it's actually
a legitimately shared line, or fix a data-entry typo - before Phase 0d's
own uniqueness-constraint patch (add_contact_phone_unique_mobile_index)
runs. See contact_dedupe.find_duplicate_mobile_contacts's own docstring for
why this is a hard gate, not just documentation: MariaDB is physically
incapable of building a UNIQUE INDEX over a column that still holds
duplicate values.

Must run after renormalize_contact_phones_to_e164 - grouping has to happen
on the final canonical E.164 value, not the pre-conversion local form,
or two rows holding the same real number in different formats would be
missed entirely.

Read-only: this patch itself changes nothing. It only logs findings via
frappe.log_error (one entry per duplicate group) and prints a summary to
the migrate console - the actual resolution is a human decision, not
something this patch can safely automate.
"""

import frappe

from contact_enhancements.api.contact_dedupe import find_duplicate_mobile_contacts


def execute():
	duplicates = find_duplicate_mobile_contacts()

	if not duplicates:
		print(
			"contact_enhancements: report_duplicate_mobile_contacts - "
			"no duplicate mobile numbers found."
		)
		return

	for entry in duplicates:
		frappe.log_error(
			title="contact_enhancements: duplicate mobile number across Contacts",
			message=(
				f"Phone {entry['phone']!r} appears on {len(entry['contacts'])} distinct "
				f"Contacts: {', '.join(entry['contacts'])}.\n\n"
				"Needs a human decision before contact_enhancements.patches."
				"add_contact_phone_unique_mobile_index can run: merge the Contacts "
				"(if this is genuinely one person entered twice), reclassify one row "
				"custom_landline=1 (if this is a legitimately shared line), or fix "
				"whichever row has the actual typo."
			),
		)

	total_contacts = sum(len(entry["contacts"]) for entry in duplicates)
	print(
		f"contact_enhancements: report_duplicate_mobile_contacts - found {len(duplicates)} "
		f"duplicate mobile number(s) across {total_contacts} Contacts. See Error Log for "
		"details - each must be resolved before add_contact_phone_unique_mobile_index can run."
	)
