"""Tests for contact_enhancements.api.lead_lookup.

Covers the Customer Primary Contact search override (search_contact_by_phone),
the "Create New Contact" action behind the mandatory contact-picker dialog
(create_minimal_contact), and the Lead-snapshot lookup used to live-prefill a
Customer form (get_lead_snapshot_for_contact / _get_contact_lead). The
doc_events hooks that consume these on the Customer side are covered
separately in test_customer_hooks.py.
"""

import random
import string

import frappe
from frappe.tests.utils import FrappeTestCase

from contact_enhancements.api.lead_lookup import (
	_dynamic_link_lookup,
	_sync_whatsapp_to_contact,
	get_lead_snapshot_for_contact,
)


def _random_name(length=10):
	"""A random ASCII-letters-only string - contact_enhancements.contact_hooks
	.normalize_contact_first_name now normalizes every Contact.first_name,
	and frappe.generate_hash's hex output routinely contains digits, which
	those rules would strip out. Pure ASCII letters trivially satisfy all 5
	rules unchanged (no digit, no Arabic character at all, single word so
	no double space) - avoids the test's expected fixture value silently
	diverging from what actually got saved.
	"""
	return "".join(random.choices(string.ascii_lowercase, k=length))


def _random_mobile_no():
	"""A syntactically valid Egyptian mobile number that's different every
	call. Phone numbers must now be unique across Contacts (Phase 0d,
	contact_hooks.enforce_unique_mobile_number) - a fixed literal default
	risks colliding with another test's own fixture, or with real
	pre-existing site data, and either now makes an unrelated test raise
	instead of the specific thing it meant to test. Same reasoning as
	_random_name above, applied to phone numbers.
	"""
	return "010" + "".join(random.choices(string.digits, k=8))


def make_lead(**kwargs):
	lead = frappe.get_doc(
		{
			"doctype": "Lead",
			"first_name": "Jane",
			"last_name": "Doe",
			"mobile_no": _random_mobile_no(),
			**kwargs,
		}
	)
	lead.insert(ignore_permissions=True)
	return lead


def make_contact(**kwargs):
	phone_nos = kwargs.pop("phone_nos", None)
	contact = frappe.get_doc(
		{
			"doctype": "Contact",
			"first_name": _random_name(),
			**kwargs,
		}
	)
	if phone_nos:
		for phone in phone_nos:
			contact.append("phone_nos", {"phone": phone})
	contact.insert(ignore_permissions=True)
	return contact


def make_lead_address(lead_name, **kwargs):
	"""An Address Dynamic-Linked to a Lead - the same relationship a user
	adds manually via the Lead's own Address & Contacts widget (Lead never
	creates one itself, unlike its auto-created Contact).
	"""
	address = frappe.get_doc(
		{
			"doctype": "Address",
			"address_title": frappe.generate_hash(length=10),
			"address_type": "Office",
			"address_line1": "1 Test Street",
			"city": "Cairo",
			"country": "Egypt",
			"links": [{"link_doctype": "Lead", "link_name": lead_name}],
			**kwargs,
		}
	)
	address.insert(ignore_permissions=True)
	return address


class TestDynamicLinkLookup(FrappeTestCase):
	"""_dynamic_link_lookup is the single query _get_contact_lead,
	_get_lead_address, and customer_hooks._find_linked_docs_from_source
	all route through now, instead of each hand-writing its own
	frappe.db.get_value("Dynamic Link", ...) call.
	"""

	def test_finds_parent_by_link_doctype_and_link_name(self):
		lead = make_lead(company_name="Acme Corp")
		address = make_lead_address(lead.name)

		found = _dynamic_link_lookup(
			{"parenttype": "Address", "link_doctype": "Lead", "link_name": lead.name},
			"parent",
		)

		self.assertEqual(found, address.name)

	def test_finds_link_name_by_parent(self):
		lead = make_lead(company_name="Acme Corp")
		contact_name = frappe.db.get_value(
			"Dynamic Link",
			{"link_doctype": "Lead", "link_name": lead.name, "parenttype": "Contact"},
			"parent",
		)

		found = _dynamic_link_lookup(
			{"parenttype": "Contact", "parent": contact_name, "link_doctype": "Lead"},
			"link_name",
		)

		self.assertEqual(found, lead.name)

	def test_returns_none_when_no_match(self):
		found = _dynamic_link_lookup(
			{"parenttype": "Address", "link_doctype": "Lead", "link_name": "no-such-lead"},
			"parent",
		)
		self.assertIsNone(found)


