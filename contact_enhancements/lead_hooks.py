# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

"""Lead doc_events for this app - Lead has no dedicated "primary address"
field of its own (confirmed via frappe.get_meta: only a native, Dynamic-
Link-based "Address & Contacts" connections widget), so "address
inheritance" here means something structurally different from Customer/
Supplier/Employee: Dynamic-Linking an Address directly to the Lead
itself, not backfilling a field.
"""

import frappe

from contact_enhancements.utils import dynamic_link_lookup, resolve_address_from_contact_links


def sync_lead_address_from_contact_links(doc, method=None):
	"""Lead on_update hook: if this Lead has no Address Dynamic-Linked to
	it yet, and its own auto-created Contact (native Lead.before_insert())
	is already linked to some other doctype this app tracks (a Customer,
	a Supplier, or a User) with an address, Dynamic-Link that Address
	directly to this Lead too - the same cross-doctype backfill Customer/
	Supplier/Employee already apply, adapted to Lead's own different
	shape (no primary-address field to set, only a Dynamic Link to
	create).

	on_update, not validate: Lead's own Contact only exists (and is only
	Dynamic-Linked to this Lead) after the very first insert - before
	that, doc.name isn't even assigned yet, so there's nothing yet for
	dynamic_link_lookup to find.

	Args:
		doc: the Lead document that was just saved.
		method: unused, present for the doc_events hook signature.
	"""
	existing = dynamic_link_lookup(
		{"parenttype": "Address", "link_doctype": "Lead", "link_name": doc.name}, "parent"
	)
	if existing:
		return

	contact_name = dynamic_link_lookup(
		{"parenttype": "Contact", "link_doctype": "Lead", "link_name": doc.name}, "parent"
	)
	if not contact_name:
		return

	address_name = resolve_address_from_contact_links(
		contact_name, exclude_doctype="Lead", exclude_name=doc.name
	)
	if not address_name:
		return

	address = frappe.get_doc("Address", address_name)
	address.append("links", {"link_doctype": "Lead", "link_name": doc.name})
	address.save(ignore_permissions=True)
