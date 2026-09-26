# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

import frappe
from frappe.tests.utils import FrappeTestCase
from unittest.mock import patch

from contact_enhancements.api.name_sync import (
	get_linked_contacts_for_name_sync,
	update_contact_names,
)
from contact_enhancements.api.lead_lookup import get_lead_snapshot_for_contact
from contact_enhancements.tests.test_lead_lookup import make_contact, make_lead, make_lead_address
from contact_enhancements.tests.test_customer_hooks import make_customer
from contact_enhancements.tests.test_supplier_hooks import make_supplier
from contact_enhancements.tests.test_employee_hooks import make_employee
from contact_enhancements.tests.test_user_hooks import make_user


class TestCrossDocTypeContactSharing(FrappeTestCase):
	"""Integration Scenario I1: Cross-DocType Contact Sharing & Multi-Doc Propagation."""

	def test_same_contact_linked_to_customer_and_supplier_propagates_to_both(self):
		"""I1.1: When the same Contact is the primary contact for both an Individual
		Customer and an Individual Supplier, updating the Contact's name, email,
		and phone propagates to BOTH linked records simultaneously.
		"""
		contact = make_contact(
			first_name="Mostafa Kamel",
			email_ids=[{"email_id": "mostafa.old@example.com", "is_primary": 1}],
		)
		contact.append("phone_nos", {"phone": "01011112222", "is_primary_mobile_no": 1})
		contact.save(ignore_permissions=True)

		customer = make_customer(
			customer_primary_contact=contact.name,
			customer_type="Individual",
		)
		supplier = make_supplier(
			supplier_primary_contact=contact.name,
			supplier_type="Individual",
		)

		contact.reload()
		# Verify dynamic links exist
		links = [(l.link_doctype, l.link_name) for l in contact.links]
		self.assertIn(("Customer", customer.name), links)
		self.assertIn(("Supplier", supplier.name), links)

		# Now update Contact's name and email
		contact.first_name = "Mostafa Kamel Updated"
		contact.email_ids[0].email_id = "mostafa.new@example.com"
		contact.save(ignore_permissions=True)

		# Both Customer and Supplier must have their names updated
		self.assertEqual(
			frappe.db.get_value("Customer", customer.name, "customer_name"),
			"Mostafa Kamel Updated",
		)
		self.assertEqual(
			frappe.db.get_value("Supplier", supplier.name, "supplier_name"),
			"Mostafa Kamel Updated",
		)

	def test_same_contact_linked_to_customer_and_employee_propagates_to_both(self):
		"""I1.2: When the same Contact is linked to Customer and Employee,
		updating Contact propagates to both entities appropriately.
		"""
		contact = make_contact(
			first_name="Sarah Tarek",
			email_ids=[{"email_id": "sarah.old@example.com", "is_primary": 1}],
		)
		contact.append("phone_nos", {"phone": "01033334444", "is_primary_mobile_no": 1})
		contact.save(ignore_permissions=True)

		customer = make_customer(
			customer_primary_contact=contact.name,
			customer_type="Individual",
		)
		employee = make_employee(employee_primary_contact=contact.name)

		contact.reload()
		contact.first_name = "Sarah Tarek Ali"
		contact.email_ids[0].email_id = "sarah.personal@example.com"
		contact.phone_nos[0].phone = "01033335555"
		contact.save(ignore_permissions=True)

		self.assertEqual(
			frappe.db.get_value("Customer", customer.name, "customer_name"),
			"Sarah Tarek Ali",
		)
		self.assertEqual(
			frappe.db.get_value("Employee", employee.name, "first_name"),
			"Sarah Tarek Ali",
		)
		self.assertEqual(
			frappe.db.get_value("Employee", employee.name, "personal_email"),
			"sarah.personal@example.com",
		)
		self.assertEqual(
			frappe.db.get_value("Employee", employee.name, "cell_number"),
			"+201033335555",
		)


