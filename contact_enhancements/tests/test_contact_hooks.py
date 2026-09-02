"""Tests for contact_enhancements.contact_hooks - the Arabic first-name
normalization rules (normalize_arabic_first_name / normalize_contact_first_name),
the per-country phone validation rules (normalize_and_validate_contact_phone
/ normalize_and_validate_contact_phones), and the landline/mobile-channel
exclusivity rule (_enforce_landline_exclusivity /
enforce_contact_phone_channel_exclusivity).
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from contact_enhancements.contact_hooks import (
	_country_for_region_code,
	_detect_phone_country,
	_enforce_landline_exclusivity,
	_national_digits,
	_running_in_background_job,
	enforce_contact_phone_channel_exclusivity,
	normalize_and_validate_contact_phone,
	normalize_and_validate_contact_phones,
	enforce_unique_mobile_number,
	normalize_arabic_first_name,
	normalize_contact_first_name,
	propagate_contact_changes_to_linked_doctypes,
	strip_phone_formatting_noise,
	try_to_e164,
	validate_full_name_has_at_least_three_words,
)
from contact_enhancements.tests.test_lead_lookup import make_contact


class TestNormalizeArabicFirstName(FrappeTestCase):
	def test_blank_is_noop(self):
		self.assertIsNone(normalize_arabic_first_name(None))
		self.assertEqual(normalize_arabic_first_name(""), "")

	def test_valid_arabic_name_is_unchanged(self):
		self.assertEqual(normalize_arabic_first_name("احمد"), "احمد")

	def test_valid_english_name_is_unchanged(self):
		self.assertEqual(normalize_arabic_first_name("Ahmed"), "Ahmed")

	def test_normalizes_word_starting_with_alef_hamza_above(self):
		self.assertEqual(normalize_arabic_first_name("أحمد"), "احمد")

	def test_allows_plain_alef_at_word_start(self):
		# The one explicitly-allowed exception to rule 1 - "ا" (plain alef,
		# U+0627) is fine, only "أ" (alef with hamza above, U+0623) isn't.
		self.assertEqual(normalize_arabic_first_name("ابراهيم"), "ابراهيم")

	def test_strips_diacritics(self):
		self.assertEqual(normalize_arabic_first_name("مُحمد"), "محمد")

	def test_strips_tatweel(self):
		self.assertEqual(normalize_arabic_first_name("محمــد"), "محمد")

	def test_collapses_consecutive_spaces(self):
		self.assertEqual(normalize_arabic_first_name("احمد  محمد"), "احمد محمد")

	def test_allows_single_space_between_words(self):
		self.assertEqual(normalize_arabic_first_name("احمد محمد"), "احمد محمد")

	def test_strips_digits(self):
		self.assertEqual(normalize_arabic_first_name("Ahmed1"), "Ahmed")

	def test_strips_symbols(self):
		self.assertEqual(normalize_arabic_first_name("Ahmed@"), "Ahmed")

	def test_digit_between_words_becomes_a_separator_not_a_merge(self):
		# A disallowed character is replaced with a space, not deleted -
		# "Ahmed1Ali" must become "Ahmed Ali" (two words), never
		# "AhmedAli" (silently merged into one).
		self.assertEqual(normalize_arabic_first_name("Ahmed1Ali"), "Ahmed Ali")

	def test_normalizes_word_ending_in_teh_marbuta(self):
		self.assertEqual(normalize_arabic_first_name("فاطمة"), "فاطمه")

	def test_normalizes_word_ending_in_yeh(self):
		self.assertEqual(normalize_arabic_first_name("علي"), "على")

	def test_allows_word_ending_in_alef_maksura(self):
		# The one explicitly-allowed exception to rule 5 - "ى" (alef
		# maksura, U+0649) is fine, only "ي" (yeh, U+064A) isn't.
		self.assertEqual(normalize_arabic_first_name("مصطفى"), "مصطفى")

	def test_applies_all_rules_together_in_one_pass(self):
		# "أحمد1  علي" - word-initial alef hamza, a digit, a double
		# space, and a word-final yeh, all in one input.
		self.assertEqual(normalize_arabic_first_name("أحمد1  علي"), "احمد على")


class TestNormalizeContactFirstName(FrappeTestCase):
	def test_insert_succeeds_with_a_previously_invalid_first_name(self):
		# Never rejected - the doc_event corrects it in place and the
		# insert goes through.
		contact = frappe.new_doc("Contact")
		contact.first_name = "أحمد"
		contact.insert(ignore_permissions=True)
		self.assertEqual(contact.first_name, "احمد")

	def test_insert_succeeds_with_an_already_valid_first_name(self):
		contact = frappe.new_doc("Contact")
		contact.first_name = "Ahmed Test"
		contact.insert(ignore_permissions=True)
		self.assertEqual(contact.first_name, "Ahmed Test")

	def test_normalizes_in_place_on_the_doc_directly(self):
		doc = frappe.new_doc("Contact")
		doc.first_name = "فاطمة"
		normalize_contact_first_name(doc)
		self.assertEqual(doc.first_name, "فاطمه")


class TestValidateFullNameHasAtLeastThreeWords(FrappeTestCase):
	def test_raises_on_a_single_word(self):
		self.assertRaises(frappe.ValidationError, validate_full_name_has_at_least_three_words, "Ahmed")

	def test_raises_on_two_words(self):
		self.assertRaises(frappe.ValidationError, validate_full_name_has_at_least_three_words, "Ahmed Mohamed")

	def test_passes_on_three_words(self):
		validate_full_name_has_at_least_three_words("Ahmed Mohamed Sabry")  # must not raise

	def test_passes_on_more_than_three_words(self):
		validate_full_name_has_at_least_three_words("Ahmed Mohamed Sabry El Din")  # must not raise

	def test_blank_is_a_noop(self):
		validate_full_name_has_at_least_three_words(None)  # must not raise
		validate_full_name_has_at_least_three_words("")  # must not raise

	def test_collapses_repeated_whitespace_before_counting(self):
		validate_full_name_has_at_least_three_words("Ahmed   Mohamed  Sabry")  # must not raise


class TestNormalizeAndValidateContactPhone(FrappeTestCase):
	"""normalize_and_validate_contact_phone - one phone number, validated
	against one country's own numbering plan via the phonenumbers library,
	covering every country (not just Egypt, the old hardcoded regex this
	replaced)."""

	def test_valid_egypt_mobile_passes_through(self):
		self.assertEqual(
			normalize_and_validate_contact_phone("01033344455", "Egypt"), "+201033344455"
		)

	def test_normalizes_egypt_plus20_prefix_to_e164(self):
		self.assertEqual(
			normalize_and_validate_contact_phone("+201033344455", "Egypt"), "+201033344455"
		)

	def test_normalizes_whitespace_and_dashes(self):
		self.assertEqual(
			normalize_and_validate_contact_phone("010 3334-4455", "Egypt"), "+201033344455"
		)

	def test_rejects_unissued_egypt_prefix(self):
		self.assertRaises(
			frappe.ValidationError, normalize_and_validate_contact_phone, "01312345678", "Egypt"
		)

	def test_rejects_unparseable_garbage(self):
		self.assertRaises(
			frappe.ValidationError, normalize_and_validate_contact_phone, "not-a-phone-number", "Egypt"
		)

	def test_rejects_blank_phone(self):
		self.assertRaises(frappe.ValidationError, normalize_and_validate_contact_phone, "", "Egypt")

	def test_rejects_blank_country(self):
		self.assertRaises(
			frappe.ValidationError, normalize_and_validate_contact_phone, "01033344455", ""
		)

	def test_accepts_egypt_landline_number_even_when_not_marked_landline(self):
		# The actual fix for "still be able to add landline numbers": a
		# genuinely valid Egyptian number (Cairo landline) is accepted
		# regardless of the row's own custom_landline flag - that flag is
		# a descriptive tag, not a rule the number has to satisfy. This is
		# the exact number shape this app's own old Egypt-only regex used
		# to reject unconditionally, for every row, because it had no
		# concept of "landline" at all.
		self.assertEqual(
			normalize_and_validate_contact_phone("0223456789", "Egypt", is_landline=False),
			"+20223456789",
		)

	def test_accepts_egypt_mobile_number_even_when_marked_landline(self):
		# The flag doesn't gate acceptance in either direction.
		self.assertEqual(
			normalize_and_validate_contact_phone("01033344455", "Egypt", is_landline=True),
			"+201033344455",
		)

	def test_validates_a_non_egypt_country_mobile_number(self):
		# Not Egypt-specific - a genuine UK mobile, checked against the
		# UK's own numbering plan.
		self.assertEqual(
			normalize_and_validate_contact_phone("+447400123456", "United Kingdom"),
			"+447400123456",
		)

	def test_accepts_a_non_egypt_country_landline_number(self):
		self.assertEqual(
			normalize_and_validate_contact_phone("02079460958", "United Kingdom"),
			"+442079460958",
		)

	def test_rejects_a_number_that_is_not_valid_for_the_selected_country(self):
		# A UK-shaped number, checked against Egypt's numbering plan, with
		# no country code of its own to say otherwise - genuinely invalid
		# for the country actually selected.
		self.assertRaises(
			frappe.ValidationError,
			normalize_and_validate_contact_phone,
			"07400123456",
			"Egypt",
		)

	def test_honors_a_foreign_country_code_prefix_regardless_of_selected_country(self):
		# phonenumbers always honors an explicit "+"/"00" prefix over the
		# region hint - a UK number stays valid even when "Egypt" is the
		# selected country (see this module's own docstring for why this
		# matters: it's what keeps ERPNext's native Lead auto-Contact
		# creation from breaking for a non-Egyptian Lead).
		self.assertEqual(
			normalize_and_validate_contact_phone("+447400123456", "Egypt"),
			"+447400123456",
		)


class TestDetectPhoneCountry(FrappeTestCase):
	"""_detect_phone_country - the Country a phone number's own country
	code identifies, used by normalize_and_validate_contact_phones to
	self-correct a row's own country for a properly-prefixed foreign
	number."""

	def test_detects_country_from_a_plus_prefixed_number(self):
		self.assertEqual(_detect_phone_country("+447400123456"), "United Kingdom")

	def test_detects_egypt_from_a_plus20_prefixed_number(self):
		self.assertEqual(_detect_phone_country("+201033344455"), "Egypt")

	def test_returns_none_for_a_bare_local_format_number(self):
		# No country code of its own - genuinely ambiguous, not guessable.
		self.assertIsNone(_detect_phone_country("01033344455"))

	def test_returns_none_for_unparseable_garbage(self):
		self.assertIsNone(_detect_phone_country("not-a-phone-number"))

	def test_returns_none_for_blank(self):
		self.assertIsNone(_detect_phone_country(""))
		self.assertIsNone(_detect_phone_country(None))


class TestCountryForRegionCode(FrappeTestCase):
	"""Country.code carries no index, so this reverse lookup scans
	tabCountry - and it runs once per phone row on every Contact save,
	with every row almost always resolving to the same country."""

	def setUp(self):
		# Each test starts from a cold cache - it lives on frappe.local,
		# which persists across tests within one run.
		frappe.local.contact_enhancements_country_by_code = None

	def test_resolves_a_region_code_to_its_country(self):
		self.assertEqual(_country_for_region_code("EG"), "Egypt")

	def test_is_case_insensitive(self):
		self.assertEqual(_country_for_region_code("eg"), "Egypt")

	def test_returns_none_for_a_blank_region(self):
		self.assertIsNone(_country_for_region_code(None))
		self.assertIsNone(_country_for_region_code(""))

	def test_queries_once_per_distinct_region_not_once_per_call(self):
		with patch.object(
			frappe.db, "get_value", wraps=frappe.db.get_value
		) as spy:
			for _ in range(5):
				_country_for_region_code("EG")

		country_lookups = [c for c in spy.call_args_list if c.args and c.args[0] == "Country"]
		self.assertEqual(len(country_lookups), 1)

	def test_caches_a_negative_result_too(self):
		# A region with no Country record must not be re-queried on every
		# row either - the miss is as repeatable as the hit.
		with patch.object(
			frappe.db, "get_value", wraps=frappe.db.get_value
		) as spy:
			for _ in range(3):
				self.assertIsNone(_country_for_region_code("ZZ"))

		country_lookups = [c for c in spy.call_args_list if c.args and c.args[0] == "Country"]
		self.assertEqual(len(country_lookups), 1)


class TestNormalizeAndValidateContactPhones(FrappeTestCase):
	"""normalize_and_validate_contact_phones - the Contact validate
	doc_event that runs every Contact Numbers row through the function
	above, against that row's own country."""

	def test_noop_when_row_has_no_phone_value(self):
		# A blank row (nothing typed yet) is skipped entirely, including
		# one with a blank country - so the real "Country is mandatory"
		# error (Frappe's own generic mandatory-field check) is the one a
		# user sees for an empty new row, not a confusing phone-specific
		# error from here first.
		doc = frappe.new_doc("Contact")
		doc.first_name = "Test"
		doc.append("phone_nos", {"phone": "", "country": None})
		normalize_and_validate_contact_phones(doc)  # must not raise

	def test_noop_without_any_phone_rows(self):
		doc = frappe.new_doc("Contact")
		doc.first_name = "Test"
		normalize_and_validate_contact_phones(doc)  # must not raise

	def test_normalizes_every_row_in_place(self):
		doc = frappe.new_doc("Contact")
		doc.first_name = "Test"
		doc.append("phone_nos", {"phone": "+201033344455", "country": "Egypt"})
		doc.append("phone_nos", {"phone": "0223456789", "country": "Egypt", "custom_landline": 1})

		normalize_and_validate_contact_phones(doc)

		self.assertEqual(doc.phone_nos[0].phone, "+201033344455")
		self.assertEqual(doc.phone_nos[1].phone, "+20223456789")
		self.assertEqual(doc.phone_nos[0].custom_phone_national, "01033344455")
		self.assertEqual(doc.phone_nos[1].custom_phone_national, "0223456789")

	def test_each_row_is_validated_against_its_own_country(self):
		# The actual point of moving country onto the row: one Contact,
		# two numbers, two different countries, both valid.
		doc = frappe.new_doc("Contact")
		doc.first_name = "Test"
		doc.append("phone_nos", {"phone": "01033344455", "country": "Egypt"})
		doc.append("phone_nos", {"phone": "07400123456", "country": "United Kingdom"})

		normalize_and_validate_contact_phones(doc)

		self.assertEqual(doc.phone_nos[0].phone, "+201033344455")
		self.assertEqual(doc.phone_nos[1].phone, "+447400123456")

	def test_raises_when_any_row_is_invalid(self):
		doc = frappe.new_doc("Contact")
		doc.first_name = "Test"
		doc.append("phone_nos", {"phone": "01033344455", "country": "Egypt"})
		doc.append("phone_nos", {"phone": "01312345678", "country": "Egypt"})  # unissued prefix

		self.assertRaises(frappe.ValidationError, normalize_and_validate_contact_phones, doc)

	def test_self_corrects_row_country_from_a_foreign_prefixed_number(self):
		# The fix for ERPNext's own native Lead auto-Contact-creation,
		# which has no way to supply the real country up front - see this
		# module's own docstring above normalize_and_validate_contact_phone.
		doc = frappe.new_doc("Contact")
		doc.first_name = "Test"
		doc.append(
			"phone_nos",
			{"phone": "+447400123456", "country": "Egypt", "is_primary_mobile_no": 1},  # still at the default
		)

		normalize_and_validate_contact_phones(doc)

		self.assertEqual(doc.phone_nos[0].country, "United Kingdom")
		self.assertEqual(doc.phone_nos[0].phone, "+447400123456")

	def test_does_not_correct_country_for_an_ordinary_local_format_number(self):
		doc = frappe.new_doc("Contact")
		doc.first_name = "Test"
		doc.append("phone_nos", {"phone": "01033344455", "country": "Egypt"})

		normalize_and_validate_contact_phones(doc)

		self.assertEqual(doc.phone_nos[0].country, "Egypt")

	def test_correcting_one_row_does_not_affect_a_different_row(self):
		doc = frappe.new_doc("Contact")
		doc.first_name = "Test"
		doc.append("phone_nos", {"phone": "01033344455", "country": "Egypt"})
		doc.append("phone_nos", {"phone": "+447400123456", "country": "Egypt"})

		normalize_and_validate_contact_phones(doc)

		self.assertEqual(doc.phone_nos[0].country, "Egypt")
		self.assertEqual(doc.phone_nos[1].country, "United Kingdom")

	def test_skips_rows_with_no_phone_value(self):
		doc = frappe.new_doc("Contact")
		doc.first_name = "Test"
		doc.append("phone_nos", {"phone": "", "country": "Egypt"})
		normalize_and_validate_contact_phones(doc)  # must not raise

	def test_runs_via_a_real_contact_insert(self):
		# End-to-end: the doc_event actually wired up in hooks.py.
		contact = frappe.new_doc("Contact")
		contact.first_name = "Test"
		contact.append(
			"phone_nos", {"phone": "+201033344401", "country": "Egypt", "is_primary_mobile_no": 1}
		)
		contact.insert(ignore_permissions=True)

		self.assertEqual(contact.phone_nos[0].phone, "+201033344401")

	def test_clearing_a_saved_rows_country_self_heals_on_resave(self):
		# Before E.164 storage, a saved row's country genuinely couldn't be
		# recovered once cleared - the stored local-form value ("01033344455")
		# carries no country code of its own, so _detect_phone_country had
		# nothing to go on and normalize_and_validate_contact_phone's own
		# "Select a Country" error fired instead. Once phone is always
		# stored as E.164 ("+201033344455"), the stored value itself always
		# carries its own country code, so _detect_phone_country can now
		# always self-heal a cleared country from the phone value alone -
		# a genuine improvement, not just a format change.
		contact = frappe.new_doc("Contact")
		contact.first_name = "Test"
		contact.append("phone_nos", {"phone": "01033344402"})
		contact.insert(ignore_permissions=True)

		frappe.db.set_value("Contact Phone", contact.phone_nos[0].name, "country", None)
		contact.reload()
		contact.save(ignore_permissions=True)  # must not raise - self-heals instead

		self.assertEqual(contact.phone_nos[0].country, "Egypt")
		self.assertEqual(contact.phone_nos[0].phone, "+201033344402")

	def test_row_country_defaults_to_egypt(self):
		# setup/custom_fields.py's own DocField default - confirmed applied
		# immediately at .append() time for a child row (unlike a
		# top-level frappe.new_doc(), which only applies its own defaults
		# at insert time).
		contact = frappe.new_doc("Contact")
		contact.first_name = "Test"
		contact.append("phone_nos", {"phone": "01033344403"})
		contact.insert(ignore_permissions=True)
		self.assertEqual(contact.phone_nos[0].country, "Egypt")

	def test_resyncs_doc_mobile_no_after_normalization(self):
		# Contact's own native validate() calls self.set_primary(
		# "mobile_no") *before* this hook ever runs (Document.hook's own
		# compose() ordering: controller validate() always completes
		# first) - reading row.phone as it stood pre-normalization. Left
		# unpatched, doc.mobile_no would keep holding that stale, local-
		# format value for the rest of this same save - confirmed the
		# hard way, since Customer/Supplier's own mobile_no fields
		# (native fetch_from="...primary_contact.mobile_no") would then
		# fetch that same stale value.
		contact = frappe.new_doc("Contact")
		contact.first_name = "Test"
		contact.append("phone_nos", {"phone": "01033344404", "is_primary_mobile_no": 1})
		contact.insert(ignore_permissions=True)

		self.assertEqual(contact.mobile_no, "+201033344404")
		self.assertEqual(contact.phone_nos[0].phone, "+201033344404")


