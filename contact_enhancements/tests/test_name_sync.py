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

	def test_get_linked_contacts_surfaces_source_address(self):
		contact = make_contact(first_name="Address Source Contact")
		customer = make_customer(customer_primary_contact=contact.name)

		# Link contact to customer
		contact.append("links", {"link_doctype": "Customer", "link_name": customer.name})
		contact.save(ignore_permissions=True)

		# Create address linked to customer
		addr = frappe.new_doc("Address")
		addr.address_title = "Customer Headquarters"
		addr.address_line1 = "10 Street"
		addr.city = "Cairo"
		addr.country = "Egypt"
		addr.append("links", {"link_doctype": "Customer", "link_name": customer.name})
		addr.insert(ignore_permissions=True)

		frappe.db.set_value("Customer", customer.name, "customer_primary_address", addr.name)

		results = get_linked_contacts_for_name_sync("Customer", customer.name)
		self.assertEqual(len(results), 1)
		self.assertEqual(results[0]["source_address"], addr.name)
		self.assertEqual(results[0]["source_address_title"], "Customer Headquarters")

	def test_update_contact_names_propagates_address_and_creates_dynamic_links(self):
		contact = make_contact(first_name="No Address Contact")
		supplier = make_supplier(supplier_name="No Address Supplier", supplier_type="Individual")
		customer = make_customer(customer_name="Source Customer")

		# Create address linked to customer
		addr = frappe.new_doc("Address")
		addr.address_title = "Central Office"
		addr.address_line1 = "456 Pyramid St"
		addr.city = "Giza"
		addr.country = "Egypt"
		addr.append("links", {"link_doctype": "Customer", "link_name": customer.name})
		addr.insert(ignore_permissions=True)
		frappe.db.set_value("Customer", customer.name, "customer_primary_address", addr.name)

		self.assertFalse(frappe.db.get_value("Contact", contact.name, "address"))
		self.assertFalse(frappe.db.get_value("Supplier", supplier.name, "supplier_primary_address"))

		update_contact_names(
			contacts=[contact.name],
			new_name="Synced Name",
			linked_records=[{"doctype": "Supplier", "name": supplier.name}],
			sync_address=True,
			source_address=addr.name,
		)

		# Verify address fields populated
		self.assertEqual(frappe.db.get_value("Contact", contact.name, "address"), addr.name)
		self.assertEqual(frappe.db.get_value("Supplier", supplier.name, "supplier_primary_address"), addr.name)

		# Verify Dynamic Links created
		contact_link = frappe.db.exists(
			"Dynamic Link",
			{"parenttype": "Address", "parent": addr.name, "link_doctype": "Contact", "link_name": contact.name},
		)
		supplier_link = frappe.db.exists(
			"Dynamic Link",
			{"parenttype": "Address", "parent": addr.name, "link_doctype": "Supplier", "link_name": supplier.name},
		)
		self.assertTrue(contact_link)
		self.assertTrue(supplier_link)

	def test_update_contact_names_does_not_propagate_address_when_disabled(self):
		contact = make_contact(first_name="No Sync Contact")
		supplier = make_supplier(supplier_name="No Sync Supplier", supplier_type="Individual")

		addr = frappe.new_doc("Address")
		addr.address_title = "Excluded Office"
		addr.address_line1 = "789 Nile Corniche"
		addr.city = "Cairo"
		addr.country = "Egypt"
		addr.insert(ignore_permissions=True)

		update_contact_names(
			contacts=[contact.name],
			new_name="Name Without Address",
			linked_records=[{"doctype": "Supplier", "name": supplier.name}],
			sync_address=False,
			source_address=addr.name,
		)

		self.assertFalse(frappe.db.get_value("Contact", contact.name, "address"))
		self.assertFalse(frappe.db.get_value("Supplier", supplier.name, "supplier_primary_address"))

	def test_update_contact_names_does_not_overwrite_existing_address(self):
		contact = make_contact(first_name="Existing Addr Contact")
		supplier = make_supplier(supplier_name="Existing Addr Supplier", supplier_type="Individual")

		addr_existing = frappe.new_doc("Address")
		addr_existing.address_title = "Supplier's Own Office"
		addr_existing.address_line1 = "1 Existing Rd"
		addr_existing.city = "Alexandria"
		addr_existing.country = "Egypt"
		addr_existing.insert(ignore_permissions=True)
		frappe.db.set_value("Supplier", supplier.name, "supplier_primary_address", addr_existing.name)

		addr_source = frappe.new_doc("Address")
		addr_source.address_title = "Source Office"
		addr_source.address_line1 = "2 Source Rd"
		addr_source.city = "Cairo"
		addr_source.country = "Egypt"
		addr_source.insert(ignore_permissions=True)

		update_contact_names(
			contacts=[contact.name],
			new_name="Another Synced Name",
			linked_records=[{"doctype": "Supplier", "name": supplier.name}],
			sync_address=True,
			source_address=addr_source.name,
		)

		# Supplier's address should remain unchanged (not overwritten)
		self.assertEqual(frappe.db.get_value("Supplier", supplier.name, "supplier_primary_address"), addr_existing.name)
		# Contact had no address, so it received addr_source
		self.assertEqual(frappe.db.get_value("Contact", contact.name, "address"), addr_source.name)

	def test_get_linked_contacts_surfaces_address_from_linked_customer_when_contact_has_none(self):
		"""Regression: when the dialog is opened from a Contact form
		(doctype="Contact"), source_address must be discovered from the
		Contact's linked Customer if the Contact itself has no address.
		Before the fix, source_address was always empty from this entry
		point and the sync checkbox never appeared."""
		contact = make_contact(first_name="Contact With No Address")
		customer = make_customer(customer_name="Addr Via Customer", customer_primary_contact=contact.name)
		contact.append("links", {"link_doctype": "Customer", "link_name": customer.name})
		contact.save(ignore_permissions=True)

		# Contact has no address; Customer does.
		addr = frappe.new_doc("Address")
		addr.address_title = "Customer Office Only"
		addr.address_line1 = "99 Delta Rd"
		addr.city = "Cairo"
		addr.country = "Egypt"
		addr.append("links", {"link_doctype": "Customer", "link_name": customer.name})
		addr.insert(ignore_permissions=True)
		frappe.db.set_value("Customer", customer.name, "customer_primary_address", addr.name)

		# Dialog opened from the Contact form, not the Customer form.
		results = get_linked_contacts_for_name_sync("Contact", contact.name)
		self.assertTrue(results)
		self.assertEqual(results[0]["source_address"], addr.name)
		self.assertEqual(results[0]["source_address_title"], "Customer Office Only")

	def test_get_linked_contacts_surfaces_linked_addresses(self):
		contact = make_contact(first_name="Addr Test Contact")
		customer = make_customer(customer_name="Addr Test Customer", customer_primary_contact=contact.name)
		contact.append("links", {"link_doctype": "Customer", "link_name": customer.name})
		contact.save(ignore_permissions=True)

		addr = frappe.new_doc("Address")
		addr.address_title = "Old Address Title"
		addr.address_type = "Office"
		addr.address_line1 = "42 Street"
		addr.city = "Cairo"
		addr.country = "Egypt"
		addr.append("links", {"link_doctype": "Customer", "link_name": customer.name})
		addr.insert(ignore_permissions=True)
		frappe.db.set_value("Customer", customer.name, "customer_primary_address", addr.name)

		results = get_linked_contacts_for_name_sync("Customer", customer.name)
		self.assertTrue(results)
		self.assertIn("linked_addresses", results[0])
		addrs = results[0]["linked_addresses"]
		self.assertTrue(any(a["name"] == addr.name for a in addrs))
		matched = next(a for a in addrs if a["name"] == addr.name)
		self.assertEqual(matched["address_title"], "Old Address Title")
		self.assertEqual(matched["address_type"], "Office")

	def test_get_linked_contacts_when_no_contacts_but_has_address(self):
		customer = make_customer(customer_name="No Contact Customer")

		addr = frappe.new_doc("Address")
		addr.address_title = "Sole Address"
		addr.address_type = "Billing"
		addr.address_line1 = "10 Street"
		addr.city = "Giza"
		addr.country = "Egypt"
		addr.append("links", {"link_doctype": "Customer", "link_name": customer.name})
		addr.insert(ignore_permissions=True)
		frappe.db.set_value("Customer", customer.name, "customer_primary_address", addr.name)

		results = get_linked_contacts_for_name_sync("Customer", customer.name)
		self.assertEqual(len(results), 1)
		self.assertIsNone(results[0]["contact"])
		self.assertTrue(results[0]["linked_addresses"])
		self.assertEqual(results[0]["linked_addresses"][0]["name"], addr.name)

	def test_update_contact_names_updates_address_title(self):
		addr1 = frappe.new_doc("Address")
		addr1.address_title = "Original Title 1"
		addr1.address_line1 = "11 Main St"
		addr1.city = "Cairo"
		addr1.country = "Egypt"
		addr1.insert(ignore_permissions=True)

		addr2 = frappe.new_doc("Address")
		addr2.address_title = "Original Title 2"
		addr2.address_line1 = "22 Main St"
		addr2.city = "Cairo"
		addr2.country = "Egypt"
		addr2.insert(ignore_permissions=True)

		# Update only addr1, leaving addr2 untouched
		results = update_contact_names(
			new_name="Renamed Company",
			linked_addresses=[addr1.name],
		)

		self.assertEqual(frappe.db.get_value("Address", addr1.name, "address_title"), "Renamed Company")
		self.assertEqual(frappe.db.get_value("Address", addr2.name, "address_title"), "Original Title 2")
		self.assertTrue(any(r["doctype"] == "Address" and r["name"] == addr1.name and r["status"] == "updated" for r in results))