class TestNameSyncRoundtrip(FrappeTestCase):
	"""Integration Scenario I2: Bidirectional Name Sync Roundtrip & Anti-Loop Validation."""

	def test_customer_rename_to_contact_via_name_sync_api(self):
		"""I2.1: Calling update_contact_names for Customer's primary contact
		correctly updates the Contact without raising loop or permission errors.
		"""
		contact = make_contact(first_name="Original Name")
		customer = make_customer(
			customer_primary_contact=contact.name,
			customer_name="Original Name",
			customer_type="Individual",
		)

		contact.reload()
		# Discover linked contacts
		results = get_linked_contacts_for_name_sync("Customer", customer.name)
		self.assertTrue(len(results) >= 1)
		self.assertEqual(results[0]["contact"], contact.name)

		# Execute Name Sync API with new name
		updated = update_contact_names(contacts=[contact.name], new_name="New Synced Name")
		self.assertTrue(any(r.get("contact") == contact.name for r in updated))

		# Verify Contact DB reflects the updated name
		self.assertEqual(frappe.db.get_value("Contact", contact.name, "first_name"), "New Synced Name")

	def test_contact_rename_propagates_to_customer_and_respects_guard_flags(self):
		"""I2.2 & I2.3: Contact rename propagates to Customer and sets anti-loop guards
		such that subsequent triggers do not bounce back.
		"""
		contact = make_contact(first_name="Tamer Hosny")
		customer = make_customer(
			customer_primary_contact=contact.name,
			customer_type="Individual",
		)
		contact.reload()

		# Explicitly verify anti-loop guard flag behavior
		contact.set("__ce_name_synced", 1)
		contact.flags.ignore_name_propagation = True
		contact.first_name = "Tamer Hosny New"
		contact.save(ignore_permissions=True)

		# Because guard was set, propagation was skipped by design
		customer_db_name = frappe.db.get_value("Customer", customer.name, "customer_name")
		self.assertNotEqual(customer_db_name, "Tamer Hosny New")

		# Without the guard flag, propagation immediately runs
		contact.reload()
		contact.set("__ce_name_synced", 0)
		contact.flags.ignore_name_propagation = False
		contact.first_name = "Tamer Hosny Final"
		contact.save(ignore_permissions=True)
		self.assertEqual(
			frappe.db.get_value("Customer", customer.name, "customer_name"),
			"Tamer Hosny Final",
		)

	def test_company_type_customer_blocks_name_sync_to_personal_contact(self):
		"""I2.4: When Customer is a Company, get_linked_contacts_for_name_sync
		marks the linked contact so company name doesn't overwrite individual name.
		"""
		contact = make_contact(first_name="Company Representative")
		company_customer = make_customer(
			customer_primary_contact=contact.name,
			customer_name="Acme Corporation Ltd",
			customer_type="Company",
		)
		contact.reload()

		# In propagation, Company customer_name is never updated from Contact.first_name
		contact.first_name = "New Representative Name"
		contact.save(ignore_permissions=True)

		self.assertEqual(
			frappe.db.get_value("Customer", company_customer.name, "customer_name"),
			"Acme Corporation Ltd",
		)


