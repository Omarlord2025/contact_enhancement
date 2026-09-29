# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

import frappe
from frappe import _

from contact_enhancements.utils import (
	_insert_dynamic_link,
	dynamic_link_lookup,
	ensure_contact_linked_to_parent,
	resolve_address_from_contact_links,
)


@frappe.whitelist()
def get_party_and_contact_details_for_opportunity(contact_name, company_name=None):
	"""Resolve or create an Opportunity party (Customer or Lead) and return contact details
	for the given Contact.

	Used by the Opportunity fast data entry / contact-picker dialog and the contact_person
	change handler in public/js/opportunity.js.

	1. Checks read permission on Contact.
	2. Resolves phone, mobile, WhatsApp, and email from the Contact.
	3. Checks if this Contact is already Dynamic-Linked to a Customer:
	   - If yes: opportunity_from="Customer", party_name=Customer.
	4. Else checks if linked to a Lead:
	   - If yes: opportunity_from="Lead", party_name=Lead.
	5. If linked to neither:
	   - Creates a new minimal Lead linked to this Contact so Opportunity's mandatory
	     party_name is satisfied, setting opportunity_from="Lead", party_name=new_lead.
	6. Resolves primary address and formatted address display.

	Args:
		contact_name: Name of the Contact record.
		company_name: Optional organization/company name provided in the dialog.

	Returns:
		dict with opportunity_from, party_name, customer_name, customer_group, territory,
		contact_person, contact_mobile, contact_email, phone, whatsapp, customer_address,
		and address_display.
	"""
	if not contact_name or not frappe.db.exists("Contact", contact_name):
		return None

	frappe.has_permission("Contact", "read", doc=contact_name, throw=True)
	contact = frappe.get_doc("Contact", contact_name)

	# 1. Resolve phone numbers and channels
	mobile_no = None
	phone = None
	whatsapp_no = None

	for row in contact.phone_nos:
		if getattr(row, "custom_whatsapp", 0) and not whatsapp_no:
			whatsapp_no = row.phone

		if getattr(row, "is_primary_mobile_no", 0):
			mobile_no = row.phone
		elif not mobile_no and not getattr(row, "custom_landline", 0):
			mobile_no = row.phone

		if getattr(row, "is_primary_phone", 0) or getattr(row, "custom_landline", 0):
			phone = row.phone

	# Fallbacks for mobile & whatsapp
	if not mobile_no and contact.phone_nos:
		mobile_no = contact.phone_nos[0].phone
	if not whatsapp_no:
		whatsapp_no = mobile_no

	# 2. Resolve primary email
	email_id = None
	for row in contact.email_ids:
		if row.is_primary:
			email_id = row.email_id
			break
	if not email_id and contact.email_ids:
		email_id = contact.email_ids[0].email_id
	if not email_id:
		email_id = contact.email_id

	# 3. Resolve party: Customer first, then Lead, or auto-create Lead
	customer = dynamic_link_lookup(
		{"parenttype": "Contact", "parent": contact_name, "link_doctype": "Customer"},
		"link_name",
	)
	lead = dynamic_link_lookup(
		{"parenttype": "Contact", "parent": contact_name, "link_doctype": "Lead"},
		"link_name",
	)

	opportunity_from = "Lead"
	party_name = None
	customer_name = contact.full_name or contact.first_name
	customer_group = None
	territory = None

	if customer and frappe.db.exists("Customer", customer):
		opportunity_from = "Customer"
		party_name = customer
		cust_row = frappe.db.get_value(
			"Customer", customer, ["customer_name", "customer_group", "territory"], as_dict=True
		)
		if cust_row:
			customer_name = cust_row.customer_name or customer
			customer_group = cust_row.customer_group
			territory = cust_row.territory
	elif lead and frappe.db.exists("Lead", lead):
		opportunity_from = "Lead"
		party_name = lead
		lead_fields = [f for f in ["lead_name", "company_name", "territory"] if frappe.db.has_column("Lead", f)]
		lead_row = frappe.db.get_value("Lead", lead, lead_fields, as_dict=True) if lead_fields else {}
		if lead_row:
			customer_name = lead_row.get("lead_name") or lead_row.get("company_name") or lead
			territory = lead_row.get("territory")
		if frappe.db.has_column("Lead", "customer_group"):
			customer_group = frappe.db.get_value("Lead", lead, "customer_group")
	else:
		# Auto-create a minimal Lead for this Contact
		lead_doc = frappe.new_doc("Lead")
		lead_doc.lead_primary_contact = contact_name
		lead_doc.first_name = contact.first_name
		lead_doc.last_name = contact.last_name or ""
		lead_doc.lead_name = contact.full_name or contact.first_name
		if company_name or contact.company_name:
			lead_doc.company_name = company_name or contact.company_name
		if email_id:
			lead_doc.email_id = email_id
		if mobile_no:
			lead_doc.mobile_no = mobile_no
		if phone:
			lead_doc.phone = phone
		if whatsapp_no:
			lead_doc.whatsapp_no = whatsapp_no

		# CustomLead in overrides/lead.py handles backfill and setting contact_doc
		lead_doc.flags.ignore_permissions = True
		lead_doc.insert()

		ensure_contact_linked_to_parent(lead_doc, "lead_primary_contact")

		opportunity_from = "Lead"
		party_name = lead_doc.name
		customer_name = lead_doc.get("lead_name") or lead_doc.get("company_name") or lead_doc.name
		customer_group = lead_doc.get("customer_group")
		territory = lead_doc.get("territory")

	# 4. Resolve address
	customer_address = resolve_address_from_contact_links(contact_name, exclude_doctype="Opportunity")
	if not customer_address and party_name and opportunity_from:
		if opportunity_from == "Customer":
			customer_address = frappe.db.get_value("Customer", party_name, "customer_primary_address")
		if not customer_address:
			customer_address = dynamic_link_lookup(
				{"parenttype": "Address", "link_doctype": opportunity_from, "link_name": party_name},
				"parent",
			)
	if not customer_address:
		customer_address = dynamic_link_lookup(
			{"parenttype": "Address", "link_doctype": "Contact", "link_name": contact_name},
			"parent",
		)

	address_display = None
	if customer_address and frappe.db.exists("Address", customer_address):
		try:
			from frappe.contacts.doctype.address.address import get_address_display

			address_display = get_address_display(customer_address)
		except Exception:
			pass

	return {
		"contact_person": contact_name,
		"opportunity_from": opportunity_from,
		"party_name": party_name,
		"customer_name": customer_name,
		"customer_group": customer_group,
		"territory": territory,
		"contact_mobile": mobile_no,
		"contact_email": email_id,
		"phone": phone,
		"whatsapp": whatsapp_no,
		"customer_address": customer_address,
		"address_display": address_display,
	}
