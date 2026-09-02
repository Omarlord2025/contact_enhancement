# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

import frappe

from contact_enhancements.api.lead_lookup import (
	_get_contact_lead,
	_lead_snapshot,
	_sync_whatsapp_to_contact,
)
from contact_enhancements.utils import (
	ensure_contact_linked_to_parent,
	ensure_doc_linked_to_parent,
	resolve_address_from_contact_links,
)


# Fields Frappe itself pre-fills on a brand-new Customer before this hook
# ever runs - confirmed empirically via frappe.new_doc("Customer"):
# customer_type defaults to "Company" at the DocField level;
# territory/customer_group get pre-filled from Selling Settings' global
# defaults; language gets pre-filled from the session/site default (not a
# DocField default either - Frappe applies it generically for any doctype
# with a "language" field). For these four, "only set if currently blank"
# silently never applies on a new doc (confirmed: a Lead with no
# company_name, which should produce customer_type "Individual", was still
# ending up "Company" because the field was already non-blank before this
# hook ran) - so on a new doc the Lead wins unconditionally instead. Every
# other snapshot field has no such default, so the ordinary "only if blank"
# guard is safe for them and is what protects a value the user deliberately
# typed/picked (e.g. a hand-edited customer_name) from being overwritten.
FIELDS_WITH_ENVIRONMENT_DEFAULTS = ("customer_type", "customer_group", "territory", "language")


def _apply_lead_field(doc, fieldname, value, pristine):
	"""Set one Customer field from the Lead snapshot.

	The default rule is "only fill in what's genuinely still blank", so
	real saved data is never clobbered. For fields in
	FIELDS_WITH_ENVIRONMENT_DEFAULTS, "blank" can't be trusted whenever the
	caller passes a non-None `pristine` (see sync_customer_from_primary_contact
	for exactly when that is - both a brand-new Customer, where Frappe
	itself silently pre-fills these before this hook ever runs, and an
	existing one whose primary contact is being set for the first time,
	where Quick Entry already left them at that same pristine state) - so
	instead the Lead wins whenever the current value still exactly matches
	`pristine`, a throwaway untouched frappe.new_doc("Customer") the caller
	builds once for comparison, meaning nothing - not the user, not this
	hook on an earlier run - has actually chosen a value yet. A value that
	differs from that pristine default, even one the user picked that
	happens to equal the Lead's own value, is left alone rather than
	guessed at.

	Args:
		doc: the Customer document being synced.
		fieldname: Customer fieldname to set.
		value: value from the Lead snapshot; a no-op if blank/None.
		pristine: a fresh, untouched Customer document for comparison, or
			None to skip the pristine check entirely (falling back to the
			plain "only if blank" rule).
	"""
	if not value:
		return

	still_pristine = (
		pristine is not None
		and fieldname in FIELDS_WITH_ENVIRONMENT_DEFAULTS
		and doc.get(fieldname) == pristine.get(fieldname)
	)
	if still_pristine or not doc.get(fieldname):
		doc.set(fieldname, value)


def _find_linked_docs_from_source(doc, parenttypes):
	"""Look up a Contact and/or Address already Dynamic-Linked to whichever
	of this Customer's lead_name/opportunity_name/prospect_name is set -
	one query for however many parenttypes are requested at once, instead
	of one query per parenttype (this used to be a single-parenttype
	function called twice in a row from sync_customer_from_primary_contact
	- once for "Contact", once for "Address" - exactly the kind of
	redundant round-trip contact_enhancements/CLAUDE.md's performance
	checklist flags; consolidated to one query here).

	Mirrors the exact lookup erpnext.selling.doctype.customer.customer.
	Customer.link_address_and_contact() uses to re-link a Contact or
	Address from a Lead/Opportunity/Prospect conversion (its own Dynamic
	Link query filters on `parenttype in ("Contact", "Address")`) - but
	that method only runs in on_update, after the row is already written,
	too late to satisfy a mandatory-field check. This makes the same
	documents available during validate() instead, so
	customer_primary_contact/customer_primary_address being mandatory
	(contact_enhancements/setup/property_setters.py) doesn't break
	ERPNext's own native "Create > Customer" button on Lead - confirmed by
	reproducing that exact flow: it never sets customer_primary_contact
	itself, only creates the Dynamic Link, and without this backfill the
	insert throws MandatoryError before that Dynamic Link is ever used.
	The same gap applies equally to customer_primary_address, which
	link_address_and_contact() also never sets directly.

	Checks only the first of lead_name/opportunity_name/prospect_name
	that's actually set, not all three independently per parenttype - in
	normal use exactly one of the three is ever set on a Customer at a
	time (a Customer is converted from one source document), so this
	doesn't need to fall through to a second source the way two
	independent single-parenttype lookups used to.

	Args:
		doc: the Customer document being validated.
		parenttypes: an iterable of "Contact"/"Address" (or both) to look up.

	Returns:
		A dict mapping each requested parenttype to a document name, or
		None for any parenttype with no linked document (or if none of
		the three source fields are set at all).
	"""
	found = dict.fromkeys(parenttypes)
	for source_doctype, source_name in (
		("Lead", doc.lead_name),
		("Opportunity", doc.opportunity_name),
		("Prospect", doc.prospect_name),
	):
		if not source_name:
			continue
		rows = frappe.get_all(
			"Dynamic Link",
			filters={
				"parenttype": ["in", list(parenttypes)],
				"link_doctype": source_doctype,
				"link_name": source_name,
			},
			fields=["parenttype", "parent"],
		)
		for row in rows:
			found[row.parenttype] = row.parent
		break
	return found


