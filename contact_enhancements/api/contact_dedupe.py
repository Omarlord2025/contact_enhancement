# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

"""Contact-level duplicate detection - a soft, catch-all backstop.

Mirrors custom_webshop.signup.matching's rule-tier philosophy (conservative,
not probabilistic - a match is either a real exact hit or it isn't) at
Contact scope, deliberately smaller: that module's own two query functions
(find_customers_by_phone/find_customers_by_email) always resolve to and
return Customer names, joining Contact Phone/Contact Email through Dynamic
Link - not reusable here unchanged, since this operates on Contact
directly, one layer earlier. No name-mismatch-downgrade layer either,
since warn_if_duplicate_contact never blocks and doesn't need one yet.

warn_if_duplicate_contact is deliberately non-blocking - frappe.msgprint,
never frappe.throw - by hard requirement, not preference: it runs inside
every fragile native flow already on record in this app's own history
(Lead.create_contact(), Supplier.create_primary_contact(),
User.create_contact()'s background job), none of which are wrapped in
broad exception handling. contact_hooks.enforce_unique_mobile_number adds
a genuinely hard-blocking layer specifically for mobile-number duplicates;
this stays the soft layer for everything else - email matches, and
landline "duplicates" that are usually legitimate (a shared office line).
"""

import frappe
from frappe.query_builder.functions import Count

NO_MATCH = "NO_MATCH"
PHONE_MATCH = "PHONE_MATCH"
EMAIL_MATCH = "EMAIL_MATCH"
PHONE_AND_EMAIL_MATCH = "PHONE_AND_EMAIL_MATCH"


def find_contacts_by_phone(phone, exclude=None):
	"""Every other Contact with an identical Contact Phone.phone value.

	Exact match only, no fuzzy scoring - callers are expected to pass an
	already-normalized number (e.g. via
	contact_hooks.normalize_and_validate_contact_phone's own return value),
	not a raw one.

	Args:
		phone: the normalized phone number to look for.
		exclude: a Contact name to exclude from the results (the Contact
			being saved, so it never matches against its own rows).

	Returns:
		A list of distinct Contact names with a matching phone row.
	"""
	if not phone:
		return []

	filters = {"parenttype": "Contact", "phone": phone}
	if exclude:
		filters["parent"] = ["!=", exclude]

	rows = frappe.get_all("Contact Phone", filters=filters, fields=["parent"], distinct=True)
	return [row.parent for row in rows]


def find_contacts_by_email(email, exclude=None):
	"""Every other Contact with an identical email_id.

	Args:
		email: the email address to look for (compared as-is - callers
			should already have normalized casing/whitespace if that
			matters to them).
		exclude: a Contact name to exclude from the results.

	Returns:
		A list of distinct Contact names with a matching email_id.
	"""
	if not email:
		return []

	filters = {"email_id": email}
	if exclude:
		filters["name"] = ["!=", exclude]

	return frappe.get_all("Contact", filters=filters, pluck="name")


def classify_contact_duplicate(doc):
	"""Which kind of duplicate, if any, this Contact appears to be.

	Checks every non-blank phone_nos row (already normalized by
	contact_hooks.normalize_and_validate_contact_phones, which runs
	earlier in the same doc_events list) and the Contact's own email_id.

	Args:
		doc: the Contact document being validated.

	Returns:
		One of NO_MATCH / PHONE_MATCH / EMAIL_MATCH / PHONE_AND_EMAIL_MATCH.
	"""
	exclude = doc.name if not doc.is_new() else None

	phone_match = False
	for row in doc.get("phone_nos", []):
		if row.phone and find_contacts_by_phone(row.phone, exclude=exclude):
			phone_match = True
			break

	email_match = bool(doc.email_id and find_contacts_by_email(doc.email_id, exclude=exclude))

	if phone_match and email_match:
		return PHONE_AND_EMAIL_MATCH
	if phone_match:
		return PHONE_MATCH
	if email_match:
		return EMAIL_MATCH
	return NO_MATCH


def warn_if_duplicate_contact(doc, method=None):
	"""Contact validate doc_event - a non-blocking heads-up when this
	Contact looks like it might already exist elsewhere. See this
	module's own docstring for why this never raises.

	Args:
		doc: the Contact document being validated.
		method: unused, present for the doc_events hook signature.
	"""
	result = classify_contact_duplicate(doc)
	if result == NO_MATCH:
		return

	frappe.msgprint(
		frappe._("This Contact's phone number or email is already used by another Contact - check before saving a duplicate."),
		indicator="orange",
		title=frappe._("Possible Duplicate Contact"),
	)


def _contact_phone_pairs_with_repeated_rows():
	"""Every (Contact, phone) pair that appears on more than one Contact
	Phone row - i.e. the same number entered twice on the same Contact.

	Grouped in SQL for the same reason as
	_phones_shared_by_multiple_contacts, and its own seam for the same
	testability reason (see that function's docstring).

	Returns:
		A sequence of (parent, phone) row tuples.
	"""
	contact_phone = frappe.qb.DocType("Contact Phone")
	return (
		frappe.qb.from_(contact_phone)
		.select(contact_phone.parent, contact_phone.phone)
		.where(
			(contact_phone.custom_landline == 0)
			& contact_phone.phone.isnotnull()
			& (contact_phone.phone != "")
		)
		.groupby(contact_phone.parent, contact_phone.phone)
		.having(Count(contact_phone.name) > 1)
	).run()