class TestEnforceLandlineExclusivity(FrappeTestCase):
	"""_enforce_landline_exclusivity - custom_landline and the three
	mobile-oriented flags (is_primary_mobile_no/custom_whatsapp/
	custom_telegram) can't both be set on one row.

	This function only ever sees a static snapshot at validate time - it
	has no idea which checkbox a user clicked most recently (that
	ordering only exists in the browser, see the live field-by-field
	handlers in public/js/contact.js), so the one deterministic rule it
	can apply is: whenever both sides end up set at once, custom_landline
	wins and the other three are cleared - never the other way round.
	"""

	def _row(self, **kwargs):
		contact = frappe.new_doc("Contact")
		contact.append("phone_nos", {"phone": "01033344455", "country": "Egypt", **kwargs})
		return contact.phone_nos[0]

	def test_landline_wins_over_is_primary_mobile_no(self):
		row = self._row(is_primary_mobile_no=1, custom_landline=1)
		_enforce_landline_exclusivity(row)
		self.assertEqual(row.is_primary_mobile_no, 0)
		self.assertEqual(row.custom_landline, 1)

	def test_landline_wins_over_whatsapp_and_telegram(self):
		row = self._row(custom_whatsapp=1, custom_telegram=1, custom_landline=1)
		_enforce_landline_exclusivity(row)
		self.assertEqual(row.custom_whatsapp, 0)
		self.assertEqual(row.custom_telegram, 0)
		self.assertEqual(row.custom_landline, 1)

	def test_landline_alone_is_untouched(self):
		row = self._row(custom_landline=1)
		_enforce_landline_exclusivity(row)
		self.assertEqual(row.custom_landline, 1)

	def test_mobile_oriented_flags_alone_are_untouched(self):
		row = self._row(is_primary_mobile_no=1, custom_whatsapp=1, custom_telegram=1)
		_enforce_landline_exclusivity(row)
		self.assertEqual(row.is_primary_mobile_no, 1)
		self.assertEqual(row.custom_whatsapp, 1)
		self.assertEqual(row.custom_telegram, 1)

	def test_neither_set_is_untouched(self):
		row = self._row()
		_enforce_landline_exclusivity(row)
		self.assertFalse(row.custom_landline)
		self.assertFalse(row.is_primary_mobile_no)