def sync_customer_from_primary_contact(doc, method=None):
	"""Customer validate hook: if customer_primary_contact is set and linked
	to a Lead, prefill this Customer's identity fields from that Lead - see
	_apply_lead_field for exactly what "prefill" means. Runs pre-write so
	the values are included in this same save. Mirrors what
	contact_enhancements/public/js/customer.js already showed the user live
	in the browser via get_lead_snapshot_for_contact, so this is correct for
	any path that sets customer_primary_contact, not just that one UI
	moment (API calls, data imports, editing an existing Customer's primary
	contact later).

	Also backfills customer_primary_contact and customer_primary_address
	themselves, via _find_linked_docs_from_source, whenever either is
	blank but this Customer is being created from a Lead/Opportunity/
	Prospect that already has one linked - see that function's docstring
	for why (and for why both are resolved in one query, not two). For
	customer_primary_address specifically, that covers the case where
	lead_name (etc) is already set going into this save (e.g. native Lead
	conversion); the case where it *isn't* yet - this app's own
	contact-picker dialog, where lead_name only gets discovered below, from
	whichever Contact the user just picked - is covered for free by
	customer_primary_address being part of the _lead_snapshot dict applied
	in the loop further down, guarded the same "only if still blank" way
	as every other snapshot field.

	Args:
		doc: the Customer document being validated.
		method: unused, present for the doc_events hook signature.
	"""
	if not doc.customer_primary_contact or not doc.customer_primary_address:
		linked = _find_linked_docs_from_source(doc, ("Contact", "Address"))
		if not doc.customer_primary_contact:
			doc.customer_primary_contact = linked["Contact"]
		if not doc.customer_primary_address:
			doc.customer_primary_address = linked["Address"]

	if not doc.customer_primary_contact:
		return

	# Broader fallback, once the more specific Lead/Opportunity/Prospect
	# source lookup above (and the Lead-snapshot one below) have both had
	# their chance: if this Customer's own primary contact is already
	# linked to some other doctype this app tracks (a Supplier, a Lead,
	# or a User) that already has an address, reuse it - same cross-
	# doctype backfill supplier_hooks.sync_supplier_
	# address_from_contact_links/employee_hooks.sync_employee_address_
	# from_contact_links already apply, generalized here to every doctype
	# this app touches, not just those two. Customer itself is naturally
	# excluded from resolve_address_from_contact_links's own priority
	# order (it only ever looks at *other* doctypes a Contact is linked
	# to), so no exclude_doctype/exclude_name is needed here the way
	# Supplier/Employee need to guard against resolving from themselves.
	if not doc.customer_primary_address:
		address = resolve_address_from_contact_links(doc.customer_primary_contact)
		if address:
			doc.customer_primary_address = address

	lead_name = _get_contact_lead(doc.customer_primary_contact)
	if not lead_name:
		return

	snapshot = _lead_snapshot(lead_name)

	# Customer's own default creation path (the "+ Add Customer" Quick
	# Entry dialog on the Customer list, since Customer.json has
	# quick_entry: 1) doesn't even offer customer_primary_contact as a
	# field - confirmed by driving it directly. So the common real
	# sequence is: create via Quick Entry (customer_type already set to a
	# real, saved value - it's mandatory there, and customer_group/
	# territory/language get Frappe's own environment defaults regardless
	# of Quick Entry) -> later open the full record and pick a primary
	# contact for the first time. At that point doc.is_new() is False, so
	# gating the pristine comparison on is_new() alone meant these four
	# fields could never be corrected, before or after save, once Quick
	# Entry had run - confirmed by reproducing this exact flow. Building
	# `pristine` whenever customer_primary_contact changed *this save*
	# instead (not just on insert) closes that gap: has_value_changed
	# already returns True for a genuine insert (no doc_before_save to
	# compare against), so this is a strict generalization of the
	# original new-doc-only behavior, not a change to it.
	primary_contact_just_set = doc.has_value_changed("customer_primary_contact")
	pristine = frappe.new_doc("Customer") if primary_contact_just_set else None
	for fieldname, value in snapshot.items():
		_apply_lead_field(doc, fieldname, value, pristine)

	# On insert, preserve the original "only if blank" rule - an explicitly
	# provided lead_name (e.g. via API, independent of
	# customer_primary_contact) is never clobbered. On an *existing*
	# Customer, also re-point lead_name when customer_primary_contact
	# itself just changed this save: otherwise, editing an already-saved
	# Customer's primary contact to a *different* Contact linked to a
	# *different* Lead correctly re-links that Contact
	# (_ensure_contact_linked_to_customer isn't lead_name-gated) but
	# silently leaves link_primary_contact's on_update side effects (Lead
	# conversion, address sharing, WhatsApp sync) operating on the stale
	# old Lead forever - the new Lead's own conversion never runs. Gated on
	# customer_primary_contact actually changing this save (not
	# unconditional) so a routine resave that doesn't touch it never
	# overwrites a lead_name set through some unrelated path (e.g. native
	# ERPNext's own Lead "Create > Customer" conversion, or a deliberate
	# manual correction).
	if not doc.lead_name or (not doc.is_new() and primary_contact_just_set):
		doc.lead_name = lead_name


