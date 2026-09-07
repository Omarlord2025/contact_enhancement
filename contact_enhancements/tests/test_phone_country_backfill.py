# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

"""Tests for the Phone Country Backfill feature: contact_hooks.
resolve_phone_country (the two-rule resolver) and api.phone_country_
resolution (the Manual Resolution Report's server side). The one-time
patches.backfill_contact_phone_country_resolution migration itself is a
thin loop over resolve_phone_country already covered here plus the existing
per-patch batching convention shared with every other patch in this app -
not re-tested separately.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from contact_enhancements.api.phone_country_resolution import (
	get_unresolved_phone_country_report,
	resolve_phone_country_row,
)
from contact_enhancements.contact_hooks import resolve_phone_country
from contact_enhancements.tests.test_lead_lookup import _random_mobile_no, make_contact


class TestResolvePhoneCountry(FrappeTestCase):
	def test_explicit_prefix_is_detected(self):
		self.assertEqual(
			resolve_phone_country("+447400123456"),
			{"country": "United Kingdom", "confidence": "detected"},
		)

	def test_egyptian_mobile_pattern_without_prefix(self):
		self.assertEqual(
			resolve_phone_country("01033344455"),
			{"country": "Egypt", "confidence": "pattern"},
		)

	def test_formatting_noise_does_not_defeat_the_pattern_rule(self):
		self.assertEqual(
			resolve_phone_country("010-3334-4455"),
			{"country": "Egypt", "confidence": "pattern"},
		)

	def test_egyptian_landline_prefix_is_unresolved(self):
		# 10 digits, "02" prefix - a real Cairo landline shape, but not one
		# of the 4 mobile prefixes this backfill rule is scoped to, and no
		# explicit country-code prefix either.
		self.assertIsNone(resolve_phone_country("0223456789"))

	def test_garbage_is_unresolved(self):
		self.assertIsNone(resolve_phone_country("12345"))


class TestPhoneCountryResolutionReport(FrappeTestCase):
	def _make_unresolved_row(self, phone, country="Egypt"):
		"""A Contact with one phone row, force-marked Unresolved (and, when
		phone/country diverge, planted directly via frappe.db so the row's
		own live-save normalization never gets the chance to correct it
		first) - simulating what the backfill patch leaves behind for a row
		neither of its two rules could resolve.
		"""
		contact = make_contact(phone_nos=[_random_mobile_no()])
		row_name = contact.phone_nos[0].name
		frappe.db.set_value(
			"Contact Phone",
			row_name,
			{"phone": phone, "country": country, "custom_country_resolution": "Unresolved"},
		)
		return contact, row_name

	def test_get_unresolved_report_includes_flagged_row(self):
		contact, row_name = self._make_unresolved_row("07400123001")

		report = get_unresolved_phone_country_report()

		self.assertIn(row_name, [row["contact_phone_row"] for row in report])
		matching = next(row for row in report if row["contact_phone_row"] == row_name)
		self.assertEqual(matching["contact"], contact.name)

	def test_resolve_phone_country_row_success_saves_country_and_landline_and_leaves_the_report(self):
		contact, row_name = self._make_unresolved_row("07400123002")

		result = resolve_phone_country_row(contact.name, row_name, "United Kingdom", landline=1)

		self.assertEqual(result, {"ok": True})
		saved = frappe.db.get_value(
			"Contact Phone",
			row_name,
			["phone", "country", "custom_landline", "custom_country_resolution"],
			as_dict=True,
		)
		self.assertEqual(saved.phone, "+447400123002")
		self.assertEqual(saved.country, "United Kingdom")
		self.assertEqual(saved.custom_landline, 1)
		self.assertEqual(saved.custom_country_resolution, "Manual")

		report = get_unresolved_phone_country_report()
		self.assertNotIn(row_name, [row["contact_phone_row"] for row in report])

	def test_resolve_phone_country_row_rejects_invalid_country_and_stays_in_the_report(self):
		contact, row_name = self._make_unresolved_row("07400123003")

		self.assertRaises(
			frappe.ValidationError,
			resolve_phone_country_row,
			contact.name,
			row_name,
			"Not A Real Country",
			0,
		)
		self.assertEqual(
			frappe.db.get_value("Contact Phone", row_name, "custom_country_resolution"), "Unresolved"
		)

	def test_resolve_phone_country_row_still_enforces_phone_validation(self):
		# "12345" is neither a valid Egyptian number nor carries any
		# country code of its own - normalize_and_validate_contact_phone
		# must still reject it, exactly as it would for a live save.
		contact, row_name = self._make_unresolved_row("12345")

		self.assertRaises(
			frappe.ValidationError,
			resolve_phone_country_row,
			contact.name,
			row_name,
			"Egypt",
			0,
		)
		self.assertEqual(
			frappe.db.get_value("Contact Phone", row_name, "custom_country_resolution"), "Unresolved"
		)
