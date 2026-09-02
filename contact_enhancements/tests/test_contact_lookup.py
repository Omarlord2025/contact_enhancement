"""Tests for contact_enhancements.api.contact_lookup - the genuinely
generic Contact search/lookup/creation surface (moved out of api/
lead_lookup.py in Phase 1 once Supplier needed the same search-as-you-type
contact picker Customer's own dialog already used)."""

import frappe
from frappe.tests.utils import FrappeTestCase

from contact_enhancements.api import contact_lookup as contact_lookup_module
from contact_enhancements.api.contact_lookup import (
	create_minimal_contact,
	escape_like_wildcards,
	get_address_from_contact_links,
	get_addresses_for_contact,
	search_contact_by_phone,
	search_contacts_with_details,
)
from contact_enhancements.tests.test_customer_hooks import make_customer
from contact_enhancements.tests.test_lead_lookup import make_contact
from contact_enhancements.tests.test_supplier_hooks import make_address


class TestSearchContactByPhone(FrappeTestCase):
	def _search(self, txt):
		return search_contact_by_phone("Contact", txt, "name", 0, 20, {})

	def test_matches_by_full_name(self):
		contact = make_contact(first_name="Zephyrine", last_name="Quibble")
		names = [r[0] for r in self._search("Zephyrine")]
		self.assertIn(contact.name, names)

	def test_matches_by_email(self):
		contact = make_contact(email_ids=[{"email_id": "zq@example.com", "is_primary": 1}])
		names = [r[0] for r in self._search("zq@example.com")]
		self.assertIn(contact.name, names)

	def test_matches_by_non_primary_phone_number(self):
		# The number is on a second, non-primary row - proving the search
		# reaches the whole Contact Numbers table, not just the primary
		# number synced onto Contact.phone/mobile_no.
		contact = make_contact(phone_nos=["01123456789", "01099998888"])
		names = [r[0] for r in self._search("9998888")]
		self.assertIn(contact.name, names)

	def test_matches_a_local_form_query_against_an_e164_stored_egypt_number(self):
		# phone is always stored E.164 ("+201099733401") - the search text
		# below is the local form staff actually type. Egypt's country
		# code happens to make a bare LIKE against `phone` alone
		# coincidentally still work for some inputs - this uses a query
		# shape that wouldn't survive that accident, so it only passes if
		# custom_phone_national is genuinely being matched.
		contact = make_contact(phone_nos=["01099733401"])
		names = [r[0] for r in self._search("01099733401")]
		self.assertIn(contact.name, names)

	def test_matches_a_local_form_query_against_an_e164_stored_uk_number(self):
		# The real proof the fix generalizes: UK's country code ("44")
		# does not end in the digit the local form starts with ("0"), so
		# a bare LIKE against the E.164-stored `phone` column can never
		# coincidentally match a local-form query the way Egypt's can -
		# this only passes via custom_phone_national.
		contact = frappe.new_doc("Contact")
		contact.first_name = "UKSearchTest"
		contact.append("phone_nos", {"phone": "07400123456", "country": "United Kingdom"})
		contact.insert(ignore_permissions=True)

		names = [r[0] for r in self._search("07400123456")]
		self.assertIn(contact.name, names)

	def test_no_match_returns_empty(self):
		self.assertEqual(self._search("no-such-contact-xyz"), ())

	def test_matches_a_full_e164_query(self):
		# Numbers are displayed in E.164 everywhere now, so pasting one
		# straight back into the search box is a normal thing to do. This
		# is the case that would regress if the `phone` predicate were
		# simply dropped in favor of custom_phone_national - national holds
		# bare local digits ("01099733402") and can never match a "+"
		# prefixed query.
		contact = make_contact(phone_nos=["01099733402"])
		names = [r[0] for r in self._search("+201099733402")]
		self.assertIn(contact.name, names)

	def test_matches_a_partial_e164_prefix(self):
		contact = make_contact(phone_nos=["01099733403"])
		names = [r[0] for r in self._search("+2010997334")]
		self.assertIn(contact.name, names)

	def test_matches_an_e164_query_typed_with_formatting_noise(self):
		# Formatting noise is stripped before the "+…digits" test, so a
		# pasted, spaced-out number still takes the anchored-prefix path.
		contact = make_contact(phone_nos=["01099733404"])
		names = [r[0] for r in self._search("+20 109 973 3404")]
		self.assertIn(contact.name, names)

	def test_a_percent_is_searched_literally_not_as_a_wildcard(self):
		# Unescaped, "%" reaches LIKE '%%%' and matches every Contact in
		# the table - at production volume, a full scan of the whole
		# Contact/Contact Phone join that any user can trigger by typing
		# one character.
		make_contact(first_name="Wildcard Probe Person")
		self.assertEqual(self._search("%"), ())

	def test_an_underscore_is_searched_literally_not_as_a_wildcard(self):
		# "_" is LIKE's single-character wildcard, so "z_q" unescaped would
		# match "zaq", "zbq", ... Only a literal "z_q" should match.
		contact = make_contact(first_name="Zaq Underscore Probe")
		names = [r[0] for r in self._search("Z_q")]
		self.assertNotIn(contact.name, names)