class TestGetLeadSnapshotForContact(FrappeTestCase):
	def test_returns_none_for_contact_without_lead(self):
		contact = make_contact()
		self.assertIsNone(get_lead_snapshot_for_contact(contact.name))

	def test_returns_snapshot_for_lead_linked_contact(self):
		lead = make_lead(company_name="Acme Corp")
		contact_name = frappe.db.get_value(
			"Dynamic Link",
			{"link_doctype": "Lead", "link_name": lead.name, "parenttype": "Contact"},
			"parent",
		)
		snapshot = get_lead_snapshot_for_contact(contact_name)
		self.assertEqual(snapshot["customer_type"], "Company")
		self.assertEqual(snapshot["customer_name"], "Acme Corp")

	def test_snapshot_includes_lead_name_itself(self):
		# Regression: every other field visibly live-prefilled the moment a
		# Contact was picked in the Customer form, but "From Lead" stayed
		# blank until save (server-side sync_customer_from_primary_contact
		# always sets it, just too late for the live preview) - confirmed
		# by reproducing that exact report in the browser. lead_name is
		# deliberately not part of _lead_snapshot's own dict (that one
		# doubles as the server-side field-copy list, which already has its
		# own, more careful lead_name handling), so this is the one place
		# it gets added, purely for the client-side live preview.
		lead = make_lead(company_name="Acme Corp")
		contact_name = frappe.db.get_value(
			"Dynamic Link",
			{"link_doctype": "Lead", "link_name": lead.name, "parenttype": "Contact"},
			"parent",
		)
		snapshot = get_lead_snapshot_for_contact(contact_name)
		self.assertEqual(snapshot["lead_name"], lead.name)

	def test_individual_lead_maps_to_individual_customer(self):
		lead = make_lead(first_name="Jane", last_name="Doe", company_name=None)
		contact_name = frappe.db.get_value(
			"Dynamic Link",
			{"link_doctype": "Lead", "link_name": lead.name, "parenttype": "Contact"},
			"parent",
		)
		snapshot = get_lead_snapshot_for_contact(contact_name)
		self.assertEqual(snapshot["customer_type"], "Individual")
		self.assertEqual(snapshot["customer_name"], lead.lead_name)

	def test_carries_every_field_native_create_customer_would(self):
		# Matches erpnext.crm.doctype.lead.lead._make_customer's own field
		# set: get_mapped_doc's default same-fieldname copy picks up
		# salutation/gender/market_segment/industry/website/language/image
		# in addition to the explicitly computed customer_type/customer_name.
		any_salutation = frappe.db.get_value("Salutation", {}, "name")
		any_gender = frappe.db.get_value("Gender", {}, "name")
		any_industry = frappe.db.get_value("Industry Type", {}, "name")
		any_language = frappe.db.get_value("Language", {}, "name")
		any_market_segment = frappe.db.get_value("Market Segment", {}, "name")

		lead = make_lead(
			company_name="Acme Corp",
			salutation=any_salutation,
			gender=any_gender,
			industry=any_industry,
			language=any_language,
			market_segment=any_market_segment,
			website="https://acme.example.com",
			image="/files/acme-logo.png",
		)
		contact_name = frappe.db.get_value(
			"Dynamic Link",
			{"link_doctype": "Lead", "link_name": lead.name, "parenttype": "Contact"},
			"parent",
		)

		snapshot = get_lead_snapshot_for_contact(contact_name)

		self.assertEqual(snapshot["salutation"], any_salutation)
		self.assertEqual(snapshot["gender"], any_gender)
		self.assertEqual(snapshot["industry"], any_industry)
		self.assertEqual(snapshot["language"], any_language)
		self.assertEqual(snapshot["market_segment"], any_market_segment)
		self.assertEqual(snapshot["website"], "https://acme.example.com")
		self.assertEqual(snapshot["image"], "/files/acme-logo.png")

	def test_snapshot_includes_address_linked_to_lead(self):
		lead = make_lead(company_name="Acme Corp")
		contact_name = frappe.db.get_value(
			"Dynamic Link",
			{"link_doctype": "Lead", "link_name": lead.name, "parenttype": "Contact"},
			"parent",
		)
		address = make_lead_address(lead.name)

		snapshot = get_lead_snapshot_for_contact(contact_name)

		self.assertEqual(snapshot["customer_primary_address"], address.name)

	def test_snapshot_address_is_blank_when_lead_has_none(self):
		# The common case - Lead never auto-creates an Address the way it
		# does a Contact, so most Leads have none. Documents the boundary:
		# nothing here synthesizes a placeholder Address from the Lead's
		# flat city/state/country fields.
		lead = make_lead(company_name="Acme Corp")
		contact_name = frappe.db.get_value(
			"Dynamic Link",
			{"link_doctype": "Lead", "link_name": lead.name, "parenttype": "Contact"},
			"parent",
		)

		snapshot = get_lead_snapshot_for_contact(contact_name)

		self.assertIsNone(snapshot["customer_primary_address"])

	def test_does_not_load_the_full_lead_document(self):
		# The perf fix: _lead_snapshot fetches only the scalar fields it
		# needs via frappe.db.get_value(..., as_dict=True) - it must not
		# trigger a full frappe.get_doc("Lead", ...) load, which would
		# also pull every child table for no reason.
		lead = make_lead(company_name="Acme Corp")
		contact_name = frappe.db.get_value(
			"Dynamic Link",
			{"link_doctype": "Lead", "link_name": lead.name, "parenttype": "Contact"},
			"parent",
		)

		load_calls = []
		original_get_doc = frappe.get_doc

		def counting_get_doc(*args, **kwargs):
			if args[:1] == ("Lead",):
				load_calls.append(args)
			return original_get_doc(*args, **kwargs)

		frappe.get_doc = counting_get_doc
		try:
			get_lead_snapshot_for_contact(contact_name)
		finally:
			frappe.get_doc = original_get_doc

		self.assertEqual(load_calls, [])


