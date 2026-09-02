# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

from contact_enhancements.setup.property_setters import create_transaction_contact_person_indexes


def execute():
	# Unlike this app's other index patches, these ALTER TABLEs run against
	# the largest tables in an ERPNext site (Sales Invoice, Sales Order,
	# Purchase Invoice, ...) and are not instant on a real dataset - run
	# this migrate in a maintenance window. See the setup function's own
	# docstring for why the indexes are worth it.
	create_transaction_contact_person_indexes()
