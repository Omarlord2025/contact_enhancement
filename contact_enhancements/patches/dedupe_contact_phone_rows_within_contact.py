# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

"""One-time cleanup for find_duplicate_phone_rows_within_contact's own
failure mode: a Contact with the exact same non-landline number entered
twice in its own phone_nos table. Found on a real production-shaped site
(6 Contacts, always exactly 2 rows each, differing only in which row had
is_primary_mobile_no/custom_whatsapp set) - invisible to find_duplicate_
mobile_contacts's own "shared across distinct Contacts" definition, but
still fatal to add_contact_phone_unique_mobile_index's ALTER TABLE, which
doesn't distinguish same-parent from different-parent collisions.

Safe to automate (unlike find_duplicate_mobile_contacts's own cross-
Contact groups, which always need a human decision): there is only one
Contact involved per group here, so "consolidate onto one row, OR the
channel flags together, delete the rest" has no ambiguity about who the
"real" record is - see contact_dedupe.dedupe_contact_phone_rows's own
docstring.

Must run after renormalize_contact_phones_to_e164 (groups on the final
canonical value) and before add_contact_phone_unique_mobile_index (which
this unblocks).
"""

import frappe

from contact_enhancements.api.contact_dedupe import (
	dedupe_contact_phone_rows,
	find_duplicate_phone_rows_within_contact,
)


def execute():
	duplicates = find_duplicate_phone_rows_within_contact()
	if not duplicates:
		print(
			"contact_enhancements: dedupe_contact_phone_rows_within_contact - "
			"no within-Contact duplicate rows found."
		)
		return

	fixed = []
	failed = []
	for entry in duplicates:
		try:
			dedupe_contact_phone_rows(entry["contact"], entry["phone"])
			fixed.append(entry)
		except Exception as e:
			failed.append((entry, str(e)))

	print(
		f"contact_enhancements: dedupe_contact_phone_rows_within_contact - "
		f"consolidated {len(fixed)} Contact(s), {len(failed)} failed."
	)

	for entry, error in failed:
		frappe.log_error(
			title="contact_enhancements: could not dedupe within-Contact duplicate phone rows",
			message=f"Contact {entry['contact']!r}, phone {entry['phone']!r}: {error}. "
			"Needs a human look - see contact_dedupe.dedupe_contact_phone_rows.",
		)
