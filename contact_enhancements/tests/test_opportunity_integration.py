# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from contact_enhancements.api.opportunity_lookup import get_party_and_contact_details_for_opportunity
from contact_enhancements.tests.test_lead_lookup import make_contact, make_lead
from contact_enhancements.tests.test_customer_hooks import make_customer


class TestOpportunityIntegration(FrappeTestCase):
	"""Unit and integration tests for Opportunity fast contact lookup and party resolution."""

	def _random_phone(self):
		import random
		return f"010{random.randint(10000000, 99999999)}"

	def test_contact_linked_to_customer_resolves_customer(self):
		phone = self._random_phone()
		contact = make_contact(
			first_name=f"Tarek {frappe.generate_hash(length=5)}",
			email_ids=[{"email_id": f"tarek_{frappe.generate_hash(length=5)}@example.com", "is_primary": 1}],
		)
		contact.append("phone_nos", {"phone": phone, "is_primary_mobile_no": 1})
		contact.save(ignore_permissions=True)

		customer = make_customer(
			customer_name=f"Customer {frappe.generate_hash(length=5)}",
			customer_primary_contact=contact.name,
			customer_type="Company",
		)

		details = get_party_and_contact_details_for_opportunity(contact.name)
		self.assertIsNotNone(details)
		self.assertEqual(details["opportunity_from"], "Customer")
		self.assertEqual(details["party_name"], customer.name)
		self.assertEqual(details["contact_person"], contact.name)
		self.assertEqual(details["contact_mobile"], contact.phone_nos[0].phone)

	def test_contact_linked_to_lead_resolves_lead(self):
		phone = self._random_phone()
		contact = make_contact(
			first_name=f"Nouran {frappe.generate_hash(length=5)}",
			email_ids=[{"email_id": f"nouran_{frappe.generate_hash(length=5)}@example.com", "is_primary": 1}],
		)
		contact.append("phone_nos", {"phone": phone, "is_primary_mobile_no": 1, "custom_whatsapp": 1})
		contact.save(ignore_permissions=True)

		lead = make_lead(
			lead_name=f"Lead {frappe.generate_hash(length=5)}",
			lead_primary_contact=contact.name,
		)

		details = get_party_and_contact_details_for_opportunity(contact.name)
		self.assertIsNotNone(details)
		self.assertEqual(details["opportunity_from"], "Lead")
		self.assertEqual(details["party_name"], lead.name)
		self.assertEqual(details["contact_person"], contact.name)
		self.assertEqual(details["contact_mobile"], contact.phone_nos[0].phone)
		self.assertEqual(details["whatsapp"], contact.phone_nos[0].phone)

	def test_unlinked_contact_auto_creates_lead(self):
		phone = self._random_phone()
		email = f"ziad_{frappe.generate_hash(length=5)}@example.com"
		contact = make_contact(
			first_name=f"Ziad {frappe.generate_hash(length=5)}",
			email_ids=[{"email_id": email, "is_primary": 1}],
		)
		contact.append("phone_nos", {"phone": phone, "is_primary_mobile_no": 1})
		contact.save(ignore_permissions=True)

		details = get_party_and_contact_details_for_opportunity(contact.name, company_name="Ziad Tech LLC")
		self.assertIsNotNone(details)
		self.assertEqual(details["opportunity_from"], "Lead")
		self.assertTrue(bool(details["party_name"]))
		self.assertEqual(details["contact_person"], contact.name)

		# Verify Lead in database
		lead_doc = frappe.get_doc("Lead", details["party_name"])
		self.assertEqual(lead_doc.lead_primary_contact, contact.name)
		self.assertEqual(lead_doc.company_name, "Ziad Tech LLC")
		self.assertEqual(lead_doc.email_id, email)
		self.assertEqual(lead_doc.mobile_no, contact.phone_nos[0].phone)

	def test_address_resolution_from_linked_customer(self):
		phone = self._random_phone()
		contact = make_contact(first_name=f"Hany {frappe.generate_hash(length=5)}")
		contact.append("phone_nos", {"phone": phone, "is_primary_mobile_no": 1})
		contact.save(ignore_permissions=True)

		# Create address
		addr = frappe.new_doc("Address")
		addr.address_title = f"Hany Office {frappe.generate_hash(length=4)}"
		addr.address_type = "Office"
		addr.address_line1 = "10 El Tahrir St"
		addr.city = "Cairo"
		addr.country = "Egypt"
		addr.append("links", {"link_doctype": "Contact", "link_name": contact.name})
		addr.insert(ignore_permissions=True)

		details = get_party_and_contact_details_for_opportunity(contact.name)
		self.assertIsNotNone(details)
		self.assertEqual(details["customer_address"], addr.name)
		self.assertIsNotNone(details["address_display"])

	def test_saving_opportunity_links_to_contact_and_address(self):
		phone = self._random_phone()
		contact = make_contact(first_name=f"Sami {frappe.generate_hash(length=5)}")
		contact.append("phone_nos", {"phone": phone, "is_primary_mobile_no": 1})
		contact.save(ignore_permissions=True)

		details = get_party_and_contact_details_for_opportunity(contact.name)

		opp = frappe.new_doc("Opportunity")
		opp.opportunity_from = details["opportunity_from"]
		opp.party_name = details["party_name"]
		opp.contact_person = contact.name
		opp.insert(ignore_permissions=True)

		contact.reload()
		links = [(l.link_doctype, l.link_name) for l in contact.links]
		self.assertIn(("Opportunity", opp.name), links)