class TestEnforceContactPhoneChannelExclusivity(FrappeTestCase):
	"""enforce_contact_phone_channel_exclusivity - the Contact validate
	doc_event that runs every row through the function above."""

	def test_applies_to_every_row(self):
		doc = frappe.new_doc("Contact")
		doc.first_name = "Test"
		doc.append(
			"phone_nos",
			{"phone": "01033344455", "country": "Egypt", "is_primary_mobile_no": 1, "custom_landline": 1},
		)
		doc.append(
			"phone_nos",
			{"phone": "01099998888", "country": "Egypt", "custom_landline": 1, "custom_whatsapp": 1},
		)

		enforce_contact_phone_channel_exclusivity(doc)

		self.assertEqual(doc.phone_nos[0].is_primary_mobile_no, 0)
		self.assertEqual(doc.phone_nos[0].custom_landline, 1)
		self.assertEqual(doc.phone_nos[1].custom_whatsapp, 0)
		self.assertEqual(doc.phone_nos[1].custom_landline, 1)

	def test_runs_via_a_real_contact_insert(self):
		# End-to-end: the doc_event actually wired up in hooks.py. An API
		# call or Data Import row could set both flags at once - this is
		# the real, unbypassable enforcement behind the JS mirror in
		# public/js/contact.js.
		contact = frappe.new_doc("Contact")
		contact.first_name = "Test"
		contact.append(
			"phone_nos",
			{
				"phone": "0223456789",
				"country": "Egypt",
				"custom_landline": 1,
				"custom_whatsapp": 1,
				"is_primary_mobile_no": 1,
			},
		)
		contact.insert(ignore_permissions=True)

		self.assertEqual(contact.phone_nos[0].custom_landline, 1)
		self.assertEqual(contact.phone_nos[0].custom_whatsapp, 0)
		self.assertEqual(contact.phone_nos[0].is_primary_mobile_no, 0)


