# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

"""Whitelisted API backing User's own "Linked Addresses" section
(public/js/user.js) - the user's own chosen design for "a User can have
multiple linked Addresses": reuse Address's existing Dynamic Link
mechanism directly (the same "links" child table Contact/Customer/
Supplier/Employee all already use), rendered as a custom list
+ "Add Address" button, rather than a new child doctype or a single Link
field (which could only ever hold one Address).

No doc_events needed here - unlike every other doctype this app touches,
there's no "primary address" field to auto-resolve or keep in sync, just
a plain many-to-many relationship a person manages directly through this
section.
"""

import frappe

from contact_enhancements.utils import (
	address_display_fields,
	address_source_label,
	get_all_addresses_for_contact,
)


@frappe.whitelist()
def get_user_addresses(user):
	"""List every Address Dynamic-Linked to this User, newest first.

	Args:
		user: the User's own name (email).

	Returns:
		A list of Address dicts (see utils.address_display_fields), each also
		carrying the fields public/js/user.js needs to render one row.
	"""
	address_names = frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Address", "link_doctype": "User", "link_name": user},
		pluck="parent",
	)
	if not address_names:
		return []

	return frappe.get_all(
		"Address",
		filters={"name": ["in", address_names]},
		fields=address_display_fields(),
		order_by="creation desc",
	)


@frappe.whitelist()
def search_addresses_for_user(doctype, txt, searchfield, start, page_len, filters):
	"""Link-field-style query for the "Add Address" dialog's own "search an
	existing Address" field - unrestricted (every Address in the system,
	not just ones already linked to some other doctype), matched by title
	or address line, mirroring the same "broaden the native restrictive
	default" fix applied to customer_primary_address/supplier_primary_address
	(see contact_enhancements/CLAUDE.md) rather than leaving Address's own
	native default query (also typically link-scoped) in place here.

	Args:
		doctype, txt, searchfield, start, page_len, filters: standard
			Frappe Link-field query arguments; only txt/start/page_len used.

	Returns:
		Rows of (name, address_title) matching txt.
	"""
	like_txt = f"%{txt}%"
	return frappe.get_all(
		"Address",
		filters=[
			["Address", "name", "like", like_txt],
		],
		or_filters=[
			["address_title", "like", like_txt],
			["address_line1", "like", like_txt],
			["city", "like", like_txt],
		],
		fields=["name", "address_title"],
		start=start,
		page_length=page_len,
		as_list=True,
	)


@frappe.whitelist()
def link_existing_address_to_user(address, user):
	"""Dynamic-Link an already-existing Address to a User - the "search an
	existing Address" path of the "Add Address" dialog.

	Idempotent: linking an Address already linked to this User is a no-op,
	not a duplicate row.

	Args:
		address: an existing Address's own name.
		user: the User's own name (email).

	Returns:
		The Address's own name.
	"""
	doc = frappe.get_doc("Address", address)
	if not doc.has_link("User", user):
		doc.append("links", {"link_doctype": "User", "link_name": user})
		doc.save()
	return doc.name


@frappe.whitelist()
def create_and_link_address(user, address_title, address_type, address_line1, city, country):
	"""Create a new Address and Dynamic-Link it to a User in one call - the
	"create a new Address" path of the "Add Address" dialog, mirroring
	contact_lookup.create_minimal_contact's own "create genuinely minimal,
	link immediately" pattern rather than routing through Frappe's generic
	Link-field "+ Create a new Address" flow.

	Args:
		user: the User's own name (email) to link the new Address to.
		address_title: Address.address_title.
		address_type: Address.address_type.
		address_line1: Address.address_line1.
		city: Address.city.
		country: name of a Country record.

	Returns:
		The new Address's own name.
	"""
	address = frappe.new_doc("Address")
	address.address_title = address_title
	address.address_type = address_type
	address.address_line1 = address_line1
	address.city = city
	address.country = country
	address.append("links", {"link_doctype": "User", "link_name": user})
	address.insert()
	return address.name


@frappe.whitelist()
def unlink_address_from_user(address, user):
	"""Remove the Dynamic Link between an Address and a User, without
	deleting the Address itself - the section's own "Remove" action per
	listed row. A no-op if the link doesn't exist (e.g. a stale client-side
	list re-clicked after another tab already removed it).

	Args:
		address: the Address's own name.
		user: the User's own name (email).
	"""
	doc = frappe.get_doc("Address", address)
	remaining = [
		row for row in doc.links if not (row.link_doctype == "User" and row.link_name == user)
	]
	if len(remaining) == len(doc.links):
		return
	doc.links = remaining
	doc.save()


@frappe.whitelist()
def get_all_addresses_for_user(user=None, contact=None):
	"""Live, always-current view of every Address connected to this User -
	both directly Dynamic-Linked to the User itself (managed via the "Add
	Address" button below), AND reachable via the same Contact from every
	other doctype it's linked to (Customer/Supplier/Employee/Lead) -
	computed fresh on every call (utils.get_all_addresses_for_contact is a
	plain SQL read, never a cached/seeded copy), so an address added or
	removed anywhere in that chain shows up here immediately, not just the
	one time a doc_event happened to run.

	Works even for a brand-new, unsaved User (user blank/None) as long as
	a Contact has already been picked - the direct-link half is simply
	skipped (nothing can be Dynamic-Linked to a User that doesn't exist in
	the database yet), but the cross-doctype half already works, which is
	the whole point of syncing this live, before Save.

	Args:
		user: the User's own name (email), or blank for a new, unsaved User.
		contact: the Contact currently set as this User's own primary
			contact, if any.

	Returns:
		A list of Address dicts (utils.address_display_fields) with two extra
		keys: "source_label" (a short human string - "Linked directly",
		or "via Customer: Acme Corp") and "removable" (True only for
		addresses directly Dynamic-Linked to this User - the only ones
		the "Remove" button can actually act on; an address reached only
		via another doctype is informational, not owned by this User).
	"""
	address_names_direct = set()
	if user:
		address_names_direct = set(
			frappe.get_all(
				"Dynamic Link",
				filters={"parenttype": "Address", "link_doctype": "User", "link_name": user},
				pluck="parent",
			)
		)

	# The third tuple slot is the source record's display title, which
	# get_all_addresses_for_contact now resolves in the same read as the
	# addresses - so labelling below costs no extra queries. A directly
	# linked Address needs none (it renders as "Linked directly").
	source_by_address = {name: ("User", user, None) for name in address_names_direct}

	if contact:
		for row in get_all_addresses_for_contact(contact, exclude_doctype="User", exclude_name=user):
			source_by_address.setdefault(
				row["address"], (row["source_doctype"], row["source_name"], row.get("source_title"))
			)

	if not source_by_address:
		return []

	addresses = frappe.get_all(
		"Address",
		filters={"name": ["in", list(source_by_address.keys())]},
		fields=address_display_fields(),
		order_by="creation desc",
	)
	for addr in addresses:
		source_doctype, source_name, source_title = source_by_address[addr.name]
		addr["removable"] = addr.name in address_names_direct
		addr["source_label"] = address_source_label(
			source_doctype, source_name, direct_doctype="User", direct_name=user, title=source_title
		)
	return addresses
