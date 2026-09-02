# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

"""Phase 1: make Supplier Primary Contact genuinely mandatory (and disable
Supplier's own Quick Entry) - see setup.property_setters.
create_supplier_contact_property_setters's own docstring for the full
reasoning, including why supplier_primary_address is deliberately left
optional (asymmetric to Customer, on purpose).
"""

from contact_enhancements.setup.property_setters import create_supplier_contact_property_setters


def execute():
	create_supplier_contact_property_setters()
