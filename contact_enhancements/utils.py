# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

"""Genuinely shared helpers, reused across every doctype this app links to
Contact (Customer, Supplier, and onward) - promoted here in Phase 1 once a
second real caller (Supplier) proved they're not Customer-specific.
"""

import frappe
from frappe import _


def dynamic_link_lookup(filters, return_field):
	"""Single low-level Dynamic Link query, reused everywhere this app
	resolves a Dynamic Link relationship instead of each caller
	hand-writing its own frappe.db.get_value("Dynamic Link", ...) call -
	three near-identical copies of this exact query existed side by side
	before being consolidated to this one place - see
	contact_enhancements/CLAUDE.md's performance checklist.

	Originally api.lead_lookup._dynamic_link_lookup - promoted to a real,
	public app surface here (Phase 1) once Supplier needed the same
	lookup lead_lookup.py's own Lead-specific callers already used;
	lead_lookup.py keeps a bare re-export so nothing there breaks.

	Args:
		filters: dict of Dynamic Link filter conditions (parenttype,
			parent, link_doctype, link_name - whichever subset identifies
			the relationship being looked up).
		return_field: which Dynamic Link column to return - "parent" to
			find the document Dynamic-Linked to something, "link_name" to
			find what a document is Dynamic-Linked to.

	Returns:
		The requested field's value, or None if no matching row exists.
	"""
	return frappe.db.get_value("Dynamic Link", filters, return_field)


def ensure_doc_linked_to_parent(parent_doc, fieldname, linked_doctype, linked_doc=None):
	"""Guarantee the parent doc's own plain Link field (to a Contact or an
	Address) is Dynamic-Linked back to it. Picking an existing record in
	that field never creates this link on its own - it's a plain Link
	field, unrelated to the Dynamic Link mechanism Contact/Address's own
	"links" table uses. Same append/has_link-guard/save pattern erpnext.
	selling.doctype.customer.customer.Customer.link_address_and_contact()
	already uses for the same purpose - reused, not reinvented.

	Generalized (originally Phase 1's ensure_contact_linked_to_parent,
	Contact-only) once this app needed the identical guarantee for
	Address too - "ensure a Contact/Address a doctype's own primary field
	points at is always in that Contact/Address's own reference table",
	not just for Contact.

	Args:
		parent_doc: a saved document (Customer, Supplier, Employee, ...)
			with a plain Link field naming a primary Contact/Address.
		fieldname: that field's own fieldname (e.g.
			"customer_primary_contact", "supplier_primary_address").
		linked_doctype: "Contact" or "Address" - which doctype fieldname
			points at.
		linked_doc: the already-loaded document for parent_doc's own
			fieldname value, if the caller already has one (avoids a
			second doctype loading the same record separately within the
			same hook invocation - see contact_enhancements/CLAUDE.md's
			performance checklist). Loaded here if not provided, so this
			still works called standalone.

	Returns:
		The document used, if one was passed in or had to be loaded to
		add a missing link - otherwise None. None therefore means either
		"nothing to link" (fieldname blank) or "already linked, so no
		document needed loading"; both mean the caller has nothing to
		reuse, and every caller that wants the document in that case
		loads it on demand anyway.
	"""
	linked_name = parent_doc.get(fieldname)
	if not linked_name:
		return None

	# Answer "is it already linked?" from whatever is cheapest. When the
	# caller handed us the document we can read its own links table in
	# memory; otherwise one indexed single-row read beats loading the
	# whole document (a Contact costs 4 SELECTs - parent + phone_nos +
	# email_ids + links - purely to reach has_link(), which is itself only
	# an in-memory loop). This runs on every save of Customer, Supplier,
	# Employee and User, and by far the most common answer is "yes,
	# already linked, do nothing".
	if linked_doc is not None:
		already_linked = linked_doc.has_link(parent_doc.doctype, parent_doc.name)
	else:
		already_linked = bool(
			dynamic_link_lookup(
				{
					"parenttype": linked_doctype,
					"parent": linked_name,
					"link_doctype": parent_doc.doctype,
					"link_name": parent_doc.name,
				},
				"name",
			)
		)

	if already_linked:
		return linked_doc

	# The old parent.save() applied a permission check before writing;
	# inserting the child row directly skips the whole document lifecycle,
	# so the check has to be made explicitly or this would silently become
	# a privilege escalation - anyone able to save the parent could write
	# a row into a Contact they have no right to modify.
	_check_may_link(parent_doc, linked_doctype, linked_name)

	# Deliberately never loads the document to do this - the row is
	# inserted directly, so the miss path costs one INSERT rather than a
	# full document load plus a full document save. See
	# _insert_dynamic_link for why the save had to go.
	_insert_dynamic_link(linked_doctype, linked_name, parent_doc.doctype, parent_doc.name)

	if linked_doc is not None:
		# Keep the caller's own copy consistent with the database. Without
		# this, a caller that later saves the document it handed us would
		# run Frappe's own update_child_table, which DELETES child rows
		# not present in the in-memory list - silently removing the link
		# just inserted.
		linked_doc.reload()
	return linked_doc


