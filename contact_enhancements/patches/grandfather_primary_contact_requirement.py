# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

from contact_enhancements.setup.property_setters import (
	create_contact_enhancements_property_setters,
	create_supplier_contact_property_setters,
)


def execute():
	"""Remove the `reqd` Property Setters this app previously applied to
	Customer.customer_primary_contact / customer_primary_address and
	Supplier.supplier_primary_contact.

	Needed as its own patch because Frappe's Patch Log is keyed by name:
	make_customer_primary_contact_mandatory and
	make_supplier_primary_contact_mandatory have already run on every site
	that installed an earlier version, so editing the functions they call
	fixes fresh installs only. This re-invokes those same (now inverted)
	functions so existing sites are corrected too.

	Why: a static `reqd` is evaluated on every save, not just inserts, so
	the requirement reached backwards and froze every Customer/Supplier
	created before this app existed - a no-op re-save raised
	MandatoryError, blocking ERPNext's own flows, Data Import and other
	apps as well as the Desk form. The requirement is unchanged for new
	records and now lives in
	customer_hooks.enforce_primary_contact_and_address_on_new_customer and
	supplier_hooks.enforce_primary_contact_on_new_supplier, both gated on
	doc.is_new().
	"""
	create_contact_enhancements_property_setters()
	create_supplier_contact_property_setters()
