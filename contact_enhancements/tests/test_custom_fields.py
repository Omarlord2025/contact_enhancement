"""Tests for contact_enhancements.setup.custom_fields - the WhatsApp/
Telegram/Landline channel checkboxes on Contact Phone, and the mandatory
per-row Country field on Contact Phone that number is validated against
(contact_enhancements.contact_hooks.normalize_and_validate_contact_phones).
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from contact_enhancements.setup.custom_fields import create_contact_enhancements_custom_fields


class TestCreateContactEnhancementsCustomFields(FrappeTestCase):
	def test_adds_contact_phone_channel_checkboxes(self):
		create_contact_enhancements_custom_fields()

		meta = frappe.get_meta("Contact Phone")
		for fieldname in ("custom_whatsapp", "custom_telegram", "custom_landline"):
			self.assertEqual(meta.get_field(fieldname).fieldtype, "Check")

	def test_adds_mandatory_country_field_on_contact_phone(self):
		create_contact_enhancements_custom_fields()

		field = frappe.get_meta("Contact Phone").get_field("country")
		self.assertEqual(field.fieldtype, "Link")
		self.assertEqual(field.options, "Country")
		self.assertEqual(field.reqd, 1)
		self.assertEqual(field.default, "Egypt")
		self.assertEqual(field.in_list_view, 1)

	def test_country_field_is_a_real_database_column(self):
		# Unlike a plain Property Setter (setup/property_setters.py's own
		# search_index gotcha), create_custom_fields applies the schema
		# change immediately via its own internal frappe.db.updatedb() -
		# confirm the column genuinely exists, not just the meta flag.
		create_contact_enhancements_custom_fields()

		self.assertIn("country", frappe.db.get_table_columns("Contact Phone"))

	def test_idempotent_does_not_duplicate_custom_field(self):
		create_contact_enhancements_custom_fields()
		create_contact_enhancements_custom_fields()

		count = frappe.db.count("Custom Field", {"dt": "Contact Phone", "fieldname": "country"})
		self.assertEqual(count, 1)