def party_identity_from_contact(contact_name):
	"""What a party record (Customer, ...) should be called, and whether it
	is a company or a person, derived from its Contact.

	Contact carries a native `company_name` field. When it's filled in, the
	Contact represents someone *at* a company, and the party being created
	for them is that company - so the party takes the company's name and is
	typed "Company". With it empty the Contact is just a person, and the
	party takes their own full name as an "Individual".

	This is the same rule api.lead_lookup._lead_snapshot already applies to
	a Lead's own company_name; it simply had no equivalent for a Contact
	that isn't attached to a Lead, so picking such a Contact produced an
	Individual named after the person even when the Contact clearly named
	an employer.

	Args:
		contact_name: the Contact to read.

	Returns:
		A dict {"party_name": ..., "party_type": "Company"|"Individual"},
		or None if the Contact is missing or names nothing usable.
	"""
	contact = frappe.db.get_value(
		"Contact", contact_name, ["full_name", "company_name"], as_dict=True
	)
	if not contact:
		return None

	if contact.company_name:
		return {"party_name": contact.company_name, "party_type": "Company"}
	if contact.full_name:
		return {"party_name": contact.full_name, "party_type": "Individual"}
	return None


def backfill_name_from_primary_contact(doc, contact_fieldname, name_fieldname):
	"""Fill a document's own name field from its primary Contact's
	full_name, but only when that name field is still blank.

	The server-side half of "picking a Contact fills in the name". The
	client-side half (contact_enhancements.prefill_from_contact, bound to
	each doctype's own primary-contact field) is the one that matters in
	Desk and cannot be replaced by this: every one of these name fields is
	mandatory, and Frappe's native client-side mandatory check runs before
	the save request is ever dispatched, so a still-blank field never
	reaches this hook at all (see CLAUDE.md section 1). This exists for
	every path where no browser is involved - the REST API, Data Import,
	another app creating records programmatically - which the client-side
	handler can never cover.

	Only ever fills a blank. Unlike
	contact_hooks.propagate_contact_changes_to_linked_doctypes, there is no
	Individual/Company gate here: that gate protects an existing trading
	name from being overwritten, and a blank field has no such name to
	lose - leaving it blank just fails the save instead.

	Args:
		doc: the document being validated.
		contact_fieldname: its primary-contact Link field.
		name_fieldname: the name field to fill.
	"""
	if doc.get(name_fieldname) or not doc.get(contact_fieldname):
		return

	full_name = frappe.db.get_value("Contact", doc.get(contact_fieldname), "full_name")
	if full_name:
		doc.set(name_fieldname, full_name)