class TestStripPhoneFormattingNoise(FrappeTestCase):
	def test_strips_spaces_dashes_dots_parens(self):
		self.assertEqual(strip_phone_formatting_noise("010 1234-5678"), "01012345678")
		self.assertEqual(strip_phone_formatting_noise("(010) 1234.5678"), "01012345678")

	def test_blank_is_unchanged(self):
		self.assertEqual(strip_phone_formatting_noise(""), "")
		self.assertIsNone(strip_phone_formatting_noise(None))

	def test_leaves_a_plus_prefix_intact(self):
		self.assertEqual(strip_phone_formatting_noise("+20 10 1234-5678"), "+201012345678")

	def test_does_not_touch_an_already_clean_value(self):
		self.assertEqual(strip_phone_formatting_noise("01312345678"), "01312345678")


class TestTryToE164(FrappeTestCase):
	"""try_to_e164 - safe, non-raising E.164 canonicalization for comparing
	a not-yet-normalized value (e.g. Lead.whatsapp_no) against an
	already-normalized Contact Phone.phone row. Never used to decide what
	gets stored - see api/lead_lookup._sync_whatsapp_to_contact for the
	real bug this was written to fix."""

	def test_converts_a_local_form_number_given_its_country(self):
		self.assertEqual(try_to_e164("01033344455", "Egypt"), "+201033344455")

	def test_already_e164_passes_through_unchanged_even_with_no_country(self):
		# An explicit "+" prefix is always honored over any region hint -
		# confirmed elsewhere this session - so no country is needed here.
		self.assertEqual(try_to_e164("+201033344455"), "+201033344455")

	def test_blank_returns_unchanged(self):
		self.assertEqual(try_to_e164(""), "")
		self.assertIsNone(try_to_e164(None))

	def test_unparseable_without_a_country_returns_unchanged(self):
		# No "+" prefix and no country to disambiguate against - genuinely
		# can't be parsed, so falls back to the original value rather than
		# raising.
		self.assertEqual(try_to_e164("01033344455"), "01033344455")

	def test_invalid_for_the_given_country_returns_unchanged(self):
		# Unissued Egyptian prefix - parses but isn't a real number, so the
		# original value is returned rather than a bogus E.164 string.
		self.assertEqual(try_to_e164("01312345678", "Egypt"), "01312345678")

	def test_garbage_returns_unchanged(self):
		self.assertEqual(try_to_e164("not-a-phone-number", "Egypt"), "not-a-phone-number")

	def test_a_bad_country_falls_back_to_regionless_parsing(self):
		# Mirrors _phonenumbers_region_for_country's own ValidationError for
		# a blank/misconfigured Country - never raised through here, since
		# this function is comparison-only and must never block a save.
		self.assertEqual(try_to_e164("01033344455", ""), "01033344455")


