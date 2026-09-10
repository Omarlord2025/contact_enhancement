# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

import frappe
from erpnext.crm.doctype.lead.lead import Lead
from erpnext.selling.doctype.customer.customer import parse_full_name


class CustomLead(Lead):
	def before_insert(self):
		self.contact_doc = None
		if self.get("lead_primary_contact"):
			self.contact_doc = frappe.get_doc("Contact", self.lead_primary_contact)
			self._backfill_from_contact()
			return

		super().before_insert()

	def validate(self):
		if self.get("lead_primary_contact"):
			self._backfill_from_contact()
		super().validate()

	def _backfill_from_contact(self):
		from contact_enhancements.api.lead_lookup import get_contact_snapshot_for_lead

		snapshot = get_contact_snapshot_for_lead(self.lead_primary_contact)
		if not snapshot:
			return

		for field in (
			"lead_name",
			"first_name",
			"last_name",
			"company_name",
			"mobile_no",
			"phone",
			"whatsapp_no",
			"email_id",
			"country",
			"gender",
			"salutation",
			"job_title",
		):
			if not self.get(field) and snapshot.get(field):
				self.set(field, snapshot[field])