def _check_may_link(parent_doc, linked_doctype, linked_name):
	"""Raise frappe.PermissionError unless the current user may write the
	Contact/Address about to be linked.

	Restores exactly what parent.save() used to enforce before this
	function started inserting the child row directly: the same "write"
	permission on the same document, honouring the same
	parent_doc.flags.ignore_permissions escape hatch that was previously
	passed straight through to save(). Without it, bypassing the document
	lifecycle for speed would also have bypassed authorization - anyone
	who can save a Customer could have written a row into any Contact.

	Checked against the document, not just the doctype, so User
	Permissions and owner-based rules still apply exactly as they did.
	That costs one document load, but only for a non-Administrator who is
	genuinely about to add a missing link: frappe.has_permission returns
	early for Administrator without loading anything, and the far more
	common "already linked" case returns before this is ever reached.

	Args:
		parent_doc: the document whose save triggered the link.
		linked_doctype: "Contact" or "Address".
		linked_name: the record being linked to.

	Raises:
		frappe.PermissionError: if the user may not write that record.
	"""
	if parent_doc.flags.ignore_permissions:
		return

	# frappe.has_permission (the top-level wrapper) takes `throw`, not the
	# `raise_exception` of frappe.permissions.has_permission underneath -
	# left False so the failure is raised below with a message naming both
	# records, rather than Frappe's generic one.
	if frappe.has_permission(linked_doctype, "write", doc=linked_name, throw=False):
		return

	raise frappe.PermissionError(
		_("Not permitted to link {0} {1} to this {2}.").format(
			_(linked_doctype), linked_name, _(parent_doc.doctype)
		)
	)


def _insert_dynamic_link(parenttype, parent, link_doctype, link_name):
	"""Add one row to a Contact's/Address's own "links" child table by
	inserting the child row directly, instead of appending to the parent
	document and saving it.

	This is the deadlock fix. The previous approach
	(parent.append(...) + parent.save()) ran a full document save - child
	table diffing, an UPDATE of the parent row, and inserts/deletes across
	its child tables - inside another document's on_update hook. Under any
	concurrency that is a wide, long-held lock footprint on tabContact
	acquired in the middle of someone else's transaction, and it
	deadlocked in practice: every QueryDeadlockError observed on this
	bench traced into this exact save. Inserting the one row we actually
	want touches one row in one table.

	It also removes the reason _resync_modified_after_side_effect_saves
	exists: a parent save bumped the Contact's `modified`, staling any
	copy the caller still held (the TimestampMismatchError this app's
	tests kept hitting). A child-row insert leaves the parent's own row,
	and therefore its timestamp, untouched.

	Two things a direct insert has to do that a parent save did for free:
	set idx (child rows are ordered), and invalidate the parent's document
	cache - frappe.get_cached_doc would otherwise keep handing out a
	Contact whose links table is missing the row we just added.

	NOTE: this bypasses permission checks, which parent.save() applied.
	That is deliberate - this link is integrity metadata about a
	relationship the parent document already established and was itself
	permitted to create, not user-initiated editing of the Contact. The
	practical effect is that linking now succeeds where it previously
	raised PermissionError for a user who can create a Customer but not
	edit the Contact they picked.

	Args:
		parenttype: "Contact" or "Address" - the document owning the table.
		parent: that document's name.
		link_doctype, link_name: what to link it to.
	"""
	next_idx = (
		frappe.db.count(
			"Dynamic Link",
			{"parenttype": parenttype, "parent": parent, "parentfield": "links"},
		)
		or 0
	) + 1

	frappe.get_doc(
		{
			"doctype": "Dynamic Link",
			"parenttype": parenttype,
			"parent": parent,
			"parentfield": "links",
			"link_doctype": link_doctype,
			"link_name": link_name,
			"idx": next_idx,
		}
	).db_insert()

	frappe.clear_document_cache(parenttype, parent)


def ensure_contact_linked_to_parent(parent_doc, primary_contact_fieldname, contact=None):
	"""Thin Contact-specific wrapper around ensure_doc_linked_to_parent -
	kept under this name since it's this app's most-used call site
	(Customer/Supplier/Employee all call it this way) and every existing
	caller/test already uses this exact signature.

	Args: see ensure_doc_linked_to_parent - primary_contact_fieldname is
	that function's own fieldname, contact is its own linked_doc.

	Returns:
		The Contact document, or None - see ensure_doc_linked_to_parent
		for exactly when each happens (None also covers "already linked,
		so nothing needed loading").
	"""
	return ensure_doc_linked_to_parent(parent_doc, primary_contact_fieldname, "Contact", linked_doc=contact)


_ADDRESS_LOOKUP_PRIORITY = ("Customer", "Supplier", "Lead", "User")