class TestNationalDigits(FrappeTestCase):
	"""_national_digits - reproduces the bare local-form digits from an
	already-E.164 number, to populate Contact Phone.custom_phone_national
	so search can still find a Contact by the local-format number staff
	are used to typing."""

	def test_reproduces_the_original_egypt_local_form(self):
		self.assertEqual(_national_digits("+201033344455"), "01033344455")

	def test_reproduces_the_original_uk_local_form(self):
		self.assertEqual(_national_digits("+447400123456"), "07400123456")

	def test_reproduces_a_landline_local_form(self):
		self.assertEqual(_national_digits("+20223456789"), "0223456789")


class TestRunningInBackgroundJob(FrappeTestCase):
	def test_false_by_default_in_a_test(self):
		# Confirms FrappeTestCase itself never sets frappe.local.job -
		# tests have to simulate it explicitly to exercise the lenient path.
		self.assertFalse(_running_in_background_job())

	def test_true_once_frappe_local_job_is_set(self):
		frappe.local.job = frappe._dict(site="erpnext")
		try:
			self.assertTrue(_running_in_background_job())
		finally:
			frappe.local.job = None

	def test_false_again_once_cleared(self):
		frappe.local.job = frappe._dict(site="erpnext")
		frappe.local.job = None
		self.assertFalse(_running_in_background_job())


