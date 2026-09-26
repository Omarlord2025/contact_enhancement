# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from contact_enhancements.api.name_sync import (
	get_linked_contacts_for_name_sync,
	update_contact_names,
)
from contact_enhancements.tests.test_customer_hooks import make_customer
from contact_enhancements.tests.test_employee_hooks import make_employee
from contact_enhancements.tests.test_lead_lookup import make_contact, make_lead
from contact_enhancements.tests.test_supplier_hooks import make_supplier


class TestNameSyncApi(FrappeTestCase):
	def test_get_linked_contacts_unsupported_doctype(self):
		self.assertEqual(get_linked_contacts_for_name_sync("Item", "NONEXISTENT"), [])
		self.assertEqual(get_linked_contacts_for_name_sync("Quotation", "NONEXISTENT"), [])

	def test_get_linked_contacts_empty_when_no_contacts(self):
		customer = make_customer()
		results = get_linked_contacts_for_name_sync("Customer", customer.name)
		self.assertEqual(results, [])

	def test_get_linked_contacts_discovery_and_primary(self):
		contact1 = make_contact(first_name="Primary Person")
		contact2 = make_contact(first_name="Secondary Person")

		customer = make_customer(customer_primary_contact=contact1.name)

		# Link both contacts to customer
		contact1.append("links", {"link_doctype": "Customer", "link_name": customer.name})
		contact1.save(ignore_permissions=True)

		contact2.append("links", {"link_doctype": "Customer", "link_name": customer.name})
		contact2.save(ignore_permissions=True)

		results = get_linked_contacts_for_name_sync("Customer", customer.name)
		self.assertEqual(len(results), 2)

		# Primary contact should come first
		self.assertTrue(results[0]["is_primary"])
		self.assertEqual(results[0]["contact"], contact1.name)
		self.assertFalse(results[1]["is_primary"])
		self.assertEqual(results[1]["contact"], contact2.name)

	def test_get_linked_contacts_surfaces_other_links_and_excludes_origin(self):
		contact = make_contact(first_name="Multi Linked Person")
		customer = make_customer(customer_primary_contact=contact.name)
		supplier_ind = make_supplier(supplier_type="Individual")
		supplier_co = make_supplier(supplier_type="Company")

		contact.append("links", {"link_doctype": "Customer", "link_name": customer.name})
		contact.append("links", {"link_doctype": "Supplier", "link_name": supplier_ind.name})
		contact.append("links", {"link_doctype": "Supplier", "link_name": supplier_co.name})
		contact.save(ignore_permissions=True)

		results = get_linked_contacts_for_name_sync("Customer", customer.name)
		self.assertEqual(len(results), 1)

		links = results[0]["links"]
		# Origin customer should NOT be in the links list
		self.assertNotIn(customer.name, [l["name"] for l in links])

		# Check supplier links and is_individual classification
		ind_link = next(l for l in links if l["name"] == supplier_ind.name)
		self.assertTrue(ind_link["is_individual"])

		co_link = next(l for l in links if l["name"] == supplier_co.name)
		self.assertFalse(co_link["is_individual"])

	def test_get_linked_contacts_for_employee(self):
		contact = make_contact(first_name="Employee Person")
		employee = make_employee(employee_primary_contact=contact.name)

		contact = frappe.get_doc("Contact", contact.name)
		if not any(l.link_doctype == "Employee" and l.link_name == employee.name for l in contact.links):
			contact.append("links", {"link_doctype": "Employee", "link_name": employee.name})
			contact.save(ignore_permissions=True)

		results = get_linked_contacts_for_name_sync("Employee", employee.name)
		self.assertEqual(len(results), 1)
		self.assertEqual(results[0]["contact"], contact.name)
		self.assertTrue(results[0]["is_primary"])

	def test_get_linked_contacts_for_lead(self):
		contact = make_contact(first_name="Lead Person")
		lead = make_lead(lead_primary_contact=contact.name)

		contact = frappe.get_doc("Contact", contact.name)
		if not any(l.link_doctype == "Lead" and l.link_name == lead.name for l in contact.links):
			contact.append("links", {"link_doctype": "Lead", "link_name": lead.name})
			contact.save(ignore_permissions=True)

		results = get_linked_contacts_for_name_sync("Lead", lead.name)
		self.assertEqual(len(results), 1)
		self.assertEqual(results[0]["contact"], contact.name)
		self.assertTrue(results[0]["is_primary"])

	def test_get_linked_contacts_for_contact(self):
		contact = make_contact(first_name="Direct Contact Person")
		customer = make_customer(customer_primary_contact=contact.name)
		employee = make_employee(employee_primary_contact=contact.name)

		contact = frappe.get_doc("Contact", contact.name)
		if not any(l.link_doctype == "Customer" and l.link_name == customer.name for l in contact.links):
			contact.append("links", {"link_doctype": "Customer", "link_name": customer.name})
		if not any(l.link_doctype == "Employee" and l.link_name == employee.name for l in contact.links):
			contact.append("links", {"link_doctype": "Employee", "link_name": employee.name})
		contact.save(ignore_permissions=True)

		results = get_linked_contacts_for_name_sync("Contact", contact.name)
		self.assertEqual(len(results), 1)
		self.assertEqual(results[0]["contact"], contact.name)
		links = results[0]["links"]
		self.assertEqual(len(links), 2)
		link_doctypes = {l["doctype"] for l in links}
		self.assertIn("Customer", link_doctypes)
		self.assertIn("Employee", link_doctypes)

	def test_update_contact_names_validation(self):
		with self.assertRaises(frappe.ValidationError):
			update_contact_names([], "")

		with self.assertRaises(frappe.ValidationError):
			update_contact_names(["nonexistent"], "   ")

	def test_update_contact_names_success_and_normalization(self):
		contact = make_contact(first_name="Old Name")
		self.assertEqual(contact.first_name, "Old Name")

		results = update_contact_names([contact.name], "أحمد علي")
		self.assertEqual(len(results), 1)
		self.assertEqual(results[0]["status"], "updated")
		self.assertEqual(results[0]["contact"], contact.name)

		# Reload from DB and verify normalization (أ -> ا, word-final ي -> ى)
		updated_first, updated_full = frappe.db.get_value(
			"Contact", contact.name, ["first_name", "full_name"]
		)
		self.assertEqual(updated_first, "احمد على")
		self.assertEqual(updated_full, "احمد على")

	def test_update_contact_and_linked_records(self):
		contact = make_contact(first_name="Base Contact")
		supplier = make_supplier(supplier_name="Old Supplier", supplier_type="Individual")
		lead = make_lead(first_name="Old Lead First")

		results = update_contact_names(
			contacts=[contact.name],
			new_name="New Unified Name",
			linked_records=[
				{"doctype": "Supplier", "name": supplier.name},
				{"doctype": "Lead", "name": lead.name},
			],
		)

		self.assertEqual(len(results), 3)
		self.assertTrue(all(r["status"] == "updated" for r in results))

		# Verify Contact was updated
		c_name = frappe.db.get_value("Contact", contact.name, "full_name")
		self.assertEqual(c_name, "New Unified Name")

		# Verify Supplier was updated
		s_name = frappe.db.get_value("Supplier", supplier.name, "supplier_name")
		self.assertEqual(s_name, "New Unified Name")

		# Verify Lead was updated
		l_first = frappe.db.get_value("Lead", lead.name, "first_name")
		self.assertEqual(l_first, "New Unified Name")

