# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

"""Re-resolve every Contact Phone row's country from the number itself,
replacing move_country_to_contact_phone's blanket "Egypt" guess with real
evidence via contact_hooks.resolve_phone_country - see that function's own
module docstring for the two rules it applies and why nothing else is
consulted.

Runs against every row, not just ones with no other signal: an existing
"Egypt" value carries no evidential weight of its own - it may already be
correct, or it may just be that earlier patch's placeholder default,  and
there is no way to tell the two apart from the stored value alone. A row a
rule resolves gets its country corrected (if needed) and re-normalized to
E.164 against that country; a row neither rule can justify is left exactly
as it was and flagged Contact Phone.custom_country_resolution="Unresolved"
for a human to resolve via the Phone Country Resolution page
(api.phone_country_resolution) instead of staying silently mislabeled.
"""

import frappe

from contact_enhancements.contact_hooks import normalize_and_validate_contact_phone, resolve_phone_country
from contact_enhancements.setup.custom_fields import create_contact_enhancements_custom_fields

BATCH_SIZE = 500


def execute():
	create_contact_enhancements_custom_fields()

	rows = frappe.get_all(
		"Contact Phone",
		fields=["name", "phone", "country", "custom_landline"],
	)

	detected = 0
	pattern = 0
	unresolved = 0
	failed = []

	for index, row in enumerate(rows, start=1):
		result = resolve_phone_country(row.phone)

		if result:
			try:
				normalized = normalize_and_validate_contact_phone(
					row.phone, result["country"], is_landline=row.custom_landline
				)
			except frappe.ValidationError as e:
				# The rule matched, but the number still doesn't actually
				# validate for that country (e.g. an Egyptian-shaped prefix
				# on a number with an invalid subscriber part) - report it,
				# same as an outright unresolved row, rather than saving a
				# country the number doesn't really validate against.
				failed.append((row.name, row.phone, result["country"], str(e)))
				frappe.db.set_value(
					"Contact Phone", row.name, "custom_country_resolution", "Unresolved", update_modified=False
				)
				unresolved += 1
			else:
				frappe.db.set_value(
					"Contact Phone",
					row.name,
					{
						"country": result["country"],
						"phone": normalized,
						"custom_country_resolution": result["confidence"].title(),
					},
					update_modified=False,
				)
				if result["confidence"] == "detected":
					detected += 1
				else:
					pattern += 1
		else:
			frappe.db.set_value(
				"Contact Phone", row.name, "custom_country_resolution", "Unresolved", update_modified=False
			)
			unresolved += 1

		if index % BATCH_SIZE == 0:
			frappe.db.commit()

	if rows:
		frappe.db.commit()

	print(
		f"contact_enhancements: backfill_contact_phone_country_resolution - "
		f"{detected} row(s) resolved by explicit prefix, {pattern} row(s) resolved by "
		f"the Egyptian mobile pattern, {unresolved} row(s) left unresolved for manual "
		f"review at the Phone Country Resolution page."
	)

	for name, phone, country, error in failed:
		frappe.log_error(
			title="contact_enhancements: resolved country failed re-normalization",
			message=(
				f"Contact Phone {name}: phone={phone!r} resolved to country={country!r} "
				f"but still failed validation - {error}\n\n"
				"Left as Unresolved by backfill_contact_phone_country_resolution - "
				"needs a human to look at it directly via the Phone Country Resolution page."
			),
		)