class TestNormalizeAndValidateContactPhonesBackgroundJobLeniency(FrappeTestCase):
	"""normalize_and_validate_contact_phones drops an unparseable row
	instead of raising, but only when genuinely running inside a
	background job (_running_in_background_job) - never for an
	interactive save, Data Import, or an ordinary test."""

	def test_raises_normally_outside_a_background_job(self):
		doc = frappe.new_doc("Contact")
		doc.first_name = "Test"
		doc.append("phone_nos", {"phone": "not-a-real-number", "country": "Egypt"})

		self.assertRaises(frappe.ValidationError, normalize_and_validate_contact_phones, doc)

	def test_drops_the_row_inside_a_background_job(self):
		doc = frappe.new_doc("Contact")
		doc.first_name = "Test"
		doc.append("phone_nos", {"phone": "not-a-real-number", "country": "Egypt"})

		frappe.local.job = frappe._dict(site="erpnext")
		try:
			normalize_and_validate_contact_phones(doc)  # must not raise
		finally:
			frappe.local.job = None

		self.assertEqual(len(doc.phone_nos), 0)

	def test_valid_rows_are_unaffected_inside_a_background_job(self):
		doc = frappe.new_doc("Contact")
		doc.first_name = "Test"
		doc.append("phone_nos", {"phone": "01033344455", "country": "Egypt"})
		doc.append("phone_nos", {"phone": "not-a-real-number", "country": "Egypt"})

		frappe.local.job = frappe._dict(site="erpnext")
		try:
			normalize_and_validate_contact_phones(doc)
		finally:
			frappe.local.job = None

		self.assertEqual(len(doc.phone_nos), 1)
		self.assertEqual(doc.phone_nos[0].phone, "+201033344455")


