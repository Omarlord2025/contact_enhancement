# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

"""Contact search/lookup/creation - genuinely generic across every doctype
that links to Contact, not specific to Lead/Customer despite originally
living in api/lead_lookup.py. Moved here in Phase 1 once Supplier needed
the same search-as-you-type contact picker Customer's own dialog already
used - confirmed by direct reading that none of these five functions
touch Customer (or Lead) in their own implementation, only in older
docstring prose. lead_lookup.py keeps a bare re-export of each so
anything still calling it by that path (e.g. public/js/customer.js's own
existing frappe.call references, before contact_picker_dialog.js
consolidated them) keeps resolving unchanged.
"""

import frappe
from frappe import _

# Upper bound for any caller-supplied page size on the whitelisted search
# endpoints - see search_contacts_with_details.
MAX_SEARCH_PAGE_LEN = 100

from contact_enhancements.utils import (
	address_display_fields,
	address_source_label,
	get_all_addresses_for_contact,
	resolve_address_from_contact_links,
)

# normalize_phone_for_country (Egypt-only regex) used to live here - replaced
# by contact_enhancements.contact_hooks.normalize_and_validate_contact_phone,
# which covers every country via the phonenumbers library and runs
# unconditionally as a Contact validate hook, so create_minimal_contact below
# no longer needs to normalize the phone number itself before saving.


@frappe.whitelist()
def create_minimal_contact(first_name, phone, country=None):
	"""Create a new Contact with just a name and a phone number.

	Backs the "Create New Contact" action in the shared contact-picker
	dialog (contact_enhancements/public/js/contact_picker_dialog.js) -
	deliberately not routed through Frappe's generic Link-field "+ Create
	a new Contact" flow, which opens a blank full Contact form and never
	actually carries the phone number the user just searched by onto the
	new record (it gets stuffed into the hidden, read-only full_name
	field via route_options.name_field, then discarded by Contact.
	validate() recomputing full_name from scratch - confirmed by reading
	that flow).

	A Contact is always a person, with its own first name, regardless of
	which doctype/party type the dialog's caller is linking it to - the
	dialog's own Individual/Company (or equivalent) choice only affects
	the *caller's* own fields and never touches this function or
	Contact's own fields at all.

	Args:
		first_name: the new Contact's first name.
		phone: phone number for the new Contact Numbers row, marked
			primary mobile - normalized/validated against country by the
			Contact validate hook itself
			(contact_enhancements.contact_hooks.normalize_and_validate_contact_phones),
			the same as every other Contact save path now, not by this
			function.
		country: name of a Country record - the dialog's own Country field,
			searchable across every country Frappe ships. Both the
			phone-validation region and the value stored on that row's own
			mandatory country field (Contact Phone.country -
			setup/custom_fields.py) - left unset (falls back to that
			field's "Egypt" DocField default) only if not provided.

	Returns:
		The new Contact's name.
	"""
	contact = frappe.new_doc("Contact")
	contact.first_name = first_name
	row = {"phone": phone, "is_primary_mobile_no": 1}
	if country:
		row["country"] = country
	contact.append("phone_nos", row)
	contact.insert()
	return contact.name


def escape_like_wildcards(txt):
	"""Neutralize LIKE's own wildcards in user-supplied search text, so a
	"%" or "_" typed into a search box is matched literally instead of
	being executed as a pattern.

	Frappe's query builder parameterizes values against SQL *injection*,
	but a parameterized value is still interpreted as a LIKE *pattern* -
	so "%" reaching `LIKE '%…%'` unescaped matches every row and turns the
	Contact search into an unbounded scan of the whole Contact/Contact
	Phone join. At production volume that is a self-inflicted outage that
	any user can trigger by typing one character.

	Escapes the escape character itself first, or "\\%" would become
	"\\\\%" - an escaped backslash followed by a live wildcard.

	Args:
		txt: raw search text as typed, or None.

	Returns:
		The text with backslash, "%" and "_" backslash-escaped (MariaDB's
		default LIKE escape character), or the original falsy value
		unchanged.
	"""
	if not txt:
		return txt
	return txt.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _is_e164_prefix_query(cleaned_txt):
	"""Whether a search string is unambiguously the start of an E.164
	phone number ("+" followed by digits only) - see _contact_search_query
	for why that case is narrowed to a single anchored, index-usable
	predicate instead of the general four-way wildcard search.

	Args:
		cleaned_txt: search text with formatting noise already stripped.

	Returns:
		True if it starts with "+" and every remaining character is a digit.
	"""
	return bool(cleaned_txt) and cleaned_txt.startswith("+") and cleaned_txt[1:].isdigit()


