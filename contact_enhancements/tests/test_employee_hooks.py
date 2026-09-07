"""Tests for contact_enhancements.employee_hooks (Phase 3) - wholly new,
optional employee_primary_contact/employee_primary_address fields, backed
by a proactive-but-dismissible contact-picker dialog (public/js/
employee.js), matching the same UX Customer/Supplier use."""

import frappe
from frappe.tests.utils import FrappeTestCase

from contact_enhancements.employee_hooks import (
	enforce_primary_contact_on_new_employee,
	link_employee_contact,
	sync_employee_address_from_contact_links,
	sync_employee_contact_from_user,
)
from contact_enhancements.tests.test_customer_hooks import make_customer
from contact_enhancements.tests.test_lead_lookup import make_contact
from contact_enhancements.tests.test_supplier_hooks import make_address


def make_employee(**kwargs):
	employee = frappe.get_doc(
		{
			"doctype": "Employee",
			"first_name": frappe.generate_hash(length=10),
			"gender": "Prefer not to say",
			"date_of_birth": "1990-01-01",
			"date_of_joining": "2020-01-01",
			**kwargs,
		}
	)
	employee.flags.ignore_mandatory = True
	employee.insert(ignore_permissions=True)
	return employee


def make_user(**kwargs):
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": frappe.generate_hash(length=8) + "@example.com",
			"first_name": "Test",
			"send_welcome_email": 0,
			**kwargs,
		}
	)
	user.flags.ignore_mandatory = True
	user.insert(ignore_permissions=True)
	return user


class TestSyncEmployeeContactFromUser(FrappeTestCase):
	def test_backfills_from_the_linked_users_own_contact(self):
		contact = make_contact(email_ids=[{"email_id": "emp-sync-user@example.com", "is_primary": 1}])
		user = make_user(email="emp-sync-user@example.com")
		employee = make_employee(user_id=user.name)
		employee.employee_primary_contact = None

		sync_employee_contact_from_user(employee)

		self.assertEqual(employee.employee_primary_contact, contact.name)

	def test_never_creates_a_contact(self):
		# Native User.on_update itself already enqueues create_contact() for
		# every new User (confirmed: runs inline under frappe.flags.in_test),
		# so a Contact matching this email may already exist by the time
		# this hook runs - the guarantee this test actually cares about is
		# that this hook itself never creates one; it only ever reuses
		# whatever get_contact_name() finds.
		user = make_user()
		employee = make_employee(user_id=user.name)
		contacts_before = frappe.db.count("Contact")

		sync_employee_contact_from_user(employee)  # must not raise

		self.assertEqual(frappe.db.count("Contact"), contacts_before)

	def test_noop_without_a_user_id(self):
		employee = make_employee()
		sync_employee_contact_from_user(employee)  # must not raise
		self.assertFalse(employee.employee_primary_contact)

	def test_never_overwrites_an_already_set_contact(self):
		other_contact = make_contact()
		contact = make_contact(email_ids=[{"email_id": "emp-sync-user2@example.com", "is_primary": 1}])
		user = make_user(email="emp-sync-user2@example.com")
		employee = make_employee(user_id=user.name, employee_primary_contact=other_contact.name)

		sync_employee_contact_from_user(employee)

		self.assertEqual(employee.employee_primary_contact, other_contact.name)
		self.assertNotEqual(employee.employee_primary_contact, contact.name)


class TestSyncEmployeeAddressFromContactLinks(FrappeTestCase):
	def test_backfills_address_from_a_customer_sharing_the_same_contact(self):
		contact = make_contact()
		address = make_address()
		make_customer(customer_primary_contact=contact.name, customer_primary_address=address.name)
		employee = make_employee(employee_primary_contact=contact.name)

		sync_employee_address_from_contact_links(employee)

		self.assertEqual(employee.employee_primary_address, address.name)

	def test_never_overwrites_an_already_set_address(self):
		contact = make_contact()
		customer_address = make_address()
		employee_address = make_address()
		make_customer(customer_primary_contact=contact.name, customer_primary_address=customer_address.name)
		employee = make_employee(employee_primary_contact=contact.name, employee_primary_address=employee_address.name)

		sync_employee_address_from_contact_links(employee)

		self.assertEqual(employee.employee_primary_address, employee_address.name)

	def test_noop_without_an_employee_primary_contact(self):
		employee = make_employee()
		sync_employee_address_from_contact_links(employee)  # must not raise
		self.assertFalse(employee.employee_primary_address)

	def test_stays_blank_when_nothing_in_the_chain_resolves(self):
		contact = make_contact()
		employee = make_employee(employee_primary_contact=contact.name)

		sync_employee_address_from_contact_links(employee)

		self.assertFalse(employee.employee_primary_address)


class TestLinkEmployeeContact(FrappeTestCase):
	def test_links_the_contact_back_to_the_employee(self):
		contact = make_contact()
		employee = make_employee(employee_primary_contact=contact.name)

		link_employee_contact(employee)

		contact.reload()
		self.assertTrue(contact.has_link("Employee", employee.name))

	def test_links_the_address_back_to_the_employee(self):
		address = make_address()
		contact = make_contact()
		employee = make_employee(employee_primary_contact=contact.name, employee_primary_address=address.name)

		link_employee_contact(employee)

		address.reload()
		self.assertTrue(address.has_link("Employee", employee.name))

	def test_noop_without_a_primary_contact(self):
		employee = make_employee()
		link_employee_contact(employee)  # must not raise


class TestPrimaryContactRequirementIsGrandfathered(FrappeTestCase):
	"""Mirrors test_customer_hooks.TestPrimaryContactRequirementIsGrandfathered
	exactly - employee_primary_contact is required from now on, never
	retroactively. A static reqd=1 would be evaluated on every save, not
	just inserts, and would freeze every Employee that predates this
	requirement - see enforce_primary_contact_on_new_employee's own
	docstring."""

	def test_a_new_employee_still_needs_a_contact(self):
		doc = frappe.get_doc(
			{
				"doctype": "Employee",
				"first_name": frappe.generate_hash(length=10),
				"gender": "Prefer not to say",
				"date_of_birth": "1990-01-01",
				"date_of_joining": "2020-01-01",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_an_existing_employee_without_one_stays_editable(self):
		employee = make_employee()  # make_employee sets ignore_mandatory itself
		self.assertFalse(employee.employee_primary_contact)
		employee.save(ignore_permissions=True)  # must not raise

	def test_the_field_is_not_statically_mandatory(self):
		# Guards against a reqd Property Setter creeping back in - that is
		# exactly what would make the requirement retroactive.
		meta = frappe.get_meta("Employee")
		self.assertFalse(meta.get_field("employee_primary_contact").reqd)

	def test_a_new_employee_with_a_contact_saves_normally(self):
		contact = make_contact()
		employee = frappe.get_doc(
			{
				"doctype": "Employee",
				"first_name": frappe.generate_hash(length=10),
				"gender": "Prefer not to say",
				"date_of_birth": "1990-01-01",
				"date_of_joining": "2020-01-01",
				"employee_primary_contact": contact.name,
			}
		)
		employee.insert(ignore_permissions=True)
		self.assertTrue(employee.name)

	def test_ignore_mandatory_bypasses_the_check(self):
		employee = frappe.get_doc(
			{
				"doctype": "Employee",
				"first_name": frappe.generate_hash(length=10),
				"gender": "Prefer not to say",
				"date_of_birth": "1990-01-01",
				"date_of_joining": "2020-01-01",
			}
		)
		employee.flags.ignore_mandatory = True
		enforce_primary_contact_on_new_employee(employee)  # must not raise
