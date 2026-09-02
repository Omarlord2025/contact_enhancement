# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

CONTACT_ENHANCEMENTS_CUSTOM_FIELDS = {
	"Contact": [
		{
			"fieldname": "linked_addresses_section",
			"fieldtype": "Section Break",
			"label": "Linked Addresses",
			"insert_after": "is_billing_contact",
			"description": (
				"Every Address reachable from this Contact, across every doctype it's "
				"linked to (Customer, Supplier, Employee, Lead, User) - a live view, "
				"recomputed every time this loads."
			),
		},
		{
			"fieldname": "linked_addresses_html",
			"fieldtype": "HTML",
			"label": "",
			"insert_after": "linked_addresses_section",
		},
	],
	"Contact Phone": [
		{
			"fieldname": "country",
			"fieldtype": "Link",
			"options": "Country",
			"label": "Country",
			"insert_after": "phone",
			"in_list_view": 1,
			"columns": 2,
			"reqd": 1,
			"default": "Egypt",
			"description": (
				"Which country's numbering plan THIS number is checked against - see "
				"contact_enhancements.contact_hooks.normalize_and_validate_contact_phones. "
				"One per number, not one for the whole Contact: a person can genuinely "
				"carry a local mobile and a foreign one side by side. Covers every "
				"country (via the phonenumbers library, the same one custom_webshop "
				"already uses for E.164 matching), not just Egypt. Defaults to Egypt, "
				"this business's primary market, so a row created by a path that never "
				"asks (e.g. ERPNext's own native auto-creation of a Contact from a new "
				"Lead) still ends up valid - override this whenever the number is "
				"genuinely from elsewhere."
			),
		},
		{
			"fieldname": "custom_phone_national",
			"fieldtype": "Data",
			"label": "Phone (National, search only)",
			"insert_after": "country",
			"hidden": 1,
			"read_only": 1,
			"in_list_view": 0,
			"in_standard_filter": 0,
			"description": (
				"Bare national-significant-number digits (no country code, e.g. "
				"\"01012345678\" for \"+201012345678\"), kept in sync automatically "
				"by contact_hooks.normalize_and_validate_contact_phones. Search-only: "
				"phone itself is the canonical, displayed, E.164 value - a LIKE search "
				"against phone alone can't reliably find a Contact by the local-format "
				"number staff are used to typing (the country-code digits get in the "
				"way), so api/lead_lookup._contact_search_query matches against this "
				"field too. Never edit directly - always derived from phone."
			),
		},
		{
			"fieldname": "custom_whatsapp",
			"fieldtype": "Check",
			"label": "WhatsApp",
			"insert_after": "is_primary_mobile_no",
			"in_list_view": 1,
			"columns": 1,
		},
		{
			"fieldname": "custom_telegram",
			"fieldtype": "Check",
			"label": "Telegram",
			"insert_after": "custom_whatsapp",
			"in_list_view": 1,
			"columns": 1,
		},
		{
			"fieldname": "custom_landline",
			"fieldtype": "Check",
			"label": "Landline",
			"insert_after": "custom_telegram",
			"in_list_view": 1,
			"columns": 1,
		},
	],
	"Employee": [
		{
			"fieldname": "primary_address_and_contact_section",
			"fieldtype": "Section Break",
			"label": "Addresses and Contacts",
			"insert_after": "relation",
		},
		{
			"fieldname": "employee_primary_address",
			"fieldtype": "Link",
			"options": "Address",
			"label": "Employee Primary Address",
			"insert_after": "primary_address_and_contact_section",
			"description": "Reselect, if the chosen address is edited after save",
		},
		{
			"fieldname": "employee_primary_address_column_break",
			"fieldtype": "Column Break",
			"insert_after": "employee_primary_address",
		},
		{
			"fieldname": "employee_primary_contact",
			"fieldtype": "Link",
			"options": "Contact",
			"label": "Employee Primary Contact",
			"insert_after": "employee_primary_address_column_break",
			"description": "Reselect, if the chosen contact is edited after save",
		},
	],
	"User": [
		{
			"fieldname": "user_contact_section",
			"fieldtype": "Section Break",
			"label": "Primary Contact",
			"insert_after": "mobile_no",
		},
		{
			"fieldname": "user_primary_contact",
			"fieldtype": "Link",
			"options": "Contact",
			"label": "User Primary Contact",
			"insert_after": "user_contact_section",
			"description": "Set via the onboarding dialog shown when creating a new User.",
		},
		{
			"fieldname": "linked_addresses_section",
			"fieldtype": "Section Break",
			"label": "Linked Addresses",
			"insert_after": "user_primary_contact",
			"description": (
				"Every Address Dynamic-Linked to this User - the same relationship "
				"mechanism Address's own links table always uses, reused directly "
				"rather than a new field or child doctype, since a User can "
				"legitimately need more than one."
			),
		},
		{
			"fieldname": "linked_addresses_html",
			"fieldtype": "HTML",
			"label": "",
			"insert_after": "linked_addresses_section",
		},
	],
}


def create_contact_enhancements_custom_fields():
	"""Add this app's custom fields: the mandatory per-row Contact Phone
	Country field and WhatsApp/Telegram/Landline channel checkboxes
	(Feature 1), plus Phase 3's own "Addresses and Contacts" section
	fields on Employee - the Section/Column Break fields that section
	needs.

	Country lives on Contact Phone, not Contact - moved here after an
	earlier Contact-level version turned out wrong: a Contact can have
	more than one number from more than one country, and "choosing a
	country should apply its rules" only makes sense read as "to the
	number sitting in that row" (see patches/move_country_to_contact_phone.py
	for the migration off the old field).

	create_custom_fields(update=True) is idempotent (safe to call on every
	install/migrate) and - unlike a plain frappe.make_property_setter call -
	already applies the resulting schema change (new columns, in this case)
	immediately via its own internal frappe.db.updatedb() call, so no
	separate schema-sync step is needed here the way
	setup/property_setters.py's search_index patch needed one.
	"""
	create_custom_fields(CONTACT_ENHANCEMENTS_CUSTOM_FIELDS, update=True)
