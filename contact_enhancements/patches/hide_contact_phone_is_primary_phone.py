# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

"""Phase 0f: hide Contact Phone.is_primary_phone via a real Property
Setter - see setup.property_setters.create_contact_phone_hidden_field_
property_setters's own docstring for why this replaces the client-side
grid.update_docfield_property hack in public/js/contact.js instead of
patching it further.
"""

from contact_enhancements.setup.property_setters import (
	create_contact_phone_hidden_field_property_setters,
)


def execute():
	create_contact_phone_hidden_field_property_setters()
