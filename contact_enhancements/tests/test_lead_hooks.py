"""Tests for contact_enhancements.lead_hooks - Lead has no dedicated
"primary address" field of its own, so its own version of the cross-
doctype address-inheritance chain (utils.resolve_address_from_contact_links)
Dynamic-Links an Address directly to the Lead instead of backfilling a
field."""

import frappe
from frappe.tests.utils import FrappeTestCase

from contact_enhancements.lead_hooks import sync_lead_address_from_contact_links
from contact_enhancements.tests.test_customer_hooks import make_customer
from contact_enhancements.tests.test_lead_lookup import make_lead
from contact_enhancements.tests.test_supplier_hooks import make_address
from contact_enhancements.utils import dynamic_link_lookup


def _lead_contact_name(lead_name):
	return dynamic_link_lookup(
		{"parenttype": "Contact", "link_doctype": "Lead", "link_name": lead_name}, "parent"
	)


class TestSyncLeadAddressFromContactLinks(FrappeTestCase):
	def test_dynamic_links_an_address_from_a_customer_sharing_the_same_contact(self):
		lead = make_lead(company_name="Lead Addr Sync Co")
		contact_name = _lead_contact_name(lead.name)
		address = make_address()
		make_customer(customer_primary_contact=contact_name, customer_primary_address=address.name)

		sync_lead_address_from_contact_links(lead)

		address.reload()
		self.assertTrue(address.has_link("Lead", lead.name))

	def test_noop_when_lead_already_has_a_linked_address(self):
		from contact_enhancements.tests.test_lead_lookup import make_lead_address

		lead = make_lead(company_name="Lead Addr Sync Existing Co")
		existing_address = make_lead_address(lead.name)
		contact_name = _lead_contact_name(lead.name)
		other_address = make_address()
		make_customer(customer_primary_contact=contact_name, customer_primary_address=other_address.name)

		sync_lead_address_from_contact_links(lead)

		other_address.reload()
		self.assertFalse(other_address.has_link("Lead", lead.name))
		existing_address.reload()
		self.assertTrue(existing_address.has_link("Lead", lead.name))

	def test_stays_unlinked_when_nothing_in_the_chain_resolves(self):
		lead = make_lead(company_name="Lead Addr Sync Nothing Co")
		sync_lead_address_from_contact_links(lead)  # must not raise

		linked = dynamic_link_lookup(
			{"parenttype": "Address", "link_doctype": "Lead", "link_name": lead.name}, "parent"
		)
		self.assertIsNone(linked)


class TestLeadPrimaryContactIntegration(FrappeTestCase):
	def test_lead_with_existing_primary_contact_bypasses_duplicate_creation(self):
		from contact_enhancements.tests.test_lead_lookup import _random_mobile_no, _random_name, make_contact

		mobile = _random_mobile_no()
		contact = make_contact(first_name=_random_name(), phone_nos=[mobile])

		# Insert a Lead pointing to this existing contact
		lead = frappe.get_doc({
			"doctype": "Lead",
			"lead_primary_contact": contact.name,
			"first_name": "TestLead",
		})
		lead.insert(ignore_permissions=True)

		# Check that contact is linked to this Lead
		contact.reload()
		self.assertTrue(contact.has_link("Lead", lead.name))

		# Verify no duplicate contact was created with this mobile
		matching_contacts = frappe.get_all(
			"Contact Phone",
			filters={"phone": ["like", f"%{mobile[-8:]}%"]},
			fields=["parent"],
			distinct=True,
		)
		self.assertEqual(len(matching_contacts), 1)
		self.assertEqual(matching_contacts[0].parent, contact.name)

	def test_lead_backfills_from_primary_contact(self):
		from contact_enhancements.tests.test_lead_lookup import _random_mobile_no, _random_name, make_contact

		mobile = _random_mobile_no()
		contact = make_contact(
			first_name=_random_name(),
			company_name="Auto Sync Org",
			phone_nos=[mobile],
		)

		lead = frappe.get_doc({
			"doctype": "Lead",
			"lead_primary_contact": contact.name,
		})
		lead.insert(ignore_permissions=True)

		self.assertEqual(lead.first_name, contact.first_name)
		self.assertEqual(lead.company_name, "Auto Sync Org")
		self.assertEqual(lead.mobile_no, contact.phone_nos[0].phone)

	def test_get_contact_snapshot_for_lead(self):
		from contact_enhancements.api.lead_lookup import get_contact_snapshot_for_lead
		from contact_enhancements.tests.test_lead_lookup import _random_mobile_no, _random_name, make_contact

		mobile = _random_mobile_no()
		contact = make_contact(
			first_name=_random_name(),
			company_name="Acme Inc",
			phone_nos=[mobile],
		)

		snapshot = get_contact_snapshot_for_lead(contact.name)
		self.assertIsNotNone(snapshot)
		self.assertEqual(snapshot["first_name"], contact.first_name)
		self.assertEqual(snapshot["company_name"], "Acme Inc")
		self.assertEqual(snapshot["mobile_no"], contact.phone_nos[0].phone)


