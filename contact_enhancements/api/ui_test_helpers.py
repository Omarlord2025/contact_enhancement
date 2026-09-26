# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

import frappe
from frappe import _


@frappe.whitelist()
def setup_ui_test_data():
	"""Sets up clean test fixtures for Cypress UI testing.
	Creates:
	- A Contact with Egyptian mobile and WhatsApp channel flag.
	- An Individual Customer with linked primary contact.
	- A Company Customer with linked primary contact.
	"""
	results = {}

	# 1. Test Contact for picker search
	phone_val = "+201099887766"
	contact_name = frappe.db.get_value("Contact Phone", {"phone": phone_val}, "parent")
	if not contact_name:
		contact = frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": "Cypress Test User",
				"email_ids": [{"email_id": "cypress.test@example.com", "is_primary": 1}],
				"phone_nos": [
					{
						"phone": "01099887766",
						"country": "Egypt",
						"is_primary_mobile_no": 1,
						"custom_whatsapp": 1,
					}
				],
			}
		)
		contact.insert(ignore_permissions=True)
		contact_name = contact.name
	results["contact"] = contact_name

	# 2. Individual Customer for Name Sync testing
	cust_name = "Cypress Individual Cust"
	if not frappe.db.exists("Customer", {"customer_name": cust_name}):
		ind_contact = frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": cust_name,
				"phone_nos": [
					{
						"phone": "01099887755",
						"country": "Egypt",
						"is_primary_mobile_no": 1,
					}
				],
			}
		)
		ind_contact.insert(ignore_permissions=True)
		customer = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": cust_name,
				"customer_type": "Individual",
				"customer_group": "Individual",
				"territory": "Rest Of The World",
				"customer_primary_contact": ind_contact.name,
			}
		)
		customer.flags.ignore_mandatory = True
		customer.insert(ignore_permissions=True)
		results["individual_customer"] = customer.name
	else:
		results["individual_customer"] = frappe.db.get_value("Customer", {"customer_name": cust_name}, "name")

	# 3. Company Customer for Name Sync warning testing
	company_name = "Cypress Enterprise Corp"
	if not frappe.db.exists("Customer", {"customer_name": company_name}):
		comp_contact = frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": "Enterprise Contact Person",
				"phone_nos": [
					{
						"phone": "01099887744",
						"country": "Egypt",
						"is_primary_mobile_no": 1,
					}
				],
			}
		)
		comp_contact.insert(ignore_permissions=True)
		customer_comp = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": company_name,
				"customer_type": "Company",
				"customer_group": "Commercial",
				"territory": "Rest Of The World",
				"customer_primary_contact": comp_contact.name,
			}
		)
		customer_comp.flags.ignore_mandatory = True
		customer_comp.insert(ignore_permissions=True)
		results["company_customer"] = customer_comp.name
	else:
		results["company_customer"] = frappe.db.get_value("Customer", {"customer_name": company_name}, "name")

	frappe.db.commit()
	return results
