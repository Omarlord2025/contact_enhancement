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

from contact_enhancements.contact_hooks import validate_full_name_has_at_least_three_words
from contact_enhancements.utils import (
	ensure_contact_linked_to_parent,
	ensure_doc_linked_to_parent,
	resolve_address_from_contact_links,
)


def enforce_full_name_has_at_least_three_words(doc, method=None):
	"""Employee validate hook: first_name (relabeled "Full Name" -
	setup/property_setters.py's create_employee_full_name_property_setters)
	must have at least three words once it's actually set - see contact_
	hooks.validate_full_name_has_at_least_three_words's own docstring for
	the full reasoning, including why this is safe to enforce
	unconditionally here (unlike a blanket Contact-level rule): the one
	native programmatic Employee-creation flow in this bench (erpnext's
	own setup wizard "create employee for self") leaves first_name blank
	entirely, which this passes through untouched.

	Args:
		doc: the Employee document being validated.
		method: unused, present for the doc_events hook signature.
	"""
	validate_full_name_has_at_least_three_words(doc.first_name)


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
	linked to another doctype this app tracks (a Customer, a Supplier, a
	Lead, or a User), backfill this Employee's own address from whichever
	of those already has one, in order of superiority - see
	utils.resolve_address_from_contact_links's own docstring for the
	exact ordering and reasoning. The same cross-doctype backfill
	supplier_hooks.sync_supplier_address_from_contact_links already does
	for Supplier, reused here rather than reimplemented.

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