def _contact_search_query(txt, start, page_len):
	"""Shared Contact<->Contact Phone join/filter/pagination, reused by
	both search_contact_by_phone (native Link-field dropdown, flat-tuple
	shape) and search_contacts_with_details (the contact-picker dialog's
	rich-card data) - those two stay separate functions with separate
	return contracts (see search_contacts_with_details's own docstring for
	why), but the join/where/groupby/pagination shape they both built
	independently is identical and now defined in exactly one place. Each
	caller adds its own .select(...) before calling .run().

	Args:
		txt: search text, matched against full_name, email_id, or any
			Contact Phone number - in either E.164 or local/national form
			(see below).
		start: pagination offset.
		page_len: max number of Contacts to return.

	Returns:
		A 3-tuple (query, contact, contact_phone) - the pypika query
		builder (joined/filtered/paginated, no columns selected yet) and
		the two DocType table references it was built from, so the caller
		can reference the same aliases in its own .select(...).
	"""
	from contact_enhancements.contact_hooks import strip_phone_formatting_noise

	contact = frappe.qb.DocType("Contact")
	contact_phone = frappe.qb.DocType("Contact Phone")
	# phone is always stored E.164 (contact_hooks.normalize_and_validate_
	# contact_phone) - a LIKE against it alone can't reliably find a
	# Contact by the local-format number staff are used to typing (the
	# country-code digits get in the way for every country but Egypt, and
	# even there only by accident). custom_phone_national holds the same
	# number's bare local digits specifically for this - see
	# contact_hooks._national_digits. Also strip formatting noise from
	# the query text itself, so "+20 101 234 5678" or "010-1234-5678"
	# matches the compact digit strings both fields actually hold.
	cleaned_txt = strip_phone_formatting_noise(txt) or txt
	safe_txt = escape_like_wildcards(txt)
	safe_cleaned_txt = escape_like_wildcards(cleaned_txt)

	if _is_e164_prefix_query(cleaned_txt):
		# An "+…"-prefixed query can only ever be an E.164 number, and
		# `phone` is the only column that stores one - custom_phone_national
		# holds bare local digits (never a "+"), and a full_name never
		# contains one. So this narrows to a single ANCHORED prefix match,
		# which the phone_index can actually serve as a range scan, instead
		# of four leading-wildcard predicates that force a full scan of the
		# Contact/Contact Phone join. This matters because phone numbers are
		# now *displayed* in E.164 everywhere, so pasting "+201012345678"
		# straight back into the search box is a normal thing to do.
		#
		# Deliberate, documented trade-off: an email using plus-addressing
		# ("omar+20@example.com") is no longer found by searching "+20".
		# Matching a phone-shaped query against an email local-part was
		# never the intent, and the full scan it costs is not worth keeping
		# for it.
		conditions = contact_phone.phone.like(f"{safe_cleaned_txt}%")
	else:
		conditions = (
			contact.full_name.like(f"%{safe_txt}%")
			| contact.email_id.like(f"%{safe_txt}%")
			| contact_phone.phone.like(f"%{safe_cleaned_txt}%")
			| contact_phone.custom_phone_national.like(f"%{safe_cleaned_txt}%")
		)

	query = (
		frappe.qb.from_(contact)
		.left_join(contact_phone)
		.on(contact_phone.parent == contact.name)
		.where(conditions)
		.groupby(contact.name)
		.limit(page_len)
		.offset(start)
	)
	return query, contact, contact_phone


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def search_contact_by_phone(doctype, txt, searchfield, start, page_len, filters):
	"""Link-field query for a primary-contact Link field (Customer's
	Customer Primary Contact, Supplier's Supplier Primary Contact, ...).

	Replaces the doctype's own native default query behavior, which
	typically only returns Contacts already Dynamic-Linked to this exact
	parent record - empty for any not-yet-saved one (see e.g.
	contact_enhancements/public/js/customer.js, which overrides the
	field's query to point here instead). Searches Contact by name/email
	as usual, and by any number in its Contact Numbers table - not just
	the primary one.

	Args:
		doctype, txt, searchfield, start, page_len, filters: standard
			Frappe Link-field query arguments; only txt/start/page_len are
			used here.

	Returns:
		Rows of (name, email_id, mobile_no) matching txt, for the Link
		field's dropdown.
	"""
	query, contact, _contact_phone = _contact_search_query(txt, start, page_len)
	return query.select(contact.name, contact.email_id, contact.mobile_no).run()