def _ensure_contact_linked_to_customer(customer, contact=None):
	"""Guarantee the Customer Primary Contact is Dynamic-Linked back to
	this Customer. Thin wrapper (Phase 1) around utils.
	ensure_contact_linked_to_parent, the same logic generalized once
	Supplier needed it too - kept here, under this name, so this file's
	own existing tests/call sites are unaffected by the extraction.

	Args:
		customer: a saved Customer document (has a real, persisted name).
		contact: the already-loaded Contact document for
			customer.customer_primary_contact, if the caller already has
			one - see ensure_contact_linked_to_parent's own docstring for
			why. Loaded here if not provided.

	Returns:
		The Contact document used (whether passed in or loaded here), or
		None if this Customer has no primary contact - so callers that
		already paid for this load can reuse it too.
	"""
	return ensure_contact_linked_to_parent(customer, "customer_primary_contact", contact=contact)


def _sync_whatsapp_to_customer_contact(customer, contact=None):
	"""If the Customer's primary Contact is linked to a Lead with a WhatsApp
	number, make sure that number is on the Contact's Contact Numbers table
	too - see contact_enhancements.api.lead_lookup._sync_whatsapp_to_contact,
	which also sets that new row's own country from the Lead's.

	Only saves the Contact when a row was actually added, to avoid a
	redundant write on every Customer save once it's already in sync.

	Args:
		customer: a saved Customer document.
		contact: the already-loaded Contact document for
			customer.customer_primary_contact, if the caller already has
			one - see _ensure_contact_linked_to_customer's docstring for
			why. Loaded here if not provided.
	"""
	if not customer.customer_primary_contact or not customer.lead_name:
		return

	lead = frappe.get_doc("Lead", customer.lead_name)
	if contact is None:
		contact = frappe.get_doc("Contact", customer.customer_primary_contact)

	rows_before = len(contact.phone_nos)
	_sync_whatsapp_to_contact(contact, lead)
	if len(contact.phone_nos) > rows_before:
		contact.save(ignore_permissions=customer.flags.ignore_permissions)


