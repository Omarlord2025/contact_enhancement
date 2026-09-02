# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

"""Disable User's own native Quick Entry, so "+ New User" always reaches
the full form - the only place public/js/user.js's own onboarding dialog
can run - see setup.property_setters.create_user_contact_property_setters's
own docstring for the full reasoning.
"""

from contact_enhancements.setup.property_setters import create_user_contact_property_setters


def execute():
	create_user_contact_property_setters()