def _phone_channels(phone_row):
	"""Turn a Contact Phone row's custom_whatsapp/custom_telegram/
	custom_landline flags (contact_enhancements/setup/custom_fields.py)
	into a list of human-readable channel labels for display.

	Args:
		phone_row: a dict with custom_whatsapp/custom_telegram/custom_landline keys.

	Returns:
		A list of zero or more of "WhatsApp", "Telegram", "Landline".
	"""
	labels = []
	if phone_row.get("custom_whatsapp"):
		labels.append(_("WhatsApp"))
	if phone_row.get("custom_telegram"):
		labels.append(_("Telegram"))
	if phone_row.get("custom_landline"):
		labels.append(_("Landline"))
	return labels


@frappe.whitelist()
def search_contacts_with_details(txt, start=0, page_len=10):
	"""Rich match list for the shared contact-picker dialog
	(contact_enhancements/public/js/contact_picker_dialog.js), so whoever's
	entering data can visually confirm a match against the person in front
	of them ("is this your email? is this your WhatsApp number?") instead
	of picking a bare name off a list.

	Deliberately a separate function from search_contact_by_phone rather
	than a reshape of it: that one also backs the native primary-contact
	Link field's own dropdown (via frm.set_query, e.g. in customer.js) and
	must keep returning its current flat-tuple shape unchanged -
	regressing that would break the working Link field, not just this
	dialog.

	Two simple queries rather than one GROUP_CONCAT-based query: packing
	phone numbers and 3 boolean channel flags into one aggregated string
	would need an ad hoc encode/decode scheme (and cross-database
	fragility, since GROUP_CONCAT's equivalent varies by backend); a
	second query keyed off the already-matched Contact names is simpler
	and independently testable.

	Args:
		txt: search text - matched against full_name, email_id, and any
			Contact Phone number, same as search_contact_by_phone.
		start: pagination offset.
		page_len: max number of Contacts to return.

	Returns:
		A list of dicts, one per matching Contact: name, full_name,
		company_name, designation, email_id, and phones (a list of
		{"phone": ..., "channels": [...]} for every phone number that
		Contact has, not just the one that matched).
	"""
	# Bounded because this is a whitelisted endpoint: the dialog always
	# sends 10, but nothing stopped another authenticated caller asking
	# for a million rows of a join that already can't use an index. Its
	# sibling search_contact_by_phone gets this for free from
	# @frappe.validate_and_sanitize_search_inputs, which cint()s these -
	# that decorator can't be used here because it requires the full
	# Link-field argument signature this function deliberately doesn't have.
	start = max(frappe.utils.cint(start), 0)
	page_len = min(max(frappe.utils.cint(page_len) or 10, 1), MAX_SEARCH_PAGE_LEN)

	query, contact, contact_phone = _contact_search_query(txt, start, page_len)
	matches = query.select(
		contact.name,
		contact.full_name,
		contact.company_name,
		contact.designation,
		contact.email_id,
	).run(as_dict=True)

	return _attach_phones(matches)