class TestEscapeLikeWildcards(FrappeTestCase):
	def test_escapes_percent(self):
		self.assertEqual(escape_like_wildcards("50%"), "50\\%")

	def test_escapes_underscore(self):
		self.assertEqual(escape_like_wildcards("a_b"), "a\\_b")

	def test_escapes_the_escape_character_first(self):
		# A backslash must be doubled before the wildcards are escaped, or
		# "\%" would become "\\%" - an escaped backslash followed by a
		# still-live wildcard.
		self.assertEqual(escape_like_wildcards("\\%"), "\\\\\\%")

	def test_leaves_ordinary_text_untouched(self):
		self.assertEqual(escape_like_wildcards("Omar Ahmed"), "Omar Ahmed")

	def test_passes_falsy_values_through_unchanged(self):
		self.assertIsNone(escape_like_wildcards(None))
		self.assertEqual(escape_like_wildcards(""), "")


class TestCreateMinimalContact(FrappeTestCase):
	"""Per-country phone format/type validation itself is contact_hooks.
	normalize_and_validate_contact_phone's own responsibility now (covered
	exhaustively in tests/test_contact_hooks.py) - these tests only cover
	what create_minimal_contact itself is responsible for: building the
	Contact and its phone_nos row correctly, and passing country through
	to both that row's own mandatory country field (Contact Phone.country)
	and (via the Contact validate hook that then runs) the phone validation.
	"""

	def test_creates_contact_with_phone_as_primary_mobile(self):
		name = create_minimal_contact(first_name="Nadia Ahmed Sabry", phone="01033344411", country="Egypt")

		contact = frappe.get_doc("Contact", name)
		self.assertEqual(contact.first_name, "Nadia Ahmed Sabry")
		matches = [row for row in contact.phone_nos if row.phone == "+201033344411"]
		self.assertEqual(len(matches), 1)
		self.assertEqual(matches[0].is_primary_mobile_no, 1)
		self.assertEqual(matches[0].country, "Egypt")

	def test_normalizes_phone_via_the_contact_validate_hook(self):
		# +20 country-code form, missing the leading 0 - proves the phone
		# actually gets normalized before saving, via the ordinary Contact
		# validate hook (not something create_minimal_contact does itself
		# anymore).
		name = create_minimal_contact(first_name="Nadia Ahmed Sabry", phone="+201033344422", country="Egypt")

		contact = frappe.get_doc("Contact", name)
		matches = [row for row in contact.phone_nos if row.phone == "+201033344422"]
		self.assertEqual(len(matches), 1)

	def test_rejects_invalid_egypt_phone(self):
		self.assertRaises(
			frappe.ValidationError,
			create_minimal_contact,
			first_name="Nadia Ahmed Sabry",
			phone="01312345678",  # unissued Egyptian prefix
			country="Egypt",
		)

	def test_validates_phone_for_other_countries_too(self):
		# Not Egypt-specific: a genuine UK mobile number, checked against
		# the UK's own numbering plan, not Egypt's.
		name = create_minimal_contact(
			first_name="Nadia Ahmed Sabry", phone="+447400123456", country="United Kingdom"
		)

		contact = frappe.get_doc("Contact", name)
		matches = [row for row in contact.phone_nos if row.phone == "+447400123456"]
		self.assertEqual(len(matches), 1)
		self.assertEqual(matches[0].country, "United Kingdom")

	def test_row_country_defaults_to_egypt_when_not_provided(self):
		# Contact Phone.country's own DocField default (setup/custom_fields
		# .py) - create_minimal_contact only sets it when explicitly given
		# one.
		name = create_minimal_contact(first_name="Nadia Ahmed Sabry", phone="01033344433")

		contact = frappe.get_doc("Contact", name)
		self.assertEqual(contact.phone_nos[0].country, "Egypt")

	def test_rejects_a_single_word_name(self):
		self.assertRaises(
			frappe.ValidationError,
			create_minimal_contact,
			first_name="Nadia",
			phone="01033344455",
			country="Egypt",
		)

	def test_rejects_a_two_word_name(self):
		self.assertRaises(
			frappe.ValidationError,
			create_minimal_contact,
			first_name="Nadia Ahmed",
			phone="01033344466",
			country="Egypt",
		)

	def test_accepts_a_three_word_name(self):
		name = create_minimal_contact(first_name="Nadia Ahmed Sabry", phone="01033344477", country="Egypt")
		self.assertTrue(frappe.db.exists("Contact", name))


