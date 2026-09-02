# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

"""Phase 0e: relabel Contact.first_name to "Full Name" and hide the unused
Contact.middle_name field - see setup.property_setters.
create_contact_full_name_property_setters's own docstring for the full
reasoning, including why last_name is deliberately left alone.
"""

from contact_enhancements.setup.property_setters import create_contact_full_name_property_setters


def execute():
	create_contact_full_name_property_setters()