def _attach_phones(matches):
	"""Graft every match's own phone numbers onto it, in one batched read.

	Shared by search_contacts_with_details and
	search_contacts_by_name_prefix so both return the identical match
	shape - the contact-picker dialog renders them with the same code.

	Kept as a second query rather than a GROUP_CONCAT for the reason given
	in search_contacts_with_details's own docstring: packing numbers plus
	three boolean channel flags into one aggregated string would need an ad
	hoc encode/decode scheme, and the equivalent function varies by
	database backend.

	Args:
		matches: list of match dicts, each carrying at least "name".

	Returns:
		The same list, each entry given a "phones" key: a list of
		{"phone": ..., "channels": [...]} for every number that Contact
		has (not only one that matched).
	"""
	if not matches:
		return []

	contact_phone = frappe.qb.DocType("Contact Phone")
	contact_names = [match["name"] for match in matches]
	phone_rows = (
		frappe.qb.from_(contact_phone)
		.select(
			contact_phone.parent,
			contact_phone.phone,
			contact_phone.custom_whatsapp,
			contact_phone.custom_telegram,
			contact_phone.custom_landline,
		)
		.where(contact_phone.parent.isin(contact_names))
	).run(as_dict=True)

	phones_by_contact = {}
	for row in phone_rows:
		phones_by_contact.setdefault(row.parent, []).append(
			{"phone": row.phone, "channels": _phone_channels(row)}
		)

	for match in matches:
		match["phones"] = phones_by_contact.get(match["name"], [])
	return matches


# How many leading name components identify "the same person" for the
# duplicate-name lookup. Three (first, father's, family) is a heuristic
# choice for this search only - the app no longer requires a name to have
# any particular number of parts, but three is still a reasonable amount
# of a name to key a "is this the same person" match on regardless of
# whether it's enforced anywhere.
NAME_PREFIX_COMPONENTS = 3

# Below this many characters a prefix matches too much of the table to be
# worth showing, and the user is still typing their first word.
MIN_NAME_PREFIX_LENGTH = 3


def name_prefix_key(txt):
	"""The prefix a typed name should be matched against.

	Two things happen here, and both matter:

	1. The text is run through this app's own name normalization, so what
	   the user types is compared in the same form the database stores.
	   Without it a typed "أحمد" would never match a stored "احمد" - the
	   normalizer rewrites word-initial alef-hamza, strips tashkeel, and
	   folds word-final teh-marbuta/yeh, so raw typed text and stored text
	   are simply different strings for a large share of Arabic names.

	2. It is capped at NAME_PREFIX_COMPONENTS. That is what makes the
	   match work in BOTH directions: typing "Ahmed Sabry Amin" finds the
	   longer "Ahmed Sabry Amin Hassan", and typing that longer name still
	   finds the shorter "Ahmed Sabry Amin", because both queries reduce to
	   the same three-component prefix. A plain prefix of the full typed
	   text would only ever find longer names.

	Args:
		txt: the name as typed so far.

	Returns:
		The prefix to match with, or None if there isn't enough to search.
	"""
	from contact_enhancements.contact_hooks import normalize_arabic_first_name

	normalized = normalize_arabic_first_name((txt or "").strip())
	if not normalized or len(normalized) < MIN_NAME_PREFIX_LENGTH:
		return None
	return " ".join(normalized.split()[:NAME_PREFIX_COMPONENTS])


