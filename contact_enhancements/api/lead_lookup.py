# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

import frappe

from contact_enhancements.contact_hooks import try_to_e164
from contact_enhancements.utils import dynamic_link_lookup

# create_minimal_contact / _contact_search_query / search_contact_by_phone /
# _phone_channels / search_contacts_with_details used to live here - moved
# to api/contact_lookup.py in Phase 1 once Supplier needed the same
# genuinely-generic search-as-you-type contact picker Customer's own dialog
# already used (confirmed none of the five touch Lead/Customer in their own
# implementation). Re-exported here unchanged so any caller still using
# this module path keeps resolving.
from contact_enhancements.api.contact_lookup import (  # noqa: F401
	_contact_search_query,
	_phone_channels,
	create_minimal_contact,
	search_contact_by_phone,
	search_contacts_with_details,
)


def _dynamic_link_lookup(filters, return_field):
	"""Thin wrapper (Phase 1) around utils.dynamic_link_lookup, the same
	query promoted to a genuinely public, shared app surface once
	Supplier needed it too. Kept here, under this name, so every existing
	call site in this file is unaffected by the promotion.

	Args:
		filters: dict of Dynamic Link filter conditions.
		return_field: which Dynamic Link column to return.

	Returns:
		The requested field's value, or None if no matching row exists.
	"""
	return dynamic_link_lookup(filters, return_field)


def _get_contact_lead(contact_name):
	"""Return the name of the Lead a Contact is Dynamic-Linked to, if any.

	Inverse of ERPNext's own auto-Contact-creation on Lead insert
	(CRM Settings.auto_creation_of_contact, on by default -
	erpnext.crm.doctype.lead.lead.Lead.before_insert/after_insert/
	link_to_contact appends a Dynamic Link row to the Contact it creates).

	Args:
		contact_name: name of the Contact to look up.

	Returns:
		The linked Lead's name, or None if this Contact has no Lead link.
	"""
	return _dynamic_link_lookup(
		{"parenttype": "Contact", "parent": contact_name, "link_doctype": "Lead"},
		"link_name",
	)


def _get_lead_address(lead_name):
	"""Return the name of an Address Dynamic-Linked to a Lead, if any.

	Same relationship _get_contact_lead reads in reverse, and the same one
	erpnext.selling.doctype.customer.customer.Customer.link_address_and_contact()
	re-links onto a Customer (parenttype "Address" is one of the two it
	queries for) - but that only creates the Dynamic Link, it never sets
	Customer.customer_primary_address itself, the same gap
	link_address_and_contact() left for customer_primary_contact.

	Args:
		lead_name: name of the Lead to look up.

	Returns:
		The linked Address's name, or None if this Lead has no Address
		linked to it (the common case - Lead never auto-creates one, only
		ever gets one if a user manually adds it via the Lead's own
		Address & Contacts widget).
	"""
	return _dynamic_link_lookup(
		{"parenttype": "Address", "link_doctype": "Lead", "link_name": lead_name},
		"parent",
	)


LEAD_SNAPSHOT_FIELDS = (
	"company_name",
	"lead_name",
	"territory",
	"salutation",
	"gender",
	"market_segment",
	"industry",
	"website",
	"language",
	"image",
)


def _lead_snapshot(lead_name):
	"""Build the Customer-prefill dict for a Lead, by name.

	Fetches only the handful of scalar fields actually read below via
	frappe.db.get_value(..., as_dict=True), not a full frappe.get_doc()
	load - nothing here needs the Lead's child tables or any of its own
	methods, just to read some field values, so the full document load
	both call sites (sync_customer_from_primary_contact and
	get_lead_snapshot_for_contact) used to do before calling this was a
	pure overhead - see contact_enhancements/CLAUDE.md's performance
	checklist.

	Matches what ERPNext's own native "Create > Customer" button on Lead
	(erpnext.crm.doctype.lead.lead._make_customer) actually transfers to the
	Customer record: customer_type/customer_name computed the same way its
	set_missing_values does, plus every other field get_mapped_doc's default
	same-fieldname copying picks up between Lead and Customer (confirmed by
	reading both doctypes' full field lists) - salutation, gender,
	market_segment, industry, website, language, image - plus customer_group
	and territory, which the native button doesn't map at all (its
	field_map's only other two entries, contact_no->phone_1 and
	fax->fax_1, are dead code - neither source nor target field exists).

	Also includes customer_primary_address (not something the native button
	transfers either - see _get_lead_address) so a Customer created through
	this app's mandatory contact-picker dialog only ever needs a manual
	Address if the Lead genuinely doesn't have one linked yet.

	Args:
		lead_name: name of the Lead.

	Returns:
		Dict of Customer field values to prefill, guarded by the caller to
		only apply where currently blank.
	"""
	lead = frappe.db.get_value("Lead", lead_name, LEAD_SNAPSHOT_FIELDS, as_dict=True)
	# get_value returns None for a Lead that no longer exists - possible
	# whenever the Dynamic Link that named it outlives the Lead itself
	# (deleted between the link lookup and this read, or a stale link left
	# behind). Every caller already treats an empty snapshot as "nothing to
	# prefill", so return that rather than raising AttributeError.
	if not lead:
		return {}

	if lead.company_name:
		customer_type = "Company"
		customer_name = lead.company_name
	else:
		customer_type = "Individual"
		customer_name = lead.lead_name

	return {
		"customer_type": customer_type,
		"customer_name": customer_name,
		"customer_group": frappe.db.get_default("Customer Group"),
		"territory": lead.territory,
		"salutation": lead.salutation,
		"gender": lead.gender,
		"market_segment": lead.market_segment,
		"industry": lead.industry,
		"website": lead.website,
		"language": lead.language,
		"image": lead.image,
		"customer_primary_address": _get_lead_address(lead_name),
	}


