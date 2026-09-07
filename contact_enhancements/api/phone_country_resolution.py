# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

"""Server side of the Phone Country Resolution page (contact_enhancements/
contact_enhancements/page/phone_country_resolution) - the manual-resolution
queue for Contact Phone rows patches.backfill_contact_phone_country_
resolution couldn't resolve automatically (Contact Phone.custom_country_
resolution == "Unresolved"). See that patch and contact_hooks.
resolve_phone_country for how a row ends up there.
"""

import frappe
from frappe import _
from frappe.utils import cint


@frappe.whitelist()
def get_unresolved_phone_country_report():
	"""Every Contact Phone row still awaiting manual country resolution.

	Two queries, not one join plus N lookups: Contact Phone rows first,
	then a single batched Contact fetch for their full_name - avoids one
	Contact read per row (see contact_enhancements/CLAUDE.md's performance
	checklist).

	Returns:
		List of {contact, contact_phone_row, phone, custom_landline,
		full_name} dicts, one per unresolved row.
	"""
	rows = frappe.get_all(
		"Contact Phone",
		filters={"custom_country_resolution": "Unresolved"},
		fields=["name", "parent", "phone", "custom_landline"],
	)
	if not rows:
		return []

	contacts = frappe.get_all(
		"Contact",
		filters={"name": ["in", list({row.parent for row in rows})]},
		fields=["name", "full_name"],
	)
	full_name_by_contact = {contact.name: contact.full_name for contact in contacts}

	return [
		{
			"contact": row.parent,
			"contact_phone_row": row.name,
			"phone": row.phone,
			"custom_landline": row.custom_landline,
			"full_name": full_name_by_contact.get(row.parent) or "",
		}
		for row in rows
	]


@frappe.whitelist()
def list_countries():
	"""Every Country name, for the report page's own Country dropdown -
	self-contained rather than depending on a generic client-list call.

	Returns:
		List of Country names, alphabetically.
	"""
	return [country.name for country in frappe.get_all("Country", fields=["name"], order_by="name")]


@frappe.whitelist()
def resolve_phone_country_row(contact, contact_phone_row, country, landline=0):
	"""Apply an administrator's manual country/landline choice to one
	unresolved Contact Phone row, from the Phone Country Resolution page.

	Reloads the parent Contact fresh (never trusts the report snapshot the
	page was rendered from) and saves it - which runs the full, existing
	Contact validate hook chain (contact_hooks.normalize_and_validate_
	contact_phones re-normalizes/validates via normalize_and_validate_
	contact_phone, enforce_contact_phone_channel_exclusivity, enforce_
	unique_mobile_number, the dedupe warning) exactly as-is. Nothing here
	reimplements any of that. doc.save()'s own built-in write-permission
	check is the permission gate - no explicit frappe.has_permission call,
	matching every other whitelisted endpoint in this app (e.g. api.
	contact_dedupe.merge_duplicate_mobile_contacts relies the same way on
	the underlying core call's own permission check).

	On any failure (bad country, a number that still doesn't validate for
	the chosen country, no permission), the exception propagates and the
	request's DB changes roll back - custom_country_resolution stays
	"Unresolved", so the row is still in the report next load.

	Args:
		contact: name of the Contact that owns the row.
		contact_phone_row: name of the Contact Phone child row to resolve.
		country: name of the Country record the administrator chose.
		landline: truthy if the administrator checked "Landline".

	Returns:
		{"ok": True} once the row has been saved as resolved.

	Raises:
		frappe.ValidationError: the row no longer exists, country isn't a
			real Country record, or the number still doesn't validate
			against it.
	"""
	if not frappe.db.exists("Country", country):
		frappe.throw(_("{0} is not a valid Country.").format(country))

	contact_doc = frappe.get_doc("Contact", contact)

	row = next((r for r in contact_doc.phone_nos if r.name == contact_phone_row), None)
	if not row:
		frappe.throw(
			_("This phone row no longer exists on {0} - it may have already been resolved or removed.").format(
				contact
			)
		)

	row.country = country
	row.custom_landline = cint(landline)
	row.custom_country_resolution = "Manual"

	contact_doc.save()

	return {"ok": True}