def _phones_shared_by_multiple_contacts():
	"""Every non-landline phone number attached to more than one Contact,
	grouped by the database rather than in Python.

	The previous approach read *every* non-landline Contact Phone row into
	a dict to find a handful of duplicates - shipping the whole table over
	the wire, synchronously, inside a web request (the Duplicate Mobile
	Contacts page calls this on load) against a table that grows with
	every Contact ever created. custom_landline is unindexed and
	near-all-zeros so it narrows nothing either. GROUP BY ... HAVING
	returns only the offending numbers, typically a handful of rows.

	Its own seam (rather than being inlined) so tests can exercise the
	grouping/reporting logic around it without needing genuinely colliding
	rows in the database - which the live UNIQUE INDEX from
	patches.add_contact_phone_unique_mobile_index makes impossible to
	construct at all (see tests/test_contact_dedupe._add_duplicate_phone).

	Returns:
		A list of phone values, each shared by 2+ distinct Contacts.
	"""
	contact_phone = frappe.qb.DocType("Contact Phone")
	return (
		frappe.qb.from_(contact_phone)
		.select(contact_phone.phone)
		.where(
			(contact_phone.custom_landline == 0)
			& contact_phone.phone.isnotnull()
			& (contact_phone.phone != "")
		)
		.groupby(contact_phone.phone)
		.having(Count(contact_phone.parent).distinct() > 1)
	).run(pluck=True)


def find_duplicate_mobile_contacts():
	"""Every phone number shared by more than one distinct Contact, among
	non-landline Contact Phone rows only.

	This is Phase 0c's own legacy-duplicate report - the hard gate before
	Phase 0d's mobile-number uniqueness constraint can ship. MariaDB is
	physically incapable of building a UNIQUE INDEX over a column that
	still holds duplicate values, so every group this returns has to be
	resolved by a human (Frappe's native "Merge with existing", reclassify
	a row custom_landline=1 if it's a legitimately shared line, or fix a
	data-entry typo) before that patch can run - not optional busywork.

	Landline rows are excluded outright: a shared landline (an office
	reception line, for example) was never a duplicate-Contact problem,
	and 0d's own constraint only ever applies to non-landline rows either
	way.

	Read-only - never merges, edits, or deletes anything itself.

	Returns:
		A list of {"phone": <E.164 value>, "contacts": [Contact name, ...]}
		dicts, one per number shared by 2+ distinct Contacts, worst
		offenders (most Contacts sharing one number) first.
	"""
	duplicate_phones = _phones_shared_by_multiple_contacts()
	if not duplicate_phones:
		return []

	# Second read fetches members for the offending numbers only.
	rows = frappe.get_all(
		"Contact Phone",
		filters={"custom_landline": 0, "phone": ["in", duplicate_phones]},
		fields=["phone", "parent"],
		distinct=True,
	)

	contacts_by_phone = {}
	for row in rows:
		contacts_by_phone.setdefault(row.phone, set()).add(row.parent)

	duplicates = [
		{"phone": phone, "contacts": sorted(contacts)}
		for phone, contacts in contacts_by_phone.items()
		if len(contacts) > 1
	]
	duplicates.sort(key=lambda entry: len(entry["contacts"]), reverse=True)
	return duplicates


@frappe.whitelist()
def merge_duplicate_mobile_contacts(phone, survivor):
	"""Merge every other Contact sharing `phone` into `survivor`, in one
	call instead of one "Merge with existing" dialog per Contact - the
	Duplicate Mobile Contacts page's own "Keep this Contact" button
	(page/duplicate_mobile_contacts/duplicate_mobile_contacts.js) calls
	this directly, one click, no separate picker dialog.

	Wraps frappe.rename_doc(doctype, old, new, merge=True) - the exact
	same core mechanism the Contact form's own "Merge with existing"
	action already uses (confirmed by reading frappe/client.py's
	rename_doc and frappe/model/rename_doc.py directly), just applied to
	every Contact in the group in one server round-trip. Real permission
	checks still apply for each merge - ignore_permissions is never
	passed, so a caller without write access to both sides gets the same
	PermissionError the native single-Contact action would raise.

	Re-derives the current group membership from the database via
	find_contacts_by_phone (excluding survivor) rather than trusting a
	caller-supplied list, so a stale report on the client can't merge a
	Contact that no longer actually shares this number, or miss one that
	started sharing it since the page loaded.

	A failure merging one Contact does not stop the rest - each merge is
	independent, and partial progress (e.g. 2 of 3 succeeding) is still
	useful to report back rather than discard.

	Args:
		phone: the shared E.164 mobile number identifying the group.
		survivor: name of the Contact that should remain - every other
			Contact currently sharing `phone` is merged into it and
			deleted.

	Returns:
		{"merged": [Contact names successfully merged], "failed": [{"name":
		..., "error": ...} for any that could not be merged]}.

	Raises:
		frappe.ValidationError: if survivor doesn't exist, or no other
			Contact currently shares this number (nothing to merge).
	"""
	if not frappe.db.exists("Contact", survivor):
		frappe.throw(frappe._("Contact {0} does not exist.").format(survivor))

	members_to_merge = find_contacts_by_phone(phone, exclude=survivor)
	if not members_to_merge:
		frappe.throw(
			frappe._("No other Contact currently shares {0} with {1} - nothing to merge.").format(
				phone, survivor
			)
		)

	merged = []
	failed = []
	for name in members_to_merge:
		try:
			frappe.rename_doc("Contact", name, survivor, merge=True)
			merged.append(name)
		except Exception as e:
			failed.append({"name": name, "error": str(e)})

	return {"merged": merged, "failed": failed}


