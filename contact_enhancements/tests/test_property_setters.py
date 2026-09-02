"""Tests for contact_enhancements.setup.property_setters - making Customer
Primary Contact a genuine, enforced-everywhere requirement (not just a UI
nudge), satisfied in the Desk UI by the mandatory contact-picker dialog in
public/js/customer.js - and disabling Customer's own Quick Entry, which has
no customer_primary_contact field at all and would otherwise let that
dialog be bypassed entirely.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from contact_enhancements.setup.property_setters import (
	create_contact_enhancements_index_property_setters,
	create_contact_enhancements_property_setters,
	create_contact_full_name_property_setters,
	create_contact_phone_hidden_field_property_setters,
	create_employee_full_name_property_setters,
	create_supplier_contact_property_setters,
	create_user_contact_property_setters,
)
from contact_enhancements.tests.test_lead_lookup import (
	_random_mobile_no,
	make_contact,
	make_lead,
	make_lead_address,
)
from contact_enhancements.tests.test_supplier_hooks import make_supplier


class TestCreateContactEnhancementsPropertySetters(FrappeTestCase):
	def test_makes_customer_primary_contact_mandatory(self):
		create_contact_enhancements_property_setters()

		self.assertEqual(frappe.get_meta("Customer").get_field("customer_primary_contact").reqd, 1)

	def test_makes_customer_primary_address_mandatory(self):
		create_contact_enhancements_property_setters()

		self.assertEqual(frappe.get_meta("Customer").get_field("customer_primary_address").reqd, 1)

	def test_disables_customer_quick_entry(self):
		# Customer's own Quick Entry has no customer_primary_contact field
		# at all (confirmed by driving it directly) - left enabled, a user
		# could create a Customer through it and never see the mandatory
		# dialog. Has to be a Property Setter, not a client-side
		# is_quick_entry() override: a doctype's doctype_js content only
		# gets evaluated once a real frappe.ui.form.Form is constructed
		# for it, and Quick Entry never constructs one until after its own
		# dialog decision is already made - confirmed by testing that a
		# JS-only override never actually took effect.
		create_contact_enhancements_property_setters()

		self.assertEqual(frappe.get_meta("Customer").quick_entry, 0)

	def test_idempotent_does_not_duplicate_property_setter(self):
		create_contact_enhancements_property_setters()
		create_contact_enhancements_property_setters()

		count = frappe.db.count(
			"Property Setter",
			{"doc_type": "Customer", "field_name": "customer_primary_contact", "property": "reqd"},
		)
		self.assertEqual(count, 1)

		address_count = frappe.db.count(
			"Property Setter",
			{"doc_type": "Customer", "field_name": "customer_primary_address", "property": "reqd"},
		)
		self.assertEqual(address_count, 1)

		quick_entry_count = frappe.db.count(
			"Property Setter", {"doc_type": "Customer", "property": "quick_entry"}
		)
		self.assertEqual(quick_entry_count, 1)

	def test_customer_cannot_be_saved_without_a_primary_contact(self):
		create_contact_enhancements_property_setters()

		customer = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": frappe.generate_hash(length=10),
				# Unrelated site quirk, not this app's concern: Selling
				# Settings' default Customer Group resolves to a group-type
				# node here, which erpnext's own validate_customer_group()
				# rejects regardless of anything in this app - set a valid
				# leaf explicitly to isolate the mandatory-contact check
				# this test is actually about.
				"customer_group": "Individual",
			}
		)
		self.assertRaises(frappe.MandatoryError, customer.insert)

	def test_customer_cannot_be_saved_without_a_primary_address(self):
		create_contact_enhancements_property_setters()
		contact = make_contact()

		customer = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": frappe.generate_hash(length=10),
				"customer_group": "Individual",  # same unrelated site quirk as above
				"customer_primary_contact": contact.name,
			}
		)
		self.assertRaises(frappe.MandatoryError, customer.insert)

	def test_native_lead_conversion_still_works(self):
		# erpnext's own native "Create > Customer" button on Lead
		# (erpnext.crm.doctype.lead.lead._make_customer) never sets
		# customer_primary_contact or customer_primary_address directly -
		# it only Dynamic-Links a Contact and Address via
		# Customer.link_address_and_contact(), which runs in on_update
		# (after the mandatory check already ran in validate).
		# _find_linked_doc_from_source (customer_hooks.py) backfills both
		# during validate() so this core flow keeps working - confirmed
		# broken without that backfill (MandatoryError) before adding it.
		from erpnext.crm.doctype.lead.lead import _make_customer

		lead = frappe.get_doc(
			{
				"doctype": "Lead",
				"first_name": "Native",
				"last_name": "Conversion",
				"company_name": "Native Conversion Co",
				"mobile_no": _random_mobile_no(),
			}
		)
		lead.insert(ignore_permissions=True)
		address = make_lead_address(lead.name)

		create_contact_enhancements_property_setters()

		customer = _make_customer(lead.name, ignore_permissions=True)
		# Unrelated site quirk, not this app's concern: Selling Settings'
		# default Customer Group resolves to a group-type node here, which
		# erpnext's own validate_customer_group() rejects regardless of
		# anything in this app - work around it to isolate the actual
		# question (do customer_primary_contact/customer_primary_address
		# end up populated).
		customer.customer_group = "Individual"
		customer.insert(ignore_permissions=True)

		self.assertTrue(customer.customer_primary_contact)
		self.assertEqual(customer.customer_primary_address, address.name)

	def test_native_lead_conversion_requires_manual_address_when_lead_has_none(self):
		# The documented boundary: unlike its auto-created Contact, a Lead
		# has no Address at all unless a user manually adds one via the
		# Lead's own Address & Contacts widget - the common case. Nothing
		# in this app synthesizes a placeholder Address from the Lead's
		# flat city/state/country fields (no clean mapping to Address's own
		# mandatory address_line1), so native conversion still requires the
		# user to supply one by hand, same as any other genuinely-missing
		# mandatory field - it doesn't silently fail, and it doesn't
		# silently fabricate data either.
		from erpnext.crm.doctype.lead.lead import _make_customer

		lead = frappe.get_doc(
			{
				"doctype": "Lead",
				"first_name": "No",
				"last_name": "Address",
				"company_name": "No Address Co",
				"mobile_no": _random_mobile_no(),
			}
		)
		lead.insert(ignore_permissions=True)

		create_contact_enhancements_property_setters()

		customer = _make_customer(lead.name, ignore_permissions=True)
		customer.customer_group = "Individual"  # same unrelated site quirk as above

		self.assertRaises(frappe.MandatoryError, customer.insert, ignore_permissions=True)


class TestCreateContactEnhancementsIndexPropertySetters(FrappeTestCase):
	def test_indexes_customer_primary_address(self):
		create_contact_enhancements_index_property_setters()

		self.assertEqual(frappe.get_meta("Customer").get_field("customer_primary_address").search_index, 1)

	def test_indexes_customer_primary_contact(self):
		create_contact_enhancements_index_property_setters()

		self.assertEqual(frappe.get_meta("Customer").get_field("customer_primary_contact").search_index, 1)

	def test_idempotent_does_not_duplicate_property_setter(self):
		create_contact_enhancements_index_property_setters()
		create_contact_enhancements_index_property_setters()

		for fieldname in ("customer_primary_address", "customer_primary_contact"):
			count = frappe.db.count(
				"Property Setter",
				{"doc_type": "Customer", "field_name": fieldname, "property": "search_index"},
			)
			self.assertEqual(count, 1)

	def test_actually_creates_the_database_index_not_just_the_property(self):
		# Setting search_index=1 alone does not alter the table - bench
		# migrate's own schema sync skips re-importing a DocType whose JSON
		# file hash is unchanged (frappe/modules/import_file.py), which is
		# exactly this case since this only adds a Property Setter. The
		# function must call frappe.db.updatedb("Customer") itself - assert
		# the index is really there via SHOW INDEX, not just the meta flag.
		create_contact_enhancements_index_property_setters()

		rows = frappe.db.sql("SHOW INDEX FROM `tabCustomer`", as_dict=True)
		indexed_columns = {row.Column_name for row in rows}
		self.assertIn("customer_primary_address", indexed_columns)
		self.assertIn("customer_primary_contact", indexed_columns)


class TestCreateContactFullNamePropertySetters(FrappeTestCase):
	"""create_contact_full_name_property_setters - Phase 0e's relabeling of
	Contact.first_name to "Full Name", and hiding the confirmed-unused
	middle_name field (last_name is deliberately left alone - see that
	function's own docstring for why)."""

	def test_relabels_first_name(self):
		create_contact_full_name_property_setters()
		self.assertEqual(frappe.get_meta("Contact").get_field("first_name").label, "Full Name")

	def test_hides_middle_name(self):
		create_contact_full_name_property_setters()
		self.assertEqual(frappe.get_meta("Contact").get_field("middle_name").hidden, 1)

	def test_does_not_hide_last_name(self):
		# Unlike middle_name, last_name has real, non-blank data on this
		# site - hiding it would make that data invisible in the UI, so
		# this function deliberately never touches it.
		create_contact_full_name_property_setters()
		self.assertFalse(frappe.get_meta("Contact").get_field("last_name").hidden)


class TestCreateContactPhoneHiddenFieldPropertySetters(FrappeTestCase):
	"""create_contact_phone_hidden_field_property_setters - Phase 0f's real
	Property Setter hiding Contact Phone.is_primary_phone, replacing the
	client-side grid.update_docfield_property hack that only ever covered
	the live-edit rendering path, not a saved/reloaded document's static
	row rendering."""

	def test_hides_is_primary_phone(self):
		create_contact_phone_hidden_field_property_setters()
		self.assertEqual(
			frappe.get_meta("Contact Phone").get_field("is_primary_phone").hidden, 1
		)

	def test_does_not_hide_is_primary_mobile_no(self):
		# The field this app's own dialogs and hooks actually read/set -
		# a different field from is_primary_phone, must stay untouched.
		create_contact_phone_hidden_field_property_setters()
		self.assertFalse(
			frappe.get_meta("Contact Phone").get_field("is_primary_mobile_no").hidden
		)


class TestCreateEmployeeFullNamePropertySetters(FrappeTestCase):
	"""create_employee_full_name_property_setters - relabels Employee.
	first_name to "Full Name" (matching Contact.first_name's own Phase 0e
	precedent) and hides the now-redundant native, already-"Full Name"-
	labeled employee_name field."""

	def test_relabels_first_name(self):
		create_employee_full_name_property_setters()
		self.assertEqual(frappe.get_meta("Employee").get_field("first_name").label, "Full Name")

	def test_hides_employee_name(self):
		create_employee_full_name_property_setters()
		self.assertEqual(frappe.get_meta("Employee").get_field("employee_name").hidden, 1)


class TestCreateUserContactPropertySetters(FrappeTestCase):
	"""create_user_contact_property_setters - disables User's own native
	Quick Entry and relabels User.first_name to "Full Name", matching the
	same precedent as Contact/Employee."""

	def test_disables_quick_entry(self):
		create_user_contact_property_setters()
		self.assertEqual(frappe.get_meta("User").as_dict().get("quick_entry"), 0)

	def test_relabels_first_name(self):
		create_user_contact_property_setters()
		self.assertEqual(frappe.get_meta("User").get_field("first_name").label, "Full Name")


class TestCreateSupplierContactPropertySetters(FrappeTestCase):
	"""create_supplier_contact_property_setters (Phase 1) - mirrors
	TestCreateContactEnhancementsPropertySetters's own Customer tests,
	but also proves the two deliberate asymmetries: supplier_primary_
	address stays optional, and Supplier's own native create_primary_
	contact()/create_primary_address() become inert once this runs."""

	def test_makes_supplier_primary_contact_mandatory(self):
		create_supplier_contact_property_setters()
		self.assertEqual(frappe.get_meta("Supplier").get_field("supplier_primary_contact").reqd, 1)

	def test_leaves_supplier_primary_address_optional(self):
		create_supplier_contact_property_setters()
		self.assertFalse(frappe.get_meta("Supplier").get_field("supplier_primary_address").reqd)

	def test_disables_supplier_quick_entry(self):
		create_supplier_contact_property_setters()
		self.assertEqual(frappe.get_meta("Supplier").quick_entry, 0)

	def test_idempotent_does_not_duplicate_property_setter(self):
		create_supplier_contact_property_setters()
		create_supplier_contact_property_setters()

		count = frappe.db.count(
			"Property Setter",
			{"doc_type": "Supplier", "field_name": "supplier_primary_contact", "property": "reqd"},
		)
		self.assertEqual(count, 1)

	def test_native_supplier_creation_still_works_with_a_primary_contact(self):
		# Confirms the mandatory field alone doesn't break an ordinary
		# Supplier insert that already supplies one - the create_primary_
		# contact()/create_primary_address() dead-code claim is about a
		# Supplier that predates having one, not about blocking normal use.
		create_supplier_contact_property_setters()
		contact = make_contact()

		supplier = make_supplier(supplier_primary_contact=contact.name)

		self.assertTrue(supplier.name)
		self.assertEqual(supplier.supplier_primary_contact, contact.name)

	def test_raises_mandatory_error_without_a_primary_contact(self):
		create_supplier_contact_property_setters()
		supplier = frappe.get_doc(
			{
				"doctype": "Supplier",
				"supplier_name": frappe.generate_hash(length=10),
				"supplier_type": "Company",
			}
		)
		self.assertRaises(frappe.MandatoryError, supplier.insert, ignore_permissions=True)