class TestSearchContactsWithDetails(FrappeTestCase):
	"""search_contacts_with_details backs the shared contact-picker
	dialog's own rich rendering (contact_enhancements/public/js/
	contact_picker_dialog.js) - a separate function from
	search_contact_by_phone, which still has to keep its original
	flat-tuple shape unchanged for the native Link field dropdown (see
	TestSearchContactByPhone above, left untouched).
	"""

	def _search(self, txt):
		return search_contacts_with_details(txt)

	def test_matches_by_full_name(self):
		contact = make_contact(first_name="Zephyrine", last_name="Quibble")
		names = [m["name"] for m in self._search("Zephyrine")]
		self.assertIn(contact.name, names)

	def test_matches_by_email(self):
		contact = make_contact(email_ids=[{"email_id": "zq2@example.com", "is_primary": 1}])
		names = [m["name"] for m in self._search("zq2@example.com")]
		self.assertIn(contact.name, names)

	def test_matches_by_non_primary_phone_number(self):
		contact = make_contact(phone_nos=["01123456780", "01099998811"])
		names = [m["name"] for m in self._search("9998811")]
		self.assertIn(contact.name, names)

	def test_matches_a_local_form_query_against_an_e164_stored_uk_number(self):
		# Same generalization proof as TestSearchContactByPhone's own
		# version - UK's country code can't coincidentally overlap with a
		# local-form query the way Egypt's can.
		contact = frappe.new_doc("Contact")
		contact.first_name = "UKDialogSearchTest"
		contact.append("phone_nos", {"phone": "07400123457", "country": "United Kingdom"})
		contact.insert(ignore_permissions=True)

		names = [m["name"] for m in self._search("07400123457")]
		self.assertIn(contact.name, names)

	def test_no_match_returns_empty_list(self):
		self.assertEqual(self._search("no-such-contact-xyz"), [])

	def test_includes_company_name_and_designation(self):
		contact = make_contact(designation="CTO", company_name="Acme Testing Co Two")

		match = next(m for m in self._search(contact.first_name) if m["name"] == contact.name)
		self.assertEqual(match["company_name"], "Acme Testing Co Two")
		self.assertEqual(match["designation"], "CTO")

	def test_includes_every_phone_tagged_with_its_channels(self):
		# Reuses this app's own Feature 1 fields (custom_whatsapp/
		# custom_telegram/custom_landline on Contact Phone) so the picker
		# can show e.g. "01011112222 (WhatsApp)" - proves every phone row
		# comes back, not just the one that matched the search text.
		contact = make_contact(phone_nos=["01011112222", "01033334444"])
		contact.phone_nos[0].custom_whatsapp = 1
		contact.phone_nos[1].custom_telegram = 1
		contact.save(ignore_permissions=True)

		match = next(m for m in self._search(contact.first_name) if m["name"] == contact.name)
		phones_by_number = {p["phone"]: p["channels"] for p in match["phones"]}
		self.assertEqual(set(phones_by_number), {"+201011112222", "+201033334444"})
		self.assertIn("WhatsApp", phones_by_number["+201011112222"])
		self.assertIn("Telegram", phones_by_number["+201033334444"])