def find_duplicate_phone_rows_within_contact():
	"""Every (Contact, phone) pair where that *same* Contact has 2+
	non-landline Contact Phone rows holding the identical number - a
	different failure mode from find_duplicate_mobile_contacts, which
	only ever catches a number shared *across* distinct Contacts.

	Not hypothetical: found on a real production-shaped site where the
	exact same number existed twice on one Contact (differing only in
	which row had is_primary_mobile_no set) - invisible to find_
	duplicate_mobile_contacts's own "len(distinct contacts) > 1"
	definition of duplicate, but still a genuine violation of the
	database-level UNIQUE INDEX add_contact_phone_unique_mobile_index
	adds, which doesn't distinguish same-parent from different-parent
	collisions. That patch's own pre-check calls this too, not just
	find_duplicate_mobile_contacts, so it aborts loudly here as well
	rather than letting this surface as a raw DB IntegrityError.

	Landline rows are excluded, matching find_duplicate_mobile_contacts
	and the constraint itself.

	Read-only - never deletes/merges anything itself.

	Returns:
		A list of {"contact": <Contact name>, "phone": <E.164 value>,
		"rows": [Contact Phone row name, ...]} dicts, one per (Contact,
		phone) pair with 2+ rows.
	"""
	# Same reasoning as _phones_shared_by_multiple_contacts: let the
	# database find the (Contact, phone) pairs that actually repeat rather
	# than reading every non-landline row in the table into Python to
	# discover that almost none of them do.
	duplicate_pairs = _contact_phone_pairs_with_repeated_rows()
	if not duplicate_pairs:
		return []

	rows = frappe.get_all(
		"Contact Phone",
		filters={
			"custom_landline": 0,
			"parent": ["in", list({contact for contact, _phone in duplicate_pairs})],
			"phone": ["in", list({phone for _contact, phone in duplicate_pairs})],
		},
		fields=["name", "phone", "parent"],
	)

	wanted = set(duplicate_pairs)
	rows_by_contact_phone = {}
	for row in rows:
		# The two "in" filters above match their cross-product, so a row
		# can come back for a pair that never actually repeated.
		if (row.parent, row.phone) not in wanted:
			continue
		rows_by_contact_phone.setdefault((row.parent, row.phone), []).append(row.name)

	return [
		{"contact": contact, "phone": phone, "rows": row_names}
		for (contact, phone), row_names in rows_by_contact_phone.items()
		if len(row_names) > 1
	]


@frappe.whitelist()
def dedupe_contact_phone_rows(contact, phone):
	"""Consolidate every non-landline Contact Phone row on `contact`
	holding `phone` into one - the resolution action for find_duplicate_
	phone_rows_within_contact's own report row. Not a merge (there is
	only one Contact involved here, not two) - contact_dedupe.
	merge_duplicate_mobile_contacts doesn't apply to this failure mode.

	Keeps whichever channel flags (is_primary_mobile_no/custom_whatsapp/
	custom_telegram) were set to 1 on *any* of the duplicate rows - OR'd
	together onto the single surviving row - so consolidating never
	silently loses a flag a human had actually set on one of the now-
	redundant rows, then deletes every other row and saves.

	Args:
		contact: the Contact whose own phone_nos holds the duplicate.
		phone: the E.164 number duplicated within that Contact.

	Returns:
		The name of the surviving Contact Phone row.

	Raises:
		frappe.ValidationError: if this Contact doesn't currently have
			2+ non-landline rows holding this number (nothing to fix).
	"""
	doc = frappe.get_doc("Contact", contact)
	matching = [row for row in doc.phone_nos if row.phone == phone and not row.custom_landline]
	if len(matching) < 2:
		frappe.throw(
			frappe._("{0} does not have duplicate rows for {1} - nothing to fix.").format(
				contact, phone
			)
		)

	survivor = matching[0]
	survivor.is_primary_mobile_no = 1 if any(row.is_primary_mobile_no for row in matching) else 0
	survivor.custom_whatsapp = 1 if any(row.custom_whatsapp for row in matching) else 0
	survivor.custom_telegram = 1 if any(row.custom_telegram for row in matching) else 0

	for row in matching[1:]:
		doc.phone_nos.remove(row)

	doc.save()
	return survivor.name
