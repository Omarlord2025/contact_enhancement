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


class TestSupplierContactRequirementIsGrandfathered(FrappeTestCase):
	"""See TestPrimaryContactRequirementIsGrandfathered in
	test_customer_hooks - Supplier was the worse case when this was
	measured: nearly half of existing Suppliers would have been frozen,
	and not one of them had a Contact that could be backfilled."""

	def test_a_new_supplier_still_needs_a_contact(self):
		doc = frappe.get_doc(
			{
				"doctype": "Supplier",
				"supplier_name": "Grandfather New " + frappe.generate_hash(length=6),
				"supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 0}, "name"),
				"supplier_type": "Company",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_an_existing_supplier_without_one_stays_editable(self):
		doc = frappe.get_doc(
			{
				"doctype": "Supplier",
				"supplier_name": "Grandfather Legacy " + frappe.generate_hash(length=6),
				"supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 0}, "name"),
				"supplier_type": "Company",
			}
		)
		doc.flags.ignore_validate = True
		doc.insert(ignore_permissions=True)

		reloaded = frappe.get_doc("Supplier", doc.name)
		self.assertFalse(reloaded.supplier_primary_contact)
		reloaded.save(ignore_permissions=True)  # must not raise

	def test_the_field_is_no_longer_statically_mandatory(self):
		self.assertFalse(frappe.get_meta("Supplier").get_field("supplier_primary_contact").reqd)


class TestSupplierIdentityFromContactCompanyName(FrappeTestCase):
	"""Same rule as Customer - a Contact with a company_name names the
	Supplier after that company - but supplier_type is treated more
	conservatively, because it is a REQUIRED three-option Select the user
	actively answers and Partnership cannot be derived from a Contact."""

	def _new_supplier(self, contact_name, supplier_type="Company"):
		supplier = frappe.new_doc("Supplier")
		supplier.supplier_primary_contact = contact_name
		supplier.supplier_type = supplier_type
		supplier.supplier_group = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
		supplier.insert(ignore_permissions=True)
		return supplier

	def test_a_contact_with_a_company_name_names_the_supplier_after_it(self):
		person = "Omar Ahmed " + frappe.generate_hash(length=6)
		contact = make_contact(first_name=person)
		company = "Nile Parts " + frappe.generate_hash(length=6)
		frappe.db.set_value("Contact", contact.name, "company_name", company)

		supplier = self._new_supplier(contact.name)

		self.assertEqual(supplier.supplier_name, company)
		self.assertEqual(supplier.supplier_type, "Company")

	def test_a_contact_without_one_names_it_after_the_person(self):
		person = "Omar Ahmed " + frappe.generate_hash(length=6)
		contact = make_contact(first_name=person)

		supplier = self._new_supplier(contact.name)

		self.assertEqual(supplier.supplier_name, person)
		# Corrected off the "Company" default - a Company named after a
		# human is the shape propagation refuses to sync.
		self.assertEqual(supplier.supplier_type, "Individual")

	def test_a_deliberate_partnership_choice_is_never_overridden(self):
		# The reason this is more conservative than Customer's equivalent.
		person = "Omar Ahmed " + frappe.generate_hash(length=6)
		contact = make_contact(first_name=person)
		company = "Nile Partners " + frappe.generate_hash(length=6)
		frappe.db.set_value("Contact", contact.name, "company_name", company)

		supplier = self._new_supplier(contact.name, supplier_type="Partnership")

		self.assertEqual(supplier.supplier_name, company)
		self.assertEqual(supplier.supplier_type, "Partnership")

	def test_a_deliberate_individual_choice_is_never_overridden(self):
		person = "Omar Ahmed " + frappe.generate_hash(length=6)
		contact = make_contact(first_name=person)
		company = "Nile Parts " + frappe.generate_hash(length=6)
		frappe.db.set_value("Contact", contact.name, "company_name", company)

		supplier = self._new_supplier(contact.name, supplier_type="Individual")

		self.assertEqual(supplier.supplier_name, company)
		self.assertEqual(supplier.supplier_type, "Individual")

	def test_a_name_already_chosen_is_never_overwritten(self):
		person = "Omar Ahmed " + frappe.generate_hash(length=6)
		contact = make_contact(first_name=person)
		company = "Nile Parts " + frappe.generate_hash(length=6)
		frappe.db.set_value("Contact", contact.name, "company_name", company)

		supplier = frappe.new_doc("Supplier")
		supplier.supplier_primary_contact = contact.name
		supplier.supplier_name = "Deliberately Chosen Supplier"
		supplier.supplier_type = "Company"
		supplier.supplier_group = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
		supplier.insert(ignore_permissions=True)

		self.assertEqual(supplier.supplier_name, "Deliberately Chosen Supplier")
