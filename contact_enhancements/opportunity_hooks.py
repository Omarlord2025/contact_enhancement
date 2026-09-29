# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

import frappe
from contact_enhancements.utils import ensure_doc_linked_to_parent


def link_opportunity_contact_and_address(doc, method=None):
	"""Opportunity on_update hook:
	Guarantee that:
	1. contact_person is Dynamic-Linked to this Opportunity in Contact.links.
	2. customer_address (if present) is Dynamic-Linked to this Opportunity in Address.links.
	"""
	if doc.get("contact_person"):
		ensure_doc_linked_to_parent(doc, "contact_person", "Contact")

	if doc.get("customer_address"):
		ensure_doc_linked_to_parent(doc, "customer_address", "Address")