class TestEnforceUniqueMobileNumber(FrappeTestCase):
	"""enforce_unique_mobile_number - the hard-blocking layer for genuine
	mobile-number duplicates (Phase 0d). Only ever fires for a non-landline
	row that's new or has a changed phone this save."""

	def test_raises_on_a_genuine_duplicate_mobile(self):
		make_contact(phone_nos=["01099822201"])
		with self.assertRaises(frappe.ValidationError):
			make_contact(phone_nos=["01099822201"])

	def test_allows_a_duplicate_landline(self):
		make_contact(phone_nos=["01099822202"])
		other = frappe.new_doc("Contact")
		other.first_name = "Landline Duplicate"
		other.append("phone_nos", {"phone": "01099822202", "custom_landline": 1})
		other.insert(ignore_permissions=True)  # must not raise
		self.assertTrue(other.name)

	def test_allows_two_contacts_with_different_mobile_numbers(self):
		make_contact(phone_nos=["01099822203"])
		other = make_contact(phone_nos=["01099822204"])  # must not raise
		self.assertTrue(other.name)

	def test_does_not_retroactively_fire_on_an_unrelated_resave_of_an_already_conflicting_legacy_row(self):
		# The gating (row.is_new() or its phone changed this save) is what
		# decides whether a row's conflict is even checked at all - so an
		# unrelated resave of an existing, phone-unchanged row must never
		# even consult find_contacts_by_phone, regardless of what it would
		# say. Mocked here to always report a conflict, proving the gate
		# itself short-circuits before ever calling it - a stronger proof
		# than trying to construct one genuinely-conflicting legacy row,
		# which the live UNIQUE INDEX (Phase 0d) now makes permanently
		# impossible to persist at all (see tests/test_contact_dedupe.py's
		# own _add_duplicate_phone docstring).
		contact = make_contact(phone_nos=["01099822205"])
		contact.reload()
		contact.first_name = "Legacy Duplicate Renamed"
		with patch(
			"contact_enhancements.api.contact_dedupe.find_contacts_by_phone",
			return_value=["some-other-contact"],
		):
			contact.save(ignore_permissions=True)  # must not raise
		self.assertEqual(contact.first_name, "Legacy Duplicate Renamed")

	def test_runs_via_enforce_unique_mobile_number_directly(self):
		make_contact(phone_nos=["01099822206"])
		doc = frappe.new_doc("Contact")
		doc.first_name = "Direct Call"
		doc.append("phone_nos", {"phone": "+201099822206", "country": "Egypt"})
		self.assertRaises(frappe.ValidationError, enforce_unique_mobile_number, doc)

	def test_raises_on_the_same_number_entered_twice_on_one_contact(self):
		# Regression test for a real bug found against production-shaped
		# data: this hook originally only ever searched *other* Contacts
		# (find_contacts_by_phone(..., exclude=doc.name)), so a Contact
		# with the identical number on two of its own rows slipped past
		# it entirely and only ever surfaced as a raw DB IntegrityError
		# once the unique index patch tried to run.
		doc = frappe.new_doc("Contact")
		doc.first_name = "Self Duplicate"
		doc.append("phone_nos", {"phone": "+201099822207", "country": "Egypt"})
		doc.append("phone_nos", {"phone": "+201099822207", "country": "Egypt"})
		self.assertRaises(frappe.ValidationError, enforce_unique_mobile_number, doc)

	def test_a_duplicate_landline_row_on_one_contact_is_allowed(self):
		doc = frappe.new_doc("Contact")
		doc.first_name = "Self Duplicate Landline"
		doc.append("phone_nos", {"phone": "+201099822208", "country": "Egypt", "custom_landline": 1})
		doc.append("phone_nos", {"phone": "+201099822208", "country": "Egypt", "custom_landline": 1})
		enforce_unique_mobile_number(doc)  # must not raise

	def test_does_not_retroactively_fire_on_unrelated_resave_of_a_self_duplicate(self):
		# Same legacy-safe gating as the cross-Contact case: an already-
		# saved Contact with a self-duplicate that predates this check
		# must not suddenly start failing an unrelated resave of a
		# different field. Two genuinely-persisted rows sharing a number
		# can no longer be constructed at all (the live UNIQUE INDEX,
		# Phase 0d, forbids it even via a raw db_insert() - see
		# tests/test_contact_dedupe.py's own _add_duplicate_phone
		# docstring), so the second row here is appended with an explicit
		# name purely in memory (confirmed empirically: giving a child row
		# its own explicit name keeps is_new() False, exactly like a real
		# already-saved row), and get_doc_before_save() is overridden on
		# this one instance to report that same row as already present
		# with an unchanged value - reproducing exactly what the real
		# gate reads (doc.get_doc_before_save().phone_nos), without
		# needing the database to hold a state it can no longer hold.
		contact = make_contact(phone_nos=["01099822209"])
		contact.reload()
		real_row = contact.phone_nos[0]
		contact.append(
			"phone_nos", {"name": "fake-legacy-row-209", "phone": real_row.phone, "country": "Egypt"}
		)

		previous_snapshot = frappe.get_doc("Contact", contact.name)
		previous_snapshot.append(
			"phone_nos", {"name": "fake-legacy-row-209", "phone": real_row.phone, "country": "Egypt"}
		)
		contact.get_doc_before_save = lambda: previous_snapshot

		contact.first_name = "Legacy Self Duplicate Renamed"
		contact.save(ignore_permissions=True)  # must not raise
		self.assertEqual(contact.first_name, "Legacy Self Duplicate Renamed")


