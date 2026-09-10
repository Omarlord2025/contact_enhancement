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
from contact_enhancements.api.lead_lookup import (
	_sync_whatsapp_to_contact,
	get_contact_snapshot_for_lead,
)
from contact_enhancements.utils import (
	_insert_dynamic_link,
	dynamic_link_lookup,
	ensure_contact_linked_to_parent,
	resolve_address_from_contact_links,
)


def backfill_lead_from_primary_contact(doc, method=None):
	"""Lead validate hook: if lead_primary_contact is set, backfill blank Lead
	fields from the Contact's snapshot.
	"""
	if not doc.get("lead_primary_contact"):
		return

	snapshot = get_contact_snapshot_for_lead(doc.lead_primary_contact)
	if not snapshot:
		return

	for field in (
		"lead_name",
		"first_name",
		"last_name",
		"company_name",
		"mobile_no",
		"phone",
		"whatsapp_no",
		"email_id",
		"country",
		"gender",
		"salutation",
		"job_title",
	):
		if not doc.get(field) and snapshot.get(field):
			doc.set(field, snapshot[field])

	if doc.get("whatsapp_no"):
		try:
			contact = frappe.get_doc("Contact", doc.lead_primary_contact)
			_sync_whatsapp_to_contact(contact, doc)
			if contact.is_dirty():
				contact.save(ignore_permissions=True)
		except Exception:
			pass


def link_lead_contact(doc, method=None):
	"""Lead on_update hook: guarantee that lead_primary_contact is
	Dynamic-Linked to this Lead in Contact.links.
	"""
	if doc.get("lead_primary_contact"):
		ensure_contact_linked_to_parent(doc, "lead_primary_contact")


def sync_lead_address_from_contact_links(doc, method=None):
	"""Lead on_update hook: if this Lead has no Address Dynamic-Linked to
	it yet, Dynamic-Link an Address from the linked Contact.
	"""
	existing = dynamic_link_lookup(
		{"parenttype": "Address", "link_doctype": "Lead", "link_name": doc.name}, "parent"
	)
	if existing:
		return

	contact_name = doc.get("lead_primary_contact") or dynamic_link_lookup(
		{"parenttype": "Contact", "link_doctype": "Lead", "link_name": doc.name}, "parent"
	)
	if not contact_name:
		return

	address_name = resolve_address_from_contact_links(
		contact_name, exclude_doctype="Lead", exclude_name=doc.name
	)
	if not address_name:
		return

	_insert_dynamic_link("Address", address_name, "Lead", doc.name)

