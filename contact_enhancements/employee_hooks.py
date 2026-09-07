# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

"""Employee doc_events for this app (Phase 3) - wholly new fields
(employee_primary_contact/employee_primary_address, setup/custom_fields.py),
nothing native to conflict with. Unlike the original, more conservative
version of this phase, Employee now gets the same proactive contact-picker
dialog UX as Customer/Supplier (public/js/employee.js's own onload
handler) - overridden by explicit product direction requesting
visual/behavioral consistency across every doctype this app touches, not
just an on-demand button. Still dismissible and not reqd, though: Employee
records are routinely created in batches by a small trusted HR group, and
a non-cancelable dialog there would have real cost for no real
duplicate-prevention gain the way it does for an external party like
Customer/Supplier.
"""

from frappe.contacts.doctype.contact.contact import get_contact_name

from contact_enhancements.utils import (
	backfill_name_from_primary_contact,
	ensure_contact_linked_to_parent,
	ensure_doc_linked_to_parent,
	resolve_address_from_contact_links,
)


def backfill_employee_name_from_primary_contact(doc, method=None):
	"""Employee validate hook - fill first_name (relabeled "Full Name")
	from the primary Contact's full_name when it's still blank. See
	utils.backfill_name_from_primary_contact.

	Args:
		doc: the Employee being validated.
		method: unused, present for the doc_events hook signature.
	"""
	backfill_name_from_primary_contact(doc, "employee_primary_contact", "first_name")


def sync_employee_contact_from_user(doc, method=None):
	"""Employee validate hook: if user_id is set and employee_primary_contact
	is blank, backfill it from get_contact_name(user_id) - Frappe core's own
	email-to-Contact resolver, the same one User.create_contact() already
	uses natively. Only-if-found - never creates a Contact, so this stays
	near-zero-risk the way the original plan for this phase intended.

	Args:
		doc: the Employee document being validated.
		method: unused, present for the doc_events hook signature.
	"""
	if doc.employee_primary_contact or not doc.user_id:
		return

	contact_name = get_contact_name(doc.user_id)
	if contact_name:
		doc.employee_primary_contact = contact_name


def sync_employee_address_from_contact_links(doc, method=None):
	"""Employee validate hook: if employee_primary_contact is already
	linked to another doctype this app tracks (a Customer, a Supplier,
	another Employee sharing the same Contact, a Lead, or a User),
	backfill this Employee's own address from whichever of those already
	has one, in order of superiority - see utils.resolve_address_from_
	contact_links's own docstring for the exact ordering and reasoning.
	The same cross-doctype backfill supplier_hooks.sync_supplier_address_
	from_contact_links already does for Supplier, reused here rather than
	reimplemented. exclude_doctype/exclude_name below is what stops this
	Employee's own (still blank) address from being read back as its own
	source when it's the only Employee linked to this Contact.

	Only-if-blank - never overwrites an address someone deliberately
	picked here.

	Args:
		doc: the Employee document being validated.
		method: unused, present for the doc_events hook signature.
	"""
	if doc.employee_primary_address or not doc.employee_primary_contact:
		return

	address = resolve_address_from_contact_links(
		doc.employee_primary_contact, exclude_doctype="Employee", exclude_name=doc.name
	)
	if address:
		doc.employee_primary_address = address


def link_employee_contact(doc, method=None):
	"""Employee on_update hook: Dynamic-Link employee_primary_contact/
	employee_primary_address back to this Employee - the same fix every
	other doctype in this app applies (picking a Contact/Address in a
	plain Link field never creates this link on its own), and the same
	"must show up in that record's own linking reference table"
	requirement supplier_hooks.link_primary_contact now also satisfies.

	Args:
		doc: the Employee document that was just saved.
		method: unused, present for the doc_events hook signature.
	"""
	ensure_contact_linked_to_parent(doc, "employee_primary_contact")
	ensure_doc_linked_to_parent(doc, "employee_primary_address", "Address")
