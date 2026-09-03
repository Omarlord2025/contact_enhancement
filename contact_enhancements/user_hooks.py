# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

"""User doc_events for this app.

Fixes a regression this app's own Contact validation introduced,
independent of anything else in this plan: User.on_update()
(frappe/core/doctype/user/user.py) unconditionally enqueues
frappe.core.doctype.user.user.create_contact(user, ignore_mandatory=True)
as a background job on every User save. That function auto-creates or
updates a Contact matching the User's own email, appending
phone/mobile_no as Contact Numbers rows. ignore_mandatory only ever gates
Document._validate_mandatory() (confirmed by reading frappe/model/
document.py) - a completely different step from run_method("validate"),
where contact_hooks.normalize_and_validate_contact_phones lives and can
frappe.throw() on a phone number it can't parse. Any User whose phone/
mobile_no doesn't parse as a valid number has always silently failed this
background sync, since this app's phone validation shipped - unrelated
to any new work in this session, just never exercised end to end before.

Two things close the gap, together:

1. Here: validate_user_phone_before_contact_sync does *safe*, always-
   correct cleanup (whitespace/dash/paren stripping only, via
   contact_hooks.strip_phone_formatting_noise) on User.phone/mobile_no
   before the background job ever reads them - this alone rescues a
   number a human typed with stray formatting, but can't rescue a
   genuinely non-Egyptian number with no "+" prefix, since User has no
   country field to check against.
2. contact_hooks.normalize_and_validate_contact_phones itself (see its
   own docstring) drops an unparseable row instead of raising when it
   detects it's running inside a real background job
   (_running_in_background_job) - which is exactly this flow's own
   execution context, confirmed via frappe/utils/background_jobs.py: a
   User saved interactively enqueues without now=True, so create_contact
   genuinely runs through the async execute_job() wrapper that sets
   frappe.local.job, not the direct frappe.call(...) path tests and
   now=True use.

Together: a formatting-only issue gets fixed before it ever reaches
Contact; a genuinely unparseable number - the case neither this hook nor
a country guess could safely fix - gets dropped from the Contact instead
of blocking the whole background sync, logged for visibility instead of
silently vanishing into an Error Log entry no one ever links back to a
specific User.
"""

from contact_enhancements.contact_hooks import strip_phone_formatting_noise
from contact_enhancements.utils import (
	backfill_name_from_primary_contact,
	ensure_contact_linked_to_parent,
)


def validate_user_phone_before_contact_sync(doc, method=None):
	"""User validate doc_event - cleans obvious formatting noise from
	phone/mobile_no before User.on_update() enqueues its own background
	Contact sync. Never raises - User is too foundational a doctype to
	hard-block over a phone-format quirk, and this is purely a best-effort
	improvement to the common case, not a validation step.

	Args:
		doc: the User document being validated.
		method: unused, present for the doc_events hook signature.
	"""
	if doc.phone:
		doc.phone = strip_phone_formatting_noise(doc.phone)
	if doc.mobile_no:
		doc.mobile_no = strip_phone_formatting_noise(doc.mobile_no)


def backfill_user_name_from_primary_contact(doc, method=None):
	"""User validate hook - fill first_name (relabeled "Full Name") from
	the primary Contact's full_name when it's still blank. See
	utils.backfill_name_from_primary_contact.

	Deliberately does NOT touch email: User.email is this document's own
	naming field, so writing it means renaming the User, which a
	background backfill has no business deciding (the same reasoning that
	keeps email out of _CONTACT_SYNC_TARGETS for User).

	Args:
		doc: the User being validated.
		method: unused, present for the doc_events hook signature.
	"""
	backfill_name_from_primary_contact(doc, "user_primary_contact", "first_name")


def link_user_contact(doc, method=None):
	"""User on_update hook: Dynamic-Link user_primary_contact back to this
	User - the same fix every other doctype in this app applies (picking a
	Contact in a plain Link field never creates this link on its own), and
	the same "must show up in that Contact's own linking reference table"
	requirement every other doctype's own primary-contact field already
	satisfies. user_primary_contact itself is set by public/js/user.js's
	own onboarding dialog (setup/custom_fields.py).

	Deliberately does NOT also seed an Address Dynamic-Link to this User
	the way an earlier version of this hook did (sync_user_address_from_
	contact_links, since removed): api.user_addresses.
	get_all_addresses_for_user already surfaces every address reachable
	via the shared Contact live, computed fresh on every render - seeding
	a permanent Dynamic-Link-to-User copy of one of them would have shown
	up mislabeled as "Linked directly" (this User's own genuine choice)
	and gone stale the moment the *source* (e.g. the Customer's own
	address) changed later, which is exactly the "not just one time"
	staleness this app's own live view exists to avoid.

	Args:
		doc: the User document that was just saved.
		method: unused, present for the doc_events hook signature.
	"""
	ensure_contact_linked_to_parent(doc, "user_primary_contact")
