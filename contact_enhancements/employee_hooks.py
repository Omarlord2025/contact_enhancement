# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

"""Employee doc_events for this app (Phase 3) - wholly new fields
(employee_primary_contact/employee_primary_address, setup/custom_fields.py),
nothing native to conflict with. Unlike the original, more conservative
version of this phase, Employee now gets the same proactive contact-picker
dialog UX as Customer/Supplier (public/js/employee.js's own onload
handler) - overridden by explicit product direction requesting
visual/behavioral consistency across every doctype this app touches, not
just an on-demand button.

employee_primary_contact is now required on a new Employee (see
enforce_primary_contact_on_new_employee below) - the same is_new()-gated
validate-hook pattern Customer/Supplier already use, never a static reqd
Property Setter, for the same reason documented at length for those two:
a static reqd is evaluated on every save, not just inserts, and would
retroactively freeze every pre-existing Employee record. Checked before
adding this: no native ERPNext/Frappe flow creates an Employee document
bypassing this app's own hooks (unlike Lead.create_contact() or the
several native flows that insert a User directly - see user_hooks.py's own
docstring for why User does NOT get this same treatment), so there is no
equivalent regression class to guard against here.

The dialog itself stays dismissible (no_cancel: false) rather than
becoming non-cancelable like Customer/Supplier's - Employee records are
routinely created in batches by a small trusted HR group, and forcing a
non-cancelable modal there would have real cost for no real
duplicate-prevention gain the way it does for an external party. Dismissing
it no longer just skips a convenience, though: public/js/employee.js's own
refresh/employee_primary_contact handlers now disable Save on a new,
still-contact-less Employee (the same frm.disable_save()/enable_save() gate
customer.js already uses) so the requirement is visible immediately rather
than only surfacing as a server round-trip error at Save.
"""

import frappe
from frappe import _
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


def enforce_primary_contact_on_new_employee(doc, method=None):
	"""Employee validate hook - require a primary Contact on an Employee
	being created, and only then. Mirrors customer_hooks.
	enforce_primary_contact_on_new_customer/supplier_hooks.
	enforce_primary_contact_on_new_supplier exactly - see either one's own
	docstring for why this is a validate-hook gated on is_new() rather
	than a static reqd Property Setter (a static reqd is evaluated on
	every save, not just inserts, and would retroactively freeze every
	Employee record that predates this requirement).

	Runs after the backfills earlier in the same validate list
	(sync_employee_contact_from_user in particular can resolve this from
	doc.user_id on its own), so anything already legitimately resolved is
	not reported as missing.

	employee_primary_address is deliberately NOT required here, matching
	Customer/Supplier/the rest of this app - see customer_hooks.
	enforce_primary_contact_on_new_customer's own docstring for the full
	reasoning (a brand-new Contact created through the picker dialog has
	no Address yet, so requiring one up front would be a dead end).

	Args:
		doc: the Employee being validated.
		method: unused, present for the doc_events hook signature.

	Raises:
		frappe.ValidationError: if a new Employee has no primary Contact.
	"""
	# ignore_mandatory is Frappe's standard "skip required-field checks"
	# escape hatch - honouring it keeps this consistent with the identical
	# check on Customer/Supplier, and with what a reqd Property Setter
	# would have done for every caller that already relies on it (Data
	# Import, programmatic setup).
	if not doc.is_new() or doc.flags.ignore_mandatory:
		return

	if not doc.get("employee_primary_contact"):
		frappe.throw(
			_("{0} is required for a new Employee.").format(doc.meta.get_label("employee_primary_contact")),
			title=_("Missing Primary Contact"),
		)


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