def link_primary_contact(doc, method=None):
	"""Customer on_update hook: the actual fix for "picking an existing
	Contact does not link it back" - see _ensure_contact_linked_to_customer.
	Unconditional and idempotent regardless of how customer_primary_contact
	got set, so it also covers a Contact that was created long before this
	Customer existed.

	If lead_name ended up set (by sync_customer_from_primary_contact above,
	or any other path), also reuses ERPNext's own Lead-to-Customer
	conversion side effects instead of reimplementing them: marks that Lead
	"Converted" and copies its Address/Contact links onto this Customer
	(link_address_and_contact() re-links whatever's already Dynamic-Linked
	to the Lead with parenttype Contact *or* Address, so an Address shared
	with the Lead ends up shared with the Customer too, the same way the
	Contact does). Called explicitly rather than relying on Customer.
	on_update's own internal calls to these, because those are gated in a
	way that never fires when updating an *already-existing* Customer (only
	a fresh insert) - confirmed in this app's own history fixing the same
	gap for the previous iteration of this feature. Also makes sure the
	Lead's WhatsApp number (never copied anywhere by native ERPNext) ends up
	on the Contact's Contact Numbers table.

	All three of those are individually idempotent/safe to re-run, but
	skipped entirely on a routine resave where neither lead_name nor
	customer_primary_contact actually changed - re-running them on every
	unrelated edit forever would be a wasted Lead status write, a wasted
	Dynamic Link query, and two wasted full doc loads (Lead + Contact) on
	every single Customer save. has_value_changed relies on
	doc_before_save, populated by Frappe before validate/on_update on a
	real .save()/.insert() call; is_new() covers insert explicitly even
	though has_value_changed already treats "no prior doc" as changed.

	Loads the primary Contact (if any) exactly once, here, and passes it
	into both _ensure_contact_linked_to_customer and
	_sync_whatsapp_to_customer_contact - they used to each load their own
	copy of the same Contact independently within this same hook
	invocation.

	Ends by conditionally re-syncing doc.modified from the database - see
	_resync_modified_after_side_effect_saves for why that's necessary, and
	for why it's only called when customer_primary_address might actually
	have just been newly Dynamic-Linked this save (not on every
	lead-link-changed save).

	Args:
		doc: the Customer document that was just saved.
		method: unused, present for the doc_events hook signature.
	"""
	contact = _ensure_contact_linked_to_customer(doc)
	# Same guarantee as the Contact one above, now for the Address side too
	# - "a Contact/Address a doctype's own primary field points at must
	# always be in that Contact/Address's own reference table" applies
	# regardless of how customer_primary_address got set. Unconditional
	# here (not gated on lead_name) since doc.link_address_and_contact()
	# just below only ever runs inside the lead_name branch - a Customer
	# whose address was set some other way (e.g. picked directly) still
	# needs this.
	ensure_doc_linked_to_parent(doc, "customer_primary_address", "Address")

	if doc.lead_name:
		lead_link_changed_this_save = (
			doc.is_new()
			or doc.has_value_changed("lead_name")
			or doc.has_value_changed("customer_primary_contact")
		)
		if lead_link_changed_this_save:
			doc.update_lead_status()
			doc.link_address_and_contact()
			_sync_whatsapp_to_customer_contact(doc, contact=contact)

			if doc.customer_primary_address and (
				doc.is_new() or doc.has_value_changed("customer_primary_address")
			):
				_resync_modified_after_side_effect_saves(doc)


def _resync_modified_after_side_effect_saves(doc):
	"""Reload doc.modified from the database, to undo a stale-timestamp bug
	this hook would otherwise cause on every Customer whose
	customer_primary_address gets backfilled from a Lead-linked Address
	that also needs a brand-new Dynamic Link to this Customer (the common
	case for a fresh contact-picker-created Customer).

	Only called (see link_primary_contact) when customer_primary_address
	is set AND either this is a new doc or that field just changed this
	save - free (no extra query), since has_value_changed/is_new() read
	only in-memory state, and it's precisely the condition under which
	link_address_and_contact() might actually Dynamic-Link that Address
	for the first time (calling address.save(), which is what triggers
	the race below) rather than finding it already linked and doing
	nothing. On a routine resave where customer_primary_address is
	unchanged, the Address was already linked in a prior save, so no new
	address.save() happens and this function doesn't need to run at all -
	skipping it there removes an unconditional extra query this hook used
	to make on every single lead-linked Customer save, not just the one
	save where it's ever actually needed.

	Confirmed root cause (reproduced with a traced frappe.db.set_value):
	link_address_and_contact() (called just above, directly by this hook
	and again natively by Customer.on_update itself) Dynamic-Links that
	Address to this Customer by calling address.save() - and ERPNext's own
	erpnext.accounts.custom.address.ERPNextAddress.on_update runs as part
	of *that* save, unconditionally looking up every Customer whose
	customer_primary_address already equals this Address (true here, for
	the first time, only because this app now backfills that field) and
	calling frappe.db.set_value("Customer", ..., "primary_address", ...,
	update_modified=True - db_set's own default) directly against the
	database - bumping this exact Customer row's modified timestamp
	without this doc object (or anything in its own save cycle) ever
	knowing. The result: the doc handed back to whatever called
	.insert()/.save() carries a modified value that's already stale the
	instant control returns - confirmed by reproducing the very next
	.save() on that same object failing with TimestampMismatchError, not
	just a theoretical race.

	Args:
		doc: the Customer document that was just saved.
	"""
	doc.modified = frappe.db.get_value(doc.doctype, doc.name, "modified")
