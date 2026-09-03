# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

"""Supplier doc_events for this app (Phase 1) - the closest analog to
Customer's own hooks, since Supplier already has the same
supplier_primary_contact/supplier_primary_address Link-field shape.
"""

import frappe
from frappe import _

from contact_enhancements.utils import (
	dynamic_link_lookup,
	backfill_name_from_primary_contact,
	ensure_contact_linked_to_parent,
	ensure_doc_linked_to_parent,
	resolve_address_from_contact_links,
)


def backfill_supplier_name_from_primary_contact(doc, method=None):
	"""Supplier validate hook - fill supplier_name from the primary
	Contact's full_name when it's still blank. See
	utils.backfill_name_from_primary_contact for why this exists alongside
	the client-side prefill rather than instead of it.

	Args:
		doc: the Supplier being validated.
		method: unused, present for the doc_events hook signature.
	"""
	backfill_name_from_primary_contact(doc, "supplier_primary_contact", "supplier_name")


def backfill_supplier_primary_contact_from_dynamic_link(doc, method=None):
	"""Supplier validate hook: if supplier_primary_contact is blank, but a
	Contact is already Dynamic-Linked to this Supplier (e.g. added by hand
	via the Supplier's own Address & Contacts widget, without ever
	touching the supplier_primary_contact field itself), backfill it from
	that link - closes the one real gap Supplier has that Customer's own
	sync_customer_from_primary_contact doesn't need to (Customer has no
	equivalent "linked but not primary" gap, since its own
	customer_primary_contact is always the field this app's dialog sets
	directly).

	Only-if-blank, and only ever fills in an already-existing Dynamic
	Link - never fabricates one. Runs in validate() (before the mandatory
	check), the same reasoning customer_hooks.sync_customer_from_primary_
	contact's own backfill uses: catching this here means a Supplier
	created by some path that Dynamic-Links a Contact without setting the
	primary field directly (any future integration, a Data Import that
	populates Dynamic Link rows straight, etc.) doesn't spuriously trip
	the new supplier_primary_contact reqd Property Setter when a linked
	Contact already exists.

	Args:
		doc: the Supplier document being validated.
		method: unused, present for the doc_events hook signature.
	"""
	if doc.supplier_primary_contact:
		return

	linked_contact = dynamic_link_lookup(
		{"parenttype": "Contact", "link_doctype": "Supplier", "link_name": doc.name},
		"parent",
	)
	if linked_contact:
		doc.supplier_primary_contact = linked_contact


def sync_supplier_address_from_contact_links(doc, method=None):
	"""Supplier validate hook: if this Supplier's own primary contact is
	already linked to another doctype this app tracks (a Customer,
	another Supplier, a Lead, or a User) - the same real person/entity
	trades as more than one party of this business, a genuinely common
	case - backfill this Supplier's own address from whichever of those
	already has one, in order of superiority (Customer first, then
	Supplier, then Lead, then User - see utils.resolve_address_from_
	contact_links's own docstring for why), only if this Supplier doesn't
	already have one of its own.

	Only-if-blank, same as every other cross-doctype backfill in this app -
	never overwrites an address someone deliberately picked or entered
	here. Generalized from an earlier, Customer-only version of this
	function once it became clear the same reasoning applies to every
	doctype a Contact can already be linked to, not just Customer -
	Customer still wins first whenever it's among the matches, since
	resolve_address_from_contact_links checks it first.

	Args:
		doc: the Supplier document being validated.
		method: unused, present for the doc_events hook signature.
	"""
	if doc.supplier_primary_address or not doc.supplier_primary_contact:
		return

	address = resolve_address_from_contact_links(
		doc.supplier_primary_contact, exclude_doctype="Supplier", exclude_name=doc.name
	)
	if address:
		doc.supplier_primary_address = address


def enforce_primary_contact_on_new_supplier(doc, method=None):
	"""Supplier validate hook - require a primary Contact on a Supplier
	being created, and only then.

	Replaces the `reqd=1` Property Setter supplier_primary_contact used to
	carry, for the same reason as the Customer equivalent - see
	customer_hooks.enforce_primary_contact_and_address_on_new_customer for
	the full reasoning. On the dataset this was measured against, Supplier
	was the worse case: nearly half of existing Suppliers would have been
	frozen, and not one of them had a Contact that could be backfilled.

	supplier_primary_address is deliberately not required at all, new or
	otherwise - unlike Customer, a Supplier has no conversion source to
	inherit one from, and they are routinely onboarded with just a contact
	person.

	Runs last in Supplier's own validate list, after
	backfill_supplier_primary_contact_from_dynamic_link has had its chance
	to resolve one from an existing Dynamic Link.

	Args:
		doc: the Supplier being validated.
		method: unused, present for the doc_events hook signature.

	Raises:
		frappe.ValidationError: if a new Supplier has no primary Contact.
	"""
	# See the Customer equivalent for why ignore_mandatory is honoured.
	if not doc.is_new() or doc.flags.ignore_mandatory or doc.supplier_primary_contact:
		return

	frappe.throw(
		_("{0} is required for a new Supplier.").format(
			doc.meta.get_label("supplier_primary_contact")
		),
		title=_("Missing Primary Contact"),
	)


def link_primary_contact(doc, method=None):
	"""Supplier on_update hook: the same fix Customer's own
	link_primary_contact applies - picking an existing Contact/Address in
	supplier_primary_contact/supplier_primary_address does not, on its
	own, Dynamic-Link it back to this Supplier (both are plain Link
	fields, unrelated to the Dynamic Link mechanism Contact/Address's own
	"links" table uses). Thin calls into utils.ensure_contact_linked_to_
	parent/ensure_doc_linked_to_parent, the same shared logic Customer's
	own link_primary_contact now uses for both too.

	Unlike Customer, there's no Lead/Opportunity/Prospect conversion
	source to also sync fields from here, and no WhatsApp-from-Lead
	syncing either - Supplier has no such source doctype in this app's
	scope, so this hook stays this simple.

	Args:
		doc: the Supplier document that was just saved.
		method: unused, present for the doc_events hook signature.
	"""
	ensure_contact_linked_to_parent(doc, "supplier_primary_contact")
	ensure_doc_linked_to_parent(doc, "supplier_primary_address", "Address")