_PRIMARY_ADDRESS_FIELD_BY_DOCTYPE = {
	"Customer": "customer_primary_address",
	"Supplier": "supplier_primary_address",
}


def _linked_address_for(doctype, name):
	"""The one address _ADDRESS_LOOKUP_PRIORITY's given doctype+name
	already resolves to, if any - a plain Link field read for Customer/
	Supplier (both already keep this current via their own hooks), a
	Dynamic Link lookup for Lead (never has its own address
	field, only ever a Dynamic-Linked one) and User (this app's own
	"Linked Addresses" section, api.user_addresses - the first one linked,
	arbitrarily but deterministically by creation order, since a User can
	have more than one and this resolver only ever needs a single best
	guess).
	"""
	address_fieldname = _PRIMARY_ADDRESS_FIELD_BY_DOCTYPE.get(doctype)
	if address_fieldname:
		return frappe.db.get_value(doctype, name, address_fieldname)

	if doctype == "Lead":
		return dynamic_link_lookup({"parenttype": "Address", "link_doctype": "Lead", "link_name": name}, "parent")

	if doctype == "User":
		return frappe.db.get_value(
			"Dynamic Link",
			{"parenttype": "Address", "link_doctype": "User", "link_name": name},
			"parent",
		)

	return None


def resolve_address_from_contact_links(contact_name, exclude_doctype=None, exclude_name=None):
	"""Look at every doctype a Contact is already Dynamic-Linked to, and
	return the first address that connection already resolves to - in
	order of superiority: Customer, then Supplier, then Lead, then User.
	Used wherever a doctype with no "party" concept of
	its own (Employee, Supplier, User) picks a Contact that turns out to
	already be someone else's contact too, so a genuinely relevant address
	doesn't have to be re-entered from scratch.

	Deliberately Customer-first, matching this app's own established
	STRONG_PARTY_DOCTYPES ranking (api/duplicate_mobile_contacts.py) - a
	Customer/Supplier relationship is the most authoritative, already-
	enforced-mandatory source; Lead and User are the least (a Lead's own
	address is never mandatory, and a User's own linked Address is just
	one of potentially several with no "primary" concept at all).

	Args:
		contact_name: the Contact whose other links to search.
		exclude_doctype, exclude_name: skip this one specific link (the
			caller's own in-progress record) so a doctype never resolves
			its own address from itself circularly - e.g. a Supplier
			already Dynamic-Linked to this same Contact from an earlier
			save must not be treated as a valid source for its own
			address backfill.

	Returns:
		An Address name, or None if nothing in the chain resolves.
	"""
	links = frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Contact", "parent": contact_name},
		fields=["link_doctype", "link_name"],
	)
	linked_names_by_doctype = {}
	for link in links:
		linked_names_by_doctype.setdefault(link.link_doctype, []).append(link.link_name)

	for doctype in _ADDRESS_LOOKUP_PRIORITY:
		for name in linked_names_by_doctype.get(doctype, []):
			if doctype == exclude_doctype and name == exclude_name:
				continue
			address = _linked_address_for(doctype, name)
			if address:
				return address
	return None


# Every doctype worth checking when aggregating *all* addresses a Contact
# can reach - deliberately broader than _ADDRESS_LOOKUP_PRIORITY above
# (which only ever needs a single best guess to backfill one field, so it
# was never worth including Employee - a doctype this app only ever lets
# *pick* a Contact, never resolve *from*, in that narrower use case).
# Employee is a genuine address source here: employee_primary_address is
# just as real a source as Customer/Supplier's own field once the goal is
# "show every address connected to this person," not "what's the one most
# authoritative guess."
_ALL_ADDRESS_SOURCE_DOCTYPES = ("Customer", "Supplier", "Employee", "Lead", "User")

_ADDRESS_FIELD_BY_SOURCE_DOCTYPE = dict(_PRIMARY_ADDRESS_FIELD_BY_DOCTYPE, Employee="employee_primary_address")


