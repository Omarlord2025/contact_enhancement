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