class TestSyncWhatsappToContact(FrappeTestCase):
	def test_adds_whatsapp_number_as_new_row(self):
		lead = make_lead(whatsapp_no="01055566677")
		contact = make_contact()

		_sync_whatsapp_to_contact(contact, lead)

		matches = [row for row in contact.phone_nos if row.phone == "01055566677"]
		self.assertEqual(len(matches), 1)
		self.assertEqual(matches[0].custom_whatsapp, 1)

	def test_does_not_duplicate_when_number_already_present(self):
		# The real point of this test post-E.164 conversion: contact's row
		# is already stored as "+201055566677" (normalized by .insert()),
		# while lead.whatsapp_no stays the raw local-form "01055566677" -
		# proving the duplicate check still recognizes them as the same
		# number instead of comparing formats literally (see this
		# function's own docstring for the bug this replaced).
		lead = make_lead(whatsapp_no="01055566677")
		contact = make_contact(phone_nos=["01055566677"])

		_sync_whatsapp_to_contact(contact, lead)

		self.assertEqual(len(contact.phone_nos), 1)

	def test_noop_without_whatsapp_no(self):
		lead = make_lead(whatsapp_no=None)
		contact = make_contact()

		_sync_whatsapp_to_contact(contact, lead)

		self.assertEqual(len(contact.phone_nos), 0)

	def test_sets_new_row_country_from_the_lead(self):
		# Contact Phone.country is mandatory (setup/custom_fields.py) -
		# native ERPNext never sets it on a row it creates, so this app's
		# own append here has to, or a resave of this exact row would fail
		# with nothing more specific to go on than the DocField default.
		lead = make_lead(whatsapp_no="01055566677", country="United Kingdom")
		contact = make_contact()

		_sync_whatsapp_to_contact(contact, lead)

		matches = [row for row in contact.phone_nos if row.phone == "01055566677"]
		self.assertEqual(matches[0].country, "United Kingdom")

	def test_new_row_country_falls_back_to_the_default_when_lead_has_none(self):
		# contact.append() applies a child row's own DocField default
		# ("Egypt") immediately, unlike a top-level frappe.new_doc() (which
		# only applies its defaults at insert time) - confirmed empirically.
		# Never fabricated from the Lead here either way - just not
		# explicitly set, so the field's own default takes over.
		lead = make_lead(whatsapp_no="01055566677")
		contact = make_contact()

		_sync_whatsapp_to_contact(contact, lead)

		matches = [row for row in contact.phone_nos if row.phone == "01055566677"]
		self.assertEqual(matches[0].country, "Egypt")


