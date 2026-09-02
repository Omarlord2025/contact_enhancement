# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

from contact_enhancements.setup.custom_fields import create_contact_enhancements_custom_fields
from contact_enhancements.setup.property_setters import create_primary_contact_fan_out_indexes


def execute():
	# The Employee/User half of these indexes is declared as
	# "search_index": 1 on this app's own Custom Fields, so the fields
	# themselves have to be re-synced before the ALTER TABLE pass can see
	# the flag - create_custom_fields(update=True) is idempotent, so
	# re-running it here is safe and only updates the changed property.
	create_contact_enhancements_custom_fields()
	create_primary_contact_fan_out_indexes()