class TestPropagateContactChangesToLinkedDoctypes(FrappeTestCase):
	"""Deliberately the one exception to this app's usual "initial
	inheritance, not continuous sync" rule - see contact_hooks.py's own
	module docstring right above propagate_contact_changes_to_linked_
	doctypes for the full reasoning."""

	def test_propagates_a_changed_name_to_customer(self):
		from contact_enhancements.tests.test_customer_hooks import make_customer

		contact = make_contact()
		customer = make_customer(customer_primary_contact=contact.name)
		contact.reload()  # make_customer's own on_update hook re-saved this Contact

		contact.first_name = "Ahmed Mohamed Sabry"
		contact.save(ignore_permissions=True)

		self.assertEqual(frappe.db.get_value("Customer", customer.name, "customer_name"), "Ahmed Mohamed Sabry")

	def test_propagates_a_changed_name_to_supplier(self):
		from contact_enhancements.tests.test_supplier_hooks import make_supplier

		contact = make_contact()
		supplier = make_supplier(supplier_primary_contact=contact.name)
		contact.reload()

		contact.first_name = "Ahmed Mohamed Supplier"
		contact.save(ignore_permissions=True)

		self.assertEqual(frappe.db.get_value("Supplier", supplier.name, "supplier_name"), "Ahmed Mohamed Supplier")

	def test_propagates_a_changed_name_to_employee(self):
		from contact_enhancements.tests.test_employee_hooks import make_employee

		contact = make_contact()
		employee = make_employee(employee_primary_contact=contact.name)
		contact.reload()

		contact.first_name = "Ahmed Mohamed Employee"
		contact.save(ignore_permissions=True)

		self.assertEqual(frappe.db.get_value("Employee", employee.name, "first_name"), "Ahmed Mohamed Employee")

	def test_propagates_a_changed_name_to_user(self):
		from contact_enhancements.tests.test_user_hooks import make_user

		contact = make_contact()
		user = make_user(user_primary_contact=contact.name)
		contact.reload()

		contact.first_name = "Ahmed Mohamed User"
		contact.save(ignore_permissions=True)

		self.assertEqual(frappe.db.get_value("User", user.name, "first_name"), "Ahmed Mohamed User")

	def test_propagates_changed_email_and_phone_to_employee(self):
		from contact_enhancements.tests.test_employee_hooks import make_employee

		contact = make_contact(email_ids=[{"email_id": "old@example.com", "is_primary": 1}])
		contact.append("phone_nos", {"phone": "01099811122", "is_primary_mobile_no": 1})
		contact.save(ignore_permissions=True)
		employee = make_employee(employee_primary_contact=contact.name)
		contact.reload()

		contact.email_ids[0].email_id = "new@example.com"
		contact.phone_nos[0].phone = "01099833344"
		contact.save(ignore_permissions=True)

		self.assertEqual(frappe.db.get_value("Employee", employee.name, "personal_email"), "new@example.com")
		self.assertEqual(frappe.db.get_value("Employee", employee.name, "cell_number"), "+201099833344")

	def test_does_not_touch_user_email(self):
		# User.email is that document's own naming field - never
		# overwritten by this background sync (would mean renaming the
		# User, a real, deliberate operation this hook has no business
		# deciding on its own).
		from contact_enhancements.tests.test_user_hooks import make_user

		contact = make_contact(email_ids=[{"email_id": "before@example.com", "is_primary": 1}])
		contact.save(ignore_permissions=True)
		user = make_user(user_primary_contact=contact.name)
		original_email = user.name
		contact.reload()

		contact.email_ids[0].email_id = "after@example.com"
		contact.save(ignore_permissions=True)

		self.assertTrue(frappe.db.exists("User", original_email))

	def test_noop_when_nothing_relevant_changed(self):
		from contact_enhancements.tests.test_customer_hooks import make_customer

		contact = make_contact()
		customer = make_customer(customer_primary_contact=contact.name)
		contact.reload()
		before = frappe.db.get_value("Customer", customer.name, "modified")

		contact.department = "Some unrelated field"
		contact.save(ignore_permissions=True)

		after = frappe.db.get_value("Customer", customer.name, "modified")
		self.assertEqual(before, after)

	def test_never_overwrites_with_a_blank_value(self):
		from contact_enhancements.tests.test_customer_hooks import make_customer

		contact = make_contact(email_ids=[{"email_id": "keep@example.com", "is_primary": 1}])
		contact.save(ignore_permissions=True)
		customer = make_customer(customer_primary_contact=contact.name)
		frappe.db.set_value("Customer", customer.name, "email_id", "keep@example.com", update_modified=False)
		contact.reload()

		contact.first_name = "Renamed Only Now Fully"
		contact.save(ignore_permissions=True)

		self.assertEqual(frappe.db.get_value("Customer", customer.name, "email_id"), "keep@example.com")

	def test_does_not_affect_an_unrelated_customer(self):
		from contact_enhancements.tests.test_customer_hooks import make_customer

		contact = make_contact()
		other_contact = make_contact()
		other_customer = make_customer(customer_primary_contact=other_contact.name)
		before = frappe.db.get_value("Customer", other_customer.name, "customer_name")

		contact.first_name = "Some Renamed Person"
		contact.save(ignore_permissions=True)

		self.assertEqual(frappe.db.get_value("Customer", other_customer.name, "customer_name"), before)

	def test_does_not_crash_the_save_when_a_target_field_collides_with_a_unique_constraint(self):
		# Reproduces the exact production failure: tabUser.mobile_no
		# carries a native UNIQUE index this app doesn't control - a
		# Contact that's the primary contact for *two different* Users (a
		# real case: the same person can genuinely hold more than one
		# account) used to crash the whole Contact save the instant the
		# second User's own propagated mobile_no collided with the
		# first's already-updated value.
		from contact_enhancements.tests.test_user_hooks import make_user

		contact = make_contact()
		contact.append("phone_nos", {"phone": "01099811111", "is_primary_mobile_no": 1})
		contact.save(ignore_permissions=True)
		user1 = make_user(user_primary_contact=contact.name, mobile_no="01099822222")
		user2 = make_user(user_primary_contact=contact.name, mobile_no="01099833333")
		contact.reload()

		contact.phone_nos[0].phone = "01099844444"
		contact.save(ignore_permissions=True)  # must not raise

		# One of the two succeeded (whichever the loop reached first);
		# the other was left exactly as it was, not crashed or corrupted.
		mobile_nos = {
			frappe.db.get_value("User", user1.name, "mobile_no"),
			frappe.db.get_value("User", user2.name, "mobile_no"),
		}
		self.assertIn("+201099844444", mobile_nos)
		self.assertEqual(len(mobile_nos), 2)  # still distinct - no corruption

	def test_a_colliding_field_does_not_block_the_other_fields_on_the_same_record(self):
		# Writes for one record are batched into a single statement, so a
		# collision on one field would roll back that record's whole
		# update unless the failure path retries field by field. Same
		# two-Users-one-Contact setup as above (tabUser.mobile_no is
		# UNIQUE), but the name changes in the same save: whichever User
		# loses the mobile_no race must still get the new first_name.
		from contact_enhancements.tests.test_user_hooks import make_user

		contact = make_contact()
		contact.append("phone_nos", {"phone": "01099855111", "is_primary_mobile_no": 1})
		contact.save(ignore_permissions=True)
		user1 = make_user(user_primary_contact=contact.name, mobile_no="01099855222")
		user2 = make_user(user_primary_contact=contact.name, mobile_no="01099855333")
		contact.reload()

		contact.first_name = "Batched Fallback Person"
		contact.phone_nos[0].phone = "01099855444"
		contact.save(ignore_permissions=True)

		first_names = [
			frappe.db.get_value("User", user1.name, "first_name"),
			frappe.db.get_value("User", user2.name, "first_name"),
		]
		# Both, not just the one whose mobile_no write happened to succeed.
		self.assertEqual(first_names, ["Batched Fallback Person", "Batched Fallback Person"])