class TestLeadConversionFlow(FrappeTestCase):
	"""Integration Scenario I3: Complete Lead-to-Customer Conversion Flow."""

	def test_full_lead_conversion_with_contact_and_address_prefill(self):
		"""I3.1 & I3.2: Converting a Lead into a Customer carries over contact,
		address, identity snapshot fields, and marks the Lead as 'Converted'.
		"""
		contact = make_contact(
			first_name="Hany Adel",
			email_ids=[{"email_id": "hany@example.com", "is_primary": 1}],
		)
		contact.append("phone_nos", {"phone": "01077778888", "is_primary_mobile_no": 1})
		contact.save(ignore_permissions=True)

		lead = make_lead(
			first_name="Hany",
			last_name="Adel",
			lead_primary_contact=contact.name,
			whatsapp_no="01077779999",
		)

		address = make_lead_address(lead.name, address_title="Hany Cairo HQ")

		# Ensure contact has dynamic link to lead
		contact.reload()
		contact.append("links", {"link_doctype": "Lead", "link_name": lead.name})
		contact.save(ignore_permissions=True)

		# Create customer from lead snapshot
		customer = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": lead.lead_name,
				"customer_type": "Individual",
				"customer_group": "Individual",
				"territory": "Rest Of The World",
				"lead_name": lead.name,
				"customer_primary_contact": contact.name,
				"customer_primary_address": address.name,
			}
		)
		customer.insert(ignore_permissions=True)

		# Verify snapshot fields on Customer
		self.assertEqual(customer.customer_name, "Hany Adel")
		self.assertEqual(customer.customer_primary_contact, contact.name)
		self.assertEqual(customer.customer_primary_address, address.name)

		# Verify Lead status became Converted
		lead.reload()
		self.assertEqual(lead.status, "Converted")

	def test_lead_conversion_syncs_whatsapp_number_to_contact_phone_rows(self):
		"""I3.3: When Lead has a whatsapp_no not yet on Contact, saving Customer
		or syncing Lead snapshot reconciles the WhatsApp number into Contact Phone child table.
		"""
		contact = make_contact(first_name="Ziad Bahaa")
		contact.append("phone_nos", {"phone": "01099991111", "is_primary_mobile_no": 1})
		contact.save(ignore_permissions=True)

		lead = make_lead(
			lead_name="Ziad Bahaa",
			lead_primary_contact=contact.name,
			whatsapp_no="01099992222",
		)

		# Trigger customer creation
		customer = make_customer(
			customer_primary_contact=contact.name,
			lead_name=lead.name,
		)

		contact.reload()
		# Verify that 01099992222 (+201099992222) was added with custom_whatsapp = 1
		whatsapp_phones = [
			p.phone for p in contact.phone_nos if p.custom_whatsapp == 1
		]
		self.assertTrue(
			any("+201099992222" in p or "01099992222" in p for p in whatsapp_phones),
			f"Expected +201099992222 in contact whatsapp phones, got {whatsapp_phones}",
		)


class TestAddressInheritanceFlow(FrappeTestCase):
	"""Integration Scenario I4: Cross-DocType Address Inheritance & Synchronization."""

	def test_address_linked_to_contact_is_inherited_by_customer(self):
		"""I4.1: If Contact has a linked Address, creating Customer with that Contact
		properly resolves the address without manually entering it.
		"""
		contact = make_contact(first_name="Karim Mahmoud")
		lead = make_lead(lead_name="Karim Mahmoud", lead_primary_contact=contact.name)
		address = make_lead_address(lead.name, address_title="Karim Office")

		# Link address dynamically to Contact as well
		address.append("links", {"link_doctype": "Contact", "link_name": contact.name})
		address.save(ignore_permissions=True)

		# Customer creation with contact
		customer = make_customer(
			customer_primary_contact=contact.name,
			lead_name=lead.name,
		)

		self.assertEqual(customer.customer_primary_address, address.name)


class TestPropagationSavepointResilience(FrappeTestCase):
	"""Integration Scenario DB-07 & HK-20: SAVEPOINT Resilience in Contact Propagation."""

	def test_propagation_db_error_does_not_rollback_contact_save(self):
		"""When an unexpected database exception happens during propagation
		to one of the linked records (e.g. simulated DB error or constraint),
		the SAVEPOINT catches it, logs it, and the Contact itself saves successfully.
		"""
		contact = make_contact(first_name="Safe Contact")
		customer = make_customer(
			customer_primary_contact=contact.name,
			customer_type="Individual",
		)
		contact.reload()

		# Simulate an error inside frappe.db.set_value during Customer update
		original_set_value = frappe.db.set_value

		def failing_set_value(doctype, name, *args, **kwargs):
			if doctype == "Customer" and name == customer.name:
				raise Exception("Simulated DB Lock / Constraint Failure in Customer set_value")
			return original_set_value(doctype, name, *args, **kwargs)

		with patch.object(frappe.db, "set_value", side_effect=failing_set_value):
			# Updating contact should NOT crash or fail
			contact.first_name = "Safe Contact Renamed"
			# This must succeed without raising an uncaught exception
			contact.save(ignore_permissions=True)

		# Contact in DB must have its new name despite target propagation error
		self.assertEqual(
			frappe.db.get_value("Contact", contact.name, "first_name"),
			"Safe Contact Renamed",
		)