def addresses_linked_to_many(names_by_doctype):
	"""Every Address tied to each of many doctype records at once - both
	the one named in a primary-address Link field (Customer/Supplier/
	Employee, if set) AND every Address Dynamic-Linked to that record
	directly, whether or not it's also the "primary" one.

	Batched deliberately, and this is the shared entry point for that rule
	- resolving one record at a time cost 2 queries *per linked record*
	inside get_all_addresses_for_contact's own loop, on a path that runs on
	every Contact and every User form render. This resolves any number of
	records in a fixed ~1 query per source doctype plus 1 for all the
	Dynamic Links together, so the cost stops scaling with how many
	doctypes a Contact is linked to. Anything else needing "which addresses
	does this record have" should call this rather than re-deriving the
	rule, which is why it's public.

	Confirmed the hard way live that checking the Dynamic Links (not just
	the primary-address field) is genuinely necessary, not redundant:
	ERPNext's own native "Address & Contacts" widget (and
	Customer.link_address_and_contact(), triggered by a Lead/Opportunity/
	Prospect conversion) Dynamic-Links an Address to a Customer/Supplier
	without ever touching customer_primary_address/supplier_primary_address
	themselves - the same gap customer_hooks.py's own backfill exists to
	close for the *primary* field, but a Customer can easily have a second
	or third Address only ever reachable via the Dynamic Link, never
	promoted to "primary" at all. An earlier version checked only the
	field and silently missed every one of those - reported live: a
	Customer's own Address & Contacts widget clearly showed an Address this
	app's own "Linked Addresses" table never surfaced.

	Also returns each source record's own display title in the same read,
	so callers rendering "via Customer: Acme Corp" don't need a second
	round-trip per address to look it up (address_source_label accepts
	that title directly).

	Args:
		names_by_doctype: {doctype: [record name, ...]} - doctypes should
			be from _ALL_ADDRESS_SOURCE_DOCTYPES; anything else simply
			resolves to no addresses.

	Returns:
		A 2-tuple (addresses_by_record, titles_by_record), both keyed by
		(doctype, record name). addresses_by_record values are lists of
		distinct Address names, primary first where one is set; a record
		with no addresses is absent from the dict entirely.
	"""
	addresses_by_record = {}
	titles_by_record = {}
	if not names_by_doctype:
		return addresses_by_record, titles_by_record

	requested = {
		(doctype, name) for doctype, names in names_by_doctype.items() for name in names
	}

	# One read per source doctype for the primary-address field and the
	# display title together - two things the old per-record path fetched
	# in two separate queries, one of them repeated once per address.
	for doctype, names in names_by_doctype.items():
		address_fieldname = _ADDRESS_FIELD_BY_SOURCE_DOCTYPE.get(doctype)
		title_field = _SOURCE_TITLE_FIELD.get(doctype)
		fields = ["name"] + [f for f in (address_fieldname, title_field) if f]
		if len(fields) == 1:
			continue
		for row in frappe.get_all(
			doctype, filters={"name": ["in", list(set(names))]}, fields=fields
		):
			key = (doctype, row["name"])
			if address_fieldname and row.get(address_fieldname):
				addresses_by_record.setdefault(key, []).append(row[address_fieldname])
			if title_field and row.get(title_field):
				titles_by_record[key] = row[title_field]

	# One Dynamic Link read covering every source record at once. Filtering
	# link_doctype and link_name with two independent "in" lists matches
	# their cross-product, so a Customer and a Lead that happen to share a
	# name would pick up each other's addresses - hence the explicit
	# membership check against the pairs actually asked for.
	for row in frappe.get_all(
		"Dynamic Link",
		filters={
			"parenttype": "Address",
			"link_doctype": ["in", list(names_by_doctype)],
			"link_name": ["in", list({name for _dt, name in requested})],
		},
		fields=["link_doctype", "link_name", "parent"],
	):
		key = (row.link_doctype, row.link_name)
		if key not in requested:
			continue
		existing = addresses_by_record.setdefault(key, [])
		if row.parent not in existing:
			existing.append(row.parent)

	return addresses_by_record, titles_by_record