def _sync_whatsapp_to_contact(contact, lead):
	"""Add the Lead's WhatsApp number to the Contact's Contact Numbers table,
	checked as WhatsApp-capable, if it isn't already there.

	Native ERPNext never copies Lead.whatsapp_no anywhere - Lead.create_contact()
	only copies phone/mobile_no to the auto-created Contact. This closes that
	gap using this app's own Feature 1 field (Contact Phone.custom_whatsapp)
	instead of leaving the Lead's WhatsApp number stranded. Mirrors the exact
	append pattern create_contact() itself already uses for phone/mobile_no.

	The new row's own mandatory country (Contact Phone.country -
	setup/custom_fields.py) is set from lead.country when the Lead has one -
	never fabricated, and only if not provided so the field's own "Egypt"
	DocField default applies otherwise, same as create_minimal_contact.

	The "already there" check compares both sides via try_to_e164, not a
	bare ==: contact.phone_nos rows loaded from the database are already
	E.164 (contact_hooks.normalize_and_validate_contact_phone), but
	lead.whatsapp_no is a flat Lead field this app never normalizes - a
	literal string comparison would silently never match the same real
	number and append a duplicate row every time this runs. Lead.country
	(unlike Contact Phone.country) has no DocField default of its own, so
	a blank one falls back to "Egypt" here too, same as everywhere else in
	this app a country is needed to interpret a bare local-format number.

	Args:
		contact: a loaded Contact document, about to be saved by the caller.
		lead: a loaded Lead document.

	Returns:
		None. No-ops if the Lead has no whatsapp_no, or the Contact already
		has a phone_nos row with that number.
	"""
	if not lead.whatsapp_no:
		return

	target = try_to_e164(lead.whatsapp_no, lead.country or "Egypt")
	already_present = any(
		try_to_e164(row.phone, row.country or "Egypt") == target for row in contact.phone_nos
	)
	if already_present:
		return

	row = {"phone": lead.whatsapp_no, "custom_whatsapp": 1}
	if lead.country:
		row["country"] = lead.country
	contact.append("phone_nos", row)


@frappe.whitelist()
def get_lead_snapshot_for_contact(contact_name):
	"""Live-prefill data for a Customer form: if the given Contact is
	Dynamic-Linked to a Lead, return that Lead's snapshot so
	contact_enhancements/public/js/customer.js can fill in the Customer's
	fields the moment that Contact is picked as its Customer Primary
	Contact - before the Customer is even saved (needed because
	customer_name is a mandatory field checked client-side before save is
	attempted, so this can't wait for a server-side save hook to run).

	Also includes lead_name itself (the Lead's own .name, not one of its
	fields, so deliberately not part of _lead_snapshot's own dict - that
	function's return value doubles as the field list
	contact_enhancements.customer_hooks._apply_lead_field loops over
	server-side, which already has its own, more careful lead_name
	handling lower down in sync_customer_from_primary_contact). Without
	this, every other field visibly live-prefilled on pick, but "From
	Lead" stayed blank until save - confirmed by reproducing that exact
	report in the browser: correct once saved (the server-side hook always
	sets it), just missing from this one-shot live preview.

	Args:
		contact_name: name of the Contact just picked.

	Returns:
		See _lead_snapshot, plus a "lead_name" key, or None if this Contact
		has no linked Lead.
	"""
	lead_name = _get_contact_lead(contact_name)
	if not lead_name:
		return None
	snapshot = _lead_snapshot(lead_name)
	snapshot["lead_name"] = lead_name
	return snapshot