class TestGetAddressFromContactLinks(FrappeTestCase):
	"""Thin whitelisted wrapper around utils.resolve_address_from_contact_links
	- the client-facing entry point every doctype's own primary-contact
	field handler calls to sync an address before Save, not just via a
	server-side validate() hook (see contact_enhancements/CLAUDE.md's own
	gotcha on why the client-side call is needed too)."""

	def test_resolves_from_a_linked_customer(self):
		contact = make_contact()
		address = make_address()
		make_customer(customer_primary_contact=contact.name, customer_primary_address=address.name)

		self.assertEqual(get_address_from_contact_links(contact.name), address.name)

	def test_returns_none_when_nothing_resolves(self):
		contact = make_contact()
		self.assertIsNone(get_address_from_contact_links(contact.name))

	def test_respects_exclude_params(self):
		from contact_enhancements.tests.test_supplier_hooks import make_supplier

		contact = make_contact()
		address = make_address()
		supplier = make_supplier(supplier_primary_contact=contact.name, supplier_primary_address=address.name)

		result = get_address_from_contact_links(
			contact.name, exclude_doctype="Supplier", exclude_name=supplier.name
		)
		self.assertIsNone(result)


class TestGetAddressesForContact(FrappeTestCase):
	"""Whitelisted wrapper around utils.get_all_addresses_for_contact,
	backing Contact's own "Linked Addresses" table (public/js/contact.js)."""

	def test_returns_empty_list_with_no_links(self):
		contact = make_contact()
		self.assertEqual(get_addresses_for_contact(contact.name), [])

	def test_returns_addresses_with_source_labels(self):
		contact = make_contact()
		address = make_address()
		customer = make_customer(customer_primary_contact=contact.name, customer_primary_address=address.name)

		results = get_addresses_for_contact(contact.name)

		self.assertEqual(len(results), 1)
		self.assertEqual(results[0]["name"], address.name)
		self.assertEqual(results[0]["source_label"], f"via Customer: {customer.customer_name}")

	def test_returns_multiple_distinct_addresses(self):
		from contact_enhancements.tests.test_supplier_hooks import make_supplier

		contact = make_contact()
		customer_address = make_address()
		supplier_address = make_address()
		make_customer(customer_primary_contact=contact.name, customer_primary_address=customer_address.name)
		make_supplier(supplier_primary_contact=contact.name, supplier_primary_address=supplier_address.name)

		results = get_addresses_for_contact(contact.name)

		self.assertEqual({r["name"] for r in results}, {customer_address.name, supplier_address.name})


class TestSearchPageLenIsBounded(FrappeTestCase):
	"""search_contacts_with_details is whitelisted, so page_len arrives
	from the caller. The dialog sends 10, but nothing stopped anyone
	asking for a million rows of a join that can't use an index."""

	def test_an_absurd_page_len_is_clamped(self):
		from contact_enhancements.api.contact_lookup import MAX_SEARCH_PAGE_LEN

		captured = {}
		original = contact_lookup_module._contact_search_query

		def spy(txt, start, page_len):
			captured["page_len"] = page_len
			captured["start"] = start
			return original(txt, start, page_len)

		contact_lookup_module._contact_search_query = spy
		try:
			search_contacts_with_details("Omar", start=-5, page_len=1000000)
		finally:
			contact_lookup_module._contact_search_query = original

		self.assertEqual(captured["page_len"], MAX_SEARCH_PAGE_LEN)
		self.assertEqual(captured["start"], 0)

	def test_a_normal_page_len_is_left_alone(self):
		captured = {}
		original = contact_lookup_module._contact_search_query

		def spy(txt, start, page_len):
			captured["page_len"] = page_len
			return original(txt, start, page_len)

		contact_lookup_module._contact_search_query = spy
		try:
			search_contacts_with_details("Omar", page_len=10)
		finally:
			contact_lookup_module._contact_search_query = original

		self.assertEqual(captured["page_len"], 10)