def get_all_addresses_for_contact(contact_name, exclude_doctype=None, exclude_name=None):
	"""Every Address reachable from a Contact, across every doctype it's
	Dynamic-Linked to - a genuinely live, uncached aggregate (a plain SQL
	read computed fresh on every call, never a point-in-time copy seeded
	once and left to go stale) backing both Contact's own "Linked
	Addresses" table and User's own polished version of the same section.

	Unlike resolve_address_from_contact_links (a single best-guess
	backfill), this returns *everything* found, each tagged with which
	doctype/record it came from, deduplicated by Address name (the same
	physical Address can legitimately be the Customer's and the
	Supplier's own primary address at once, when the same person trades
	as both).

	Args:
		contact_name: the Contact whose other links to search.
		exclude_doctype, exclude_name: skip this one specific link (the
			caller's own in-progress record), same reasoning as
			resolve_address_from_contact_links's own parameters.

	Returns:
		A list of dicts: {"address": ..., "source_doctype": ...,
		"source_name": ..., "source_title": ...} - source_doctype/
		source_name identify which linked record this address came from,
		for display ("via Customer: Acme Corp"), and source_title is that
		record's own display name, already resolved so the caller doesn't
		have to look it up again per address.
	"""
	links = frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Contact", "parent": contact_name},
		fields=["link_doctype", "link_name"],
	)

	relevant_links = [
		link
		for link in links
		if link.link_doctype in _ALL_ADDRESS_SOURCE_DOCTYPES
		and not (link.link_doctype == exclude_doctype and link.link_name == exclude_name)
	]
	if not relevant_links:
		return []

	names_by_doctype = {}
	for link in relevant_links:
		names_by_doctype.setdefault(link.link_doctype, []).append(link.link_name)

	addresses_by_record, titles_by_record = addresses_linked_to_many(names_by_doctype)

	# Built by walking relevant_links (not the dicts above) so the output
	# order still follows the Contact's own link order, exactly as the
	# per-record version produced it.
	results = []
	seen_addresses = set()
	for link in relevant_links:
		key = (link.link_doctype, link.link_name)
		for address_name in addresses_by_record.get(key, []):
			if not address_name or address_name in seen_addresses:
				continue
			seen_addresses.add(address_name)
			results.append(
				{
					"address": address_name,
					"source_doctype": link.link_doctype,
					"source_name": link.link_name,
					"source_title": titles_by_record.get(key),
				}
			)
	return results


# A friendlier per-doctype "title" than a bare document name, for
# address_source_label's own "via Customer: Acme Corp" style output -
# falls back to the bare name itself for any doctype not listed here.
_SOURCE_TITLE_FIELD = {
	"Customer": "customer_name",
	"Supplier": "supplier_name",
	"Employee": "employee_name",
	"Lead": "lead_name",
}


def address_display_fields():
	"""The Address fields every "Linked Addresses" rendering in this app
	needs (Contact's own table, User's own polished section) - one place
	so both stay in sync if the display ever needs another field."""
	return [
		"name",
		"address_title",
		"address_type",
		"address_line1",
		"address_line2",
		"city",
		"country",
		"is_primary_address",
		"is_shipping_address",
	]


def address_source_label(
	source_doctype, source_name, direct_doctype=None, direct_name=None, title=None
):
	"""A short, human-readable label for one address's own source -
	shared by both Contact's and User's own "Linked Addresses" rendering
	so the two sections read consistently.

	Args:
		source_doctype, source_name: where this particular address
			actually came from (a get_all_addresses_for_contact result).
		direct_doctype, direct_name: the *caller's own* doctype/name, if
			the address is the caller's own direct link rather than one
			reached via another doctype (e.g. a User looking at an
			Address it Dynamic-Linked to itself directly) - shown as
			"Linked directly" instead of "via User: ...".
		title: the source record's own display title, if the caller
			already has it - get_all_addresses_for_contact now returns it
			as "source_title", fetched in the same read as the addresses
			themselves. Passing it skips the lookup below, which otherwise
			runs once per address and repeats identically for every
			address reached via the same record. Looked up here when not
			provided, so standalone callers still work.

	Returns:
		"Linked directly", or "via <Doctype>: <title>".
	"""
	if source_doctype == direct_doctype and source_name == direct_name:
		return "Linked directly"
	if not title:
		title_field = _SOURCE_TITLE_FIELD.get(source_doctype)
		title = frappe.db.get_value(source_doctype, source_name, title_field) if title_field else None
	return f"via {source_doctype}: {title or source_name}"
