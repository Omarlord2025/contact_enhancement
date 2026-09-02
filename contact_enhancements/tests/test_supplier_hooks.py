"""Tests for contact_enhancements.supplier_hooks (Phase 1) - the closest
analog to test_customer_hooks.py, since Supplier already has the same
supplier_primary_contact Link-field shape Customer does."""

import frappe
from frappe.tests.utils import FrappeTestCase

from contact_enhancements.supplier_hooks import (
	backfill_supplier_primary_contact_from_dynamic_link,
	link_primary_contact,
	sync_supplier_address_from_contact_links,
)
from contact_enhancements.tests.test_customer_hooks import make_customer
from contact_enhancements.tests.test_lead_lookup import make_contact


def make_supplier(**kwargs):
	supplier = frappe.get_doc(
		{
			"doctype": "Supplier",
			"supplier_name": frappe.generate_hash(length=10),
			"supplier_group": frappe.db.get_value("Supplier Group", {}, "name"),
			"supplier_type": "Company",
			**kwargs,
		}
	)
	# supplier_primary_contact is genuinely mandatory once this app's own
	# Property Setter is applied - ignore_mandatory lets tests build a
	# Supplier without one where that's specifically what's under test
	# (mirrors test_customer_hooks.py's own make_customer/_bypasses_
	# mandatory_link_fields reasoning).
	supplier.flags.ignore_mandatory = "supplier_primary_contact" not in kwargs
	supplier.insert(ignore_permissions=True)
	return supplier


class TestBackfillSupplierPrimaryContactFromDynamicLink(FrappeTestCase):
	def test_noop_when_already_set(self):
		contact = make_contact()
		supplier = make_supplier(supplier_primary_contact=contact.name)

		backfill_supplier_primary_contact_from_dynamic_link(supplier)
		self.assertEqual(supplier.supplier_primary_contact, contact.name)

	def test_backfills_from_an_existing_dynamic_link(self):
		supplier = make_supplier()
		contact = make_contact()
		contact.append("links", {"link_doctype": "Supplier", "link_name": supplier.name})
		contact.save(ignore_permissions=True)

		supplier.supplier_primary_contact = None
		backfill_supplier_primary_contact_from_dynamic_link(supplier)

		self.assertEqual(supplier.supplier_primary_contact, contact.name)

	def test_stays_blank_when_no_dynamic_link_exists(self):
		supplier = make_supplier()
		supplier.supplier_primary_contact = None
		backfill_supplier_primary_contact_from_dynamic_link(supplier)
		self.assertFalse(supplier.supplier_primary_contact)


def make_address(**kwargs):
	address = frappe.get_doc(
		{
			"doctype": "Address",
			"address_title": frappe.generate_hash(length=10),
			"address_type": "Office",
			"address_line1": "1 Test Street",
			"city": "Cairo",
			"country": "Egypt",
			**kwargs,
		}
	)
	address.insert(ignore_permissions=True)
	return address


class TestSyncSupplierAddressFromContactLinks(FrappeTestCase):
	def test_backfills_address_from_a_customer_sharing_the_same_contact(self):
		contact = make_contact()
		address = make_address()
		make_customer(customer_primary_contact=contact.name, customer_primary_address=address.name)
		supplier = make_supplier(supplier_primary_contact=contact.name)

		sync_supplier_address_from_contact_links(supplier)

		self.assertEqual(supplier.supplier_primary_address, address.name)

	def test_never_overwrites_an_already_set_supplier_address(self):
		contact = make_contact()
		customer_address = make_address()
		supplier_address = make_address()
		make_customer(customer_primary_contact=contact.name, customer_primary_address=customer_address.name)
		supplier = make_supplier(
			supplier_primary_contact=contact.name, supplier_primary_address=supplier_address.name
		)

		sync_supplier_address_from_contact_links(supplier)

		self.assertEqual(supplier.supplier_primary_address, supplier_address.name)

	def test_noop_without_a_supplier_primary_contact(self):
		supplier = make_supplier()
		sync_supplier_address_from_contact_links(supplier)  # must not raise
		self.assertFalse(supplier.supplier_primary_address)

	def test_stays_blank_when_no_customer_shares_this_contact(self):
		contact = make_contact()
		supplier = make_supplier(supplier_primary_contact=contact.name)

		sync_supplier_address_from_contact_links(supplier)

		self.assertFalse(supplier.supplier_primary_address)

	def test_falls_back_to_a_linked_lead_when_no_customer_or_other_supplier_matches(self):
		from contact_enhancements.tests.test_lead_lookup import make_lead, make_lead_address

		contact = make_contact()
		lead = make_lead(company_name="Supplier Sync Fallback Lead Co")
		lead_address = make_lead_address(lead.name)
		contact.append("links", {"link_doctype": "Lead", "link_name": lead.name})
		contact.save(ignore_permissions=True)
		supplier = make_supplier(supplier_primary_contact=contact.name)

		sync_supplier_address_from_contact_links(supplier)

		self.assertEqual(supplier.supplier_primary_address, lead_address.name)


class TestSupplierLinkPrimaryContact(FrappeTestCase):
	def test_links_the_contact_back_to_the_supplier(self):
		contact = make_contact()
		supplier = make_supplier(supplier_primary_contact=contact.name)

		link_primary_contact(supplier)

		contact.reload()
		self.assertTrue(contact.has_link("Supplier", supplier.name))

	def test_links_the_address_back_to_the_supplier(self):
		address = make_address()
		contact = make_contact()
		supplier = make_supplier(supplier_primary_contact=contact.name, supplier_primary_address=address.name)

		link_primary_contact(supplier)

		address.reload()
		self.assertTrue(address.has_link("Supplier", supplier.name))

	def test_noop_without_a_primary_contact(self):
		supplier = make_supplier()
		link_primary_contact(supplier)  # must not raise


class TestSupplierNativeFlows(FrappeTestCase):
	"""Confirms the reasoning in setup.property_setters.
	create_supplier_contact_property_setters's own docstring: Supplier.
	create_primary_contact()/create_primary_address() (native on_update)
	become inert once supplier_primary_contact is genuinely mandatory."""

	def test_create_primary_contact_is_dead_code_once_primary_contact_is_reqd(self):
		# Supplier.create_primary_contact() only fires
		# `if not self.supplier_primary_contact` - once that field is
		# reqd=1, _validate_mandatory() (runs before on_update) guarantees
		# no doc ever reaches on_update blank, so the native method's own
		# guard is never satisfied. Simulated directly here (rather than
		# depending on the live Property Setter being applied in this
		# test run) by calling it against a Supplier that already has one.
		contact = make_contact()
		supplier = make_supplier(supplier_primary_contact=contact.name, mobile_no="01099866601")

		supplier.create_primary_contact()

		supplier.reload()
		self.assertEqual(supplier.supplier_primary_contact, contact.name)