@frappe.whitelist()
def search_contacts_by_name_prefix(txt, page_len=10):
	"""Contacts whose name starts with the same components as `txt` - the
	"this person may already exist" lookup behind the contact-picker
	dialog's live duplicate-name list.

	Exists because phone-based duplicate detection cannot catch the case
	this app most needs to catch: the same person coming back and being
	entered again with a DIFFERENT phone number. Nothing about the two
	records matches on phone, but the name usually does.

	Unlike search_contacts_with_details, this is an ANCHORED prefix match
	on full_name alone. That matters twice over: it is the semantics the
	feature actually wants (same name, or same name plus a fourth), and a
	leading-anchored LIKE is one of the few this app can serve from a
	B-tree index - hence the search_index on Contact.full_name added
	alongside it. The general search deliberately cannot use an index,
	because it matches mid-string and ORs across email and phone columns
	too, which would also drag in Contacts that merely share a phone
	fragment.

	Args:
		txt: the name as typed so far.
		page_len: maximum matches to return (bounded like every other
			whitelisted search here).

	Returns:
		A list of match dicts in exactly the shape
		search_contacts_with_details returns - name, full_name,
		company_name, designation, email_id, phones - so the dialog can
		render both lists with the same code. Empty when there isn't
		enough typed to search on.
	"""
	prefix = name_prefix_key(txt)
	if not prefix:
		return []

	page_len = min(max(frappe.utils.cint(page_len) or 10, 1), MAX_SEARCH_PAGE_LEN)
	matches = frappe.get_all(
		"Contact",
		filters={"full_name": ["like", f"{escape_like_wildcards(prefix)}%"]},
		fields=["name", "full_name", "company_name", "designation", "email_id"],
		order_by="modified desc",
		limit_page_length=page_len,
	)
	return _attach_phones(matches)


@frappe.whitelist()
def get_address_from_contact_links(contact, exclude_doctype=None, exclude_name=None):
	"""Whitelisted client-facing wrapper around utils.
	resolve_address_from_contact_links, called the moment a doctype's own
	primary-contact field changes (customer.js/supplier.js/employee.js's
	own field handlers) - needed because Frappe's own native client-side
	mandatory-field check runs before the save request is ever dispatched
	to the server, so a server-side validate() hook doing this same
	resolution never gets a chance to run at all whenever the target
	field (e.g. Customer's own mandatory customer_primary_address) is
	still blank in frm.doc at the moment Save is pressed. Calling this
	proactively and frm.set_value()-ing the result closes that gap for
	every doctype's own address field.

	Args:
		contact: the Contact whose other links to search.
		exclude_doctype, exclude_name: the calling doctype's own
			in-progress record, if it's one that could itself appear in
			the priority chain (Customer/Supplier) - see
			resolve_address_from_contact_links's own docstring for why.

	Returns:
		An Address name, or None if nothing in the chain resolves.
	"""
	return resolve_address_from_contact_links(contact, exclude_doctype=exclude_doctype, exclude_name=exclude_name)


@frappe.whitelist()
def get_addresses_for_contact(contact):
	"""Live, always-current list of every Address reachable from a Contact
	(Contact's own "Linked Addresses" table, public/js/contact.js) -
	backs the same requirement as api.user_addresses.
	get_all_addresses_for_user, direct on Contact this time: a plain read
	via utils.get_all_addresses_for_contact, computed fresh on every call,
	so it's never a stale snapshot - an address added or removed on any
	linked Customer/Supplier/Employee/Lead/User shows up here immediately.

	Args:
		contact: the Contact whose links to search.

	Returns:
		A list of Address dicts (utils.address_display_fields) with one
		extra key, "source_label" (e.g. "via Customer: Acme Corp") - every
		address here is reached through another doctype, never Contact's
		own direct link (Contact isn't itself a Dynamic Link target the
		way Customer/Supplier/Employee/Lead/User are), so there's no
		"removable" flag the way User's own equivalent view has - nothing
		here is owned by the Contact directly to remove.
	"""
	rows = get_all_addresses_for_contact(contact)
	if not rows:
		return []

	# source_title comes back from get_all_addresses_for_contact already
	# resolved, so labelling below costs no further queries - it used to
	# run one lookup per address, repeated identically for every address
	# reached via the same Customer/Supplier/Employee.
	source_by_address = {
		row["address"]: (row["source_doctype"], row["source_name"], row.get("source_title"))
		for row in rows
	}
	addresses = frappe.get_all(
		"Address",
		filters={"name": ["in", list(source_by_address.keys())]},
		fields=address_display_fields(),
		order_by="creation desc",
	)
	for addr in addresses:
		source_doctype, source_name, source_title = source_by_address[addr.name]
		addr["source_label"] = address_source_label(source_doctype, source_name, title=source_title)
	return addresses
