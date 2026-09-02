# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

"""Relabel Employee.first_name to "Full Name" and hide the now-redundant
native Employee.employee_name - see setup.property_setters.
create_employee_full_name_property_setters's own docstring for the full
reasoning.
"""

from contact_enhancements.setup.property_setters import create_employee_full_name_property_setters


def execute():
	create_employee_full_name_property_setters()
