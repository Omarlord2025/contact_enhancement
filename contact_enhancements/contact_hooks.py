# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

"""Contact doc_events for this app.

Arabic first-name rules (normalize_arabic_first_name / normalize_contact_first_name)
------------------------------------------------------------------------------
This is the single source of truth for the 5 rules - contact_enhancements/
public/js/contact.js mirrors them in JS syntax for instant client-side
feedback, but does not re-document them; this docstring is the canonical
rule table both implementations are meant to match. This app has no JS
test infrastructure at all (no package.json, no Cypress/QUnit specs
anywhere in this app), so keeping them in sync is a code-review
discipline, not something automated tests can catch on the JS side -
only the Python side has real test coverage (tests/test_contact_hooks.py).

Deliberately auto-normalize, not reject: an earlier version of this
module called frappe.throw on the first violation, blocking the save
entirely. That's the wrong default for a formatting nit a human reviewer
would just silently fix by hand anyway (the old error messages
themselves always said exactly what the fix was - "use ا instead of أ",
"use ه instead of ة") - rejecting Data Import rows or API inserts over
this is disproportionate to the mistake. Every rule below is corrected
in place instead; nothing is ever rejected here.

Applied in this exact order (each step's output feeds the next):

1. No word may start with U+0623 (ALEF WITH HAMZA ABOVE, "أ") specifically
   - not U+0622 ("آ") or U+0625 ("إ"). Plain alef (U+0627, "ا") is fine.
   Normalization: replace a word-initial "أ" with "ا".
2. No Arabic diacritics/tashkeel/tatweel: any of U+064B-U+065F, U+0670,
   or U+0640 (tatweel/kashida). Normalization: delete them - they're
   optional orthographic marks with no distinct letter identity of
   their own, so removing them is the correct fix, not a lossy one.
3. No 2+ consecutive (ASCII) spaces. Normalization: collapse any run of
   whitespace to a single space, and trim the ends.
4. Only letters (any script), combining marks, whitespace, and the
   intra-name punctuation in NAME_PUNCTUATION (apostrophe and hyphen) -
   anything else (digits, symbols) is disallowed. Normalization: replace
   each disallowed character with a space (not delete it outright) so
   e.g. "Ahmed1Ali" becomes "Ahmed Ali" - two separate words - rather
   than "AhmedAli" silently merging into one; the whitespace collapse in
   rule 3 then cleans up any resulting run of spaces. A token left with
   no letter at all is then dropped entirely, so "Ahmed - Ali" does not
   keep a bare "-" as a word.

   This was once a whitelist of Latin plus the Arabic Unicode blocks and
   nothing else, which silently destroyed every other script: Cyrillic,
   Greek, Chinese and Hebrew names were blanked outright, accented Latin
   was mangled ("Muller" -> "M ller"), and every script whose vowels are
   combining marks - Devanagari, Tamil, Bengali, Thai, pointed Hebrew -
   was reduced to its bare consonants ("राम कुमार शर्मा" -> "र म क म र
   शर म"). Both failures were reported from production. Rule 2 above
   still strips Arabic tashkeel by explicit codepoint range and runs
   first, so allowing marks here does not undo it.
5. No word may end in U+0629 (TEH MARBUTA, "ة") or U+064A (ARABIC LETTER
   YEH, "ي") specifically - not U+0649 (ALEF MAKSURA, "ى").
   Normalization: replace a word-final "ة" with "ه", and a word-final
   "ي" with "ى".

normalize_arabic_first_name applies these in a fixed pipeline order -
diacritics and disallowed characters are stripped *before* the
word-boundary-based rules (1 and 5) run, so those see the real word
boundaries rather than ones an as-yet-unstripped character was hiding
(e.g. a digit sitting directly before an "أ" would otherwise stop that
"أ" from being recognized as word-initial).

All character literals below are written as explicit \\uXXXX escapes,
never as literal Arabic characters in source - several of these codepoints
(e.g. U+0623 "أ" vs U+0622 "آ" vs U+0625 "إ" vs U+0627 "ا") are visually
near-identical, so an escape sequence is the only way to make the exact
intended codepoint unambiguous on inspection.
"""

import re
import unicodedata

import phonenumbers
from phonenumbers import PhoneNumberType

import frappe
from frappe import _

ALEF_HAMZA_ABOVE = "أ"  # أ - not آ "آ" or إ "إ"
PLAIN_ALEF = "ا"  # ا
TASHKEEL_AND_TATWEEL_PATTERN = re.compile("[ً-ٰٟـ]")
WHITESPACE_RUN_PATTERN = re.compile(r"\s+")
WORD_INITIAL_ALEF_HAMZA_PATTERN = re.compile(r"(^|\s)" + ALEF_HAMZA_ABOVE)
TEH_MARBUTA = "ة"  # ة
YEH = "ي"  # ي - not ى "ى"
ALEF_MAKSURA = "ى"  # ى
HEH = "ه"  # ه
WORD_FINAL_DISALLOWED_ENDING_PATTERN = re.compile(f"({TEH_MARBUTA}|{YEH})(?=\\s|$)")


def _strip_diacritics(name):
	"""Rule 2 normalization - delete tashkeel/tatweel outright."""
	return TASHKEEL_AND_TATWEEL_PATTERN.sub("", name)


# Punctuation that genuinely belongs inside a name rather than being junk
# to strip: the apostrophe in O'Brien / D'Angelo (both the ASCII and the
# typographic form, since real input carries either) and the hyphen in
# Anne-Marie / Al-Sayed. Treating these as symbols stored "O'Brien" as
# "O Brien" and "Ahmed Al-Sayed" as "Ahmed Al Sayed" - a silently
# corrupted name, reported from production.
NAME_PUNCTUATION = frozenset("'’-")


def _is_allowed_name_character(char):
	"""Whether a character may appear in a name: any Unicode letter,
	whitespace, or one of the intra-name punctuation marks in
	NAME_PUNCTUATION.

	This used to be an explicit character class of a-zA-Z plus the Arabic
	blocks and nothing else, which silently destroyed every other script -
	Chinese, Cyrillic, Greek, Hebrew and Japanese names were blanked
	entirely, and accented Latin was mangled ("Muller" keeping its umlaut
	became "M ller", "Jose" with an accent became "Jos"). Because
	Contact.first_name is mandatory, a blanked name meant the save failed
	outright, so a customer whose name used any of those scripts could not
	be created at all - reported from production by another app's signup
	flow.

	str.isalpha() rather than a regex character class because Python's
	own `re` has no Unicode property escapes (\\p{L}); isalpha() is True
	for letters in every script and False for digits, punctuation and
	symbols, which is exactly the rule this always meant to express.

	Combining marks (Unicode general category M*) count as part of a name
	too, and isalpha() alone is False for every one of them. Without this
	the rule destroyed every script that writes vowels as marks attached
	to a consonant rather than as separate letters - Devanagari
	"राम कुमार शर्मा" became "र म क म र शर म", and Tamil, Bengali, Thai
	and pointed Hebrew failed the same way. It looked like the name
	survived, because the consonants did.

	This does not resurrect Arabic tashkeel: those are stripped earlier by
	_strip_diacritics's own explicit codepoint ranges (rule 2), before
	this rule ever sees them. Rule 2 stays a deliberate Arabic-specific
	normalization; this is only about not shredding scripts whose vowels
	are marks by construction.

	The Arabic-specific rules elsewhere in this module (word-initial alef
	hamza, word-final teh marbuta/yeh) are unaffected by widening this:
	each targets specific Arabic codepoints, so they are inert on text in
	any other script.

	Args:
		char: a single character.

	Returns:
		True if it may stay in a name.
	"""
	return (
		char.isalpha()
		or char.isspace()
		or char in NAME_PUNCTUATION
		or unicodedata.category(char).startswith("M")
	)


def _strip_disallowed_characters(name):
	"""Rule 4 normalization - replace a disallowed character (digit,
	symbol) with a space rather than deleting it, so it can't silently
	merge two words into one."""
	return "".join(char if _is_allowed_name_character(char) else " " for char in name)


def _drop_letterless_tokens(name):
	"""Remove any whitespace-separated token that carries no letter at all.

	Needed only because NAME_PUNCTUATION lets apostrophes and hyphens
	survive rule 4: without this, input like "Ahmed - Ali" would keep a
	bare "-" as a word of its own, and "'''" would normalize to itself
	rather than to nothing. A token has to contain at least one letter to
	be part of a name; punctuation belongs *inside* a component, never as
	one.

	Args:
		name: the partially-normalized name.

	Returns:
		The name with letterless tokens removed.
	"""
	return " ".join(token for token in name.split() if any(char.isalpha() for char in token))


def _collapse_whitespace(name):
	"""Rule 3 normalization - collapse any whitespace run (including
	ones introduced by _strip_disallowed_characters above) to a single
	space, and trim the ends."""
	return WHITESPACE_RUN_PATTERN.sub(" ", name).strip()


def _normalize_word_initial_alef_hamza(name):
	"""Rule 1 normalization - a word-initial "أ" becomes plain "ا"."""
	return WORD_INITIAL_ALEF_HAMZA_PATTERN.sub(r"\1" + PLAIN_ALEF, name)


def _normalize_word_final_endings(name):
	"""Rule 5 normalization - a word-final "ة" becomes "ه", and a
	word-final "ي" becomes "ى"."""

	def _replacement(match):
		return HEH if match.group(1) == TEH_MARBUTA else ALEF_MAKSURA

	return WORD_FINAL_DISALLOWED_ENDING_PATTERN.sub(_replacement, name)


def normalize_arabic_first_name(first_name):
	"""Apply the 5 rules documented at the top of this module to
	first_name and return the corrected value - see the module docstring
	for the exact rule table, pipeline order, and why this normalizes
	instead of rejecting.

	Args:
		first_name: the value to normalize; a no-op if blank.

	Returns:
		The normalized first_name (identical to the input if it already
		satisfied every rule, or if it was blank).
	"""
	if not first_name:
		return first_name

	name = first_name
	name = _strip_diacritics(name)
	name = _strip_disallowed_characters(name)
	name = _collapse_whitespace(name)
	name = _drop_letterless_tokens(name)
	name = _normalize_word_initial_alef_hamza(name)
	name = _normalize_word_final_endings(name)
	return name


def normalize_contact_first_name(doc, method=None):
	"""Contact validate doc_event - normalizes doc.first_name in place
	(contact_enhancements/public/js/contact.js has the client-side mirror
	for instant feedback before the round-trip to the server). This is a
	genuine, unbypassable normalization step, not just a UI nudge: a
	validate hook is the only place that also covers the REST API and
	Data Import, which never run form JS at all.

	Recomputes full_name afterwards, and that is not optional. Frappe's own
	Contact.validate() sets full_name from first/middle/last BEFORE this
	hook runs - the controller's own validate() always precedes any
	doc_events-registered hook for the same event (Document.hook's
	compose()) - so normalizing first_name here left full_name holding the
	pre-normalization text. The two fields then disagreed permanently: a
	Contact typed "أحمد محمد على" stored first_name "احمد محمد على" and
	full_name "أحمد محمد على".

	That is not cosmetic. full_name is what this app searches on, what it
	names a Customer/Supplier from (utils.party_identity_from_contact), and
	what propagate_contact_changes_to_linked_doctypes pushes out to every
	linked record - so the un-normalized form was the one that travelled.
	It also silently broke the duplicate-name lookup, which compares
	normalized typed text against stored full_name and could never match.

	Exactly the same failure mode as the stale doc.mobile_no this module
	already re-syncs at the end of normalize_and_validate_contact_phones,
	and for exactly the same ordering reason.

	Args:
		doc: the Contact document being validated.
		method: unused, present for the doc_events hook signature.
	"""
	doc.first_name = normalize_arabic_first_name(doc.first_name)
	doc.full_name = doc._get_full_name()


def validate_full_name_has_at_least_three_words(full_name):
	"""Raise unless full_name is blank, or has at least three space-
	separated words - e.g. "Ahmed Mohamed Sabry" (first, father's, and
	family name, the common convention this app's own dialogs assume by
	asking for one combined "Full Name" field instead of separate first/
	last inputs), not "Ahmed" or "Ahmed Mohamed" alone.

	Blank is deliberately allowed through here untouched - this only ever
	enforces *shape* once a name is actually present; whether one is
	required at all is a separate, doctype-specific concern (a native
	reqd=1 Property Setter, or the calling dialog's own "enter a name"
	check).

	Deliberately NOT wired as a blanket Contact validate() doc_event, the
	way normalize_contact_first_name above is - confirmed the risk before
	adding this: ERPNext's own native Lead.create_contact() (erpnext/crm/
	doctype/lead/lead.py) sets a brand-new Contact's first_name to just
	the Lead's own single first_name field, with last_name held
	separately - a blanket Contact-level hook here would reject that
	native flow's own Contact the same way Contact.country's own reqd=1
	once broke it (contact_enhancements/CLAUDE.md). Scoped instead to
	exactly the paths this app itself owns and controls: api.contact_
	lookup.create_minimal_contact (every "create a new Contact" dialog
	this app has - Customer/Supplier/Employee/User all funnel through it,
	never called by any native ERPNext flow) and Employee.
	first_name directly (employee_hooks.enforce_full_name_has_at_least_
	three_words) - Employee has no equivalent native single-word-name
	creation flow (confirmed: the one native programmatic Employee
	creation site, erpnext's own setup wizard "create employee for self",
	leaves first_name blank entirely and relies on ignore_mandatory to
	bypass the separate reqd check - unaffected by this function's own
	blank-is-a-noop behavior).

	Args:
		full_name: the value about to become a "Full Name" field.

	Raises:
		frappe.ValidationError if non-blank with fewer than three words.
	"""
	if not full_name:
		return
	if len(full_name.split()) < 3:
		frappe.throw(
			_("Enter a full name with at least three words (e.g. first, father's, and family name).")
		)


"""Per-country phone validation (normalize_and_validate_contact_phone /
_detect_phone_country / normalize_and_validate_contact_phones)
------------------------------------------------------------------------------
Replaces what used to be an Egypt-only hardcoded regex
(api/lead_lookup.EGYPT_MOBILE_PATTERN, since removed) with real validation
against every country's own numbering plan, via the phonenumbers library
(Google's libphonenumber, Python port) - the same library and approach
custom_webshop already uses for its own E.164 identity-matching column
(see custom_webshop.signup.identity), so this app now follows the exact
same precedent instead of inventing a second approach to the same problem.

Which country a number is checked against is Contact Phone.country
(setup/custom_fields.py) - one field per *row*, not one for the whole
Contact: a person can genuinely carry a local mobile and a foreign one
side by side, and "pick a country, its rules apply" only makes sense read
as applying to the number sitting in that same row. An earlier version of
this put country on Contact itself; moved down to the row after that
turned out wrong for exactly that reason. It's mandatory with a DocField
default of "Egypt" (this app's own pre-existing default everywhere else a
country choice was needed), so a row created by a path that never asks
still ends up with a valid record.

That default alone isn't enough, though - confirmed the hard way: ERPNext's
own native Lead.create_contact() (erpnext/crm/doctype/lead/lead.py) copies
lead.phone/lead.mobile_no verbatim onto a brand-new Contact and inserts it
*before* the Lead itself even has a name, with no Dynamic Link back to the
Lead yet either - there is no hook point in this app that can reach in and
supply the Lead's real country on that row first. A non-Egyptian Lead's
own phone number would then fail validation against the "Egypt" default,
breaking Lead creation site-wide, not just something this app owns. Two
things close that gap without needing to touch (or duplicate) any ERPNext
core code:

1. A number's own unambiguous country code (a "+"/"00" prefix) is always
   honored over whatever country is currently selected - confirmed
   empirically: phonenumbers.parse("+447400123456", "EG") still correctly
   returns a UK number; the region hint only disambiguates a number with
   *no* country code of its own. _detect_phone_country uses exactly this
   to self-correct that row's own country the moment such a number is
   entered - see normalize_and_validate_contact_phones.
2. Validation is never gated on the row's own custom_landline flag
   matching the number's real type (landline vs mobile) - any number
   genuinely valid for the country is accepted regardless. That flag is a
   purely descriptive tag (same as custom_whatsapp/custom_telegram), not
   a rule the number itself has to satisfy - matching this app's own
   "normalize/accept a real number, don't reject it over a formatting or
   classification nitpick" philosophy (see this module's Arabic
   first-name rules above). This is also what actually satisfies "still
   be able to add landline numbers": the old Egypt-only regex rejected
   every landline-shaped number outright, with no flag able to rescue it.

What's still a genuine, unavoidable gap: a bare local-format number (no
country code of its own) for a country other than whatever's currently
selected on that row - phonenumbers has no way to guess a "07911 123456"
-shaped number is British rather than Egyptian without either a prefix or
external context this app doesn't have. In practice this only bites a row
whose country was never deliberately set and whose number was also
entered without a country code - documented here, not engineered around
further.

Deliberately not mirrored in JS the way the Arabic first-name and old
Egypt-only phone rules were: phonenumbers is a large per-country dataset
(numbering plans, national significant number lengths, and so on for
every country in the world), not a handful of regexes worth
reimplementing by hand, and there is no equivalent already loaded in this
app's frontend bundle. The mandatory contact-picker dialog
(public/js/customer.js) still gets a clear validation error the moment it
submits - just via the existing frappe.call round-trip and its own error()
callback, not an instant keystroke check - which is the same tradeoff
this app already documented and accepted for search_contacts_with_details'
own error handling (see contact_enhancements/CLAUDE.md).
"""


def _phonenumbers_region_for_country(country):
	"""Resolve the phonenumbers region code (e.g. "EG", "GB") for a
	Country doctype record.

	Args:
		country: name (docname) of a Country record - e.g. "Egypt".

	Returns:
		The two-letter ISO region code, upper-cased.

	Raises:
		frappe.ValidationError: if country is blank, or the Country record
			has no "code" configured (frappe.geo.doctype.country's own ISO
			alpha-2 field) - every real Country record Frappe ships with
			has one (confirmed: 250 of 251 on this site), so this only
			fires for a genuinely blank/misconfigured Country record.
	"""
	if not country:
		frappe.throw(_("Select a Country before entering a phone number."))

	# Cached, not frappe.db.get_value: this runs once per phone row per
	# save, and in practice every row on a Contact carries the same
	# country - so an uncached read re-fetches the identical static
	# reference row N times per save. frappe.get_cached_value returns None
	# for a missing record exactly like frappe.db.get_value does (it
	# swallows DoesNotExistError), so the blank-code throw below is
	# unaffected, and Frappe invalidates this cache itself whenever the
	# Country record is saved.
	code = frappe.get_cached_value("Country", country, "code")
	if not code:
		frappe.throw(
			_("{0} has no phone dialing code configured - fix its Country record first.").format(country)
		)
	return code.upper()


def strip_phone_formatting_noise(raw):
	"""Trim whitespace and collapse dashes/dots/parens out of a phone
	number, without attempting to parse or validate it.

	Deliberately does not use phonenumbers at all - this is for a context
	that has no country to check against yet (see user_hooks.py's
	validate_user_phone_before_contact_sync) and must never raise. Always
	safe to apply: it can only remove characters phonenumbers itself would
	also ignore, never change what number is actually meant.

	Args:
		raw: the phone number as typed, or None.

	Returns:
		The cleaned string, or the original falsy value unchanged (never
		coerces None to "").
	"""
	if not raw:
		return raw
	return re.sub(r"[\s\-().]", "", raw)


def try_to_e164(phone, country=None):
	"""Best-effort E.164 form of a phone number, for comparison only - never
	raises, never used to decide what gets stored or accepted.

	Needed because Contact Phone.phone is E.164 once normalized (see
	normalize_and_validate_contact_phone), but not every phone-shaped value
	this app compares against it has been through that normalization yet -
	e.g. Lead.whatsapp_no, a flat field this app doesn't own or validate.
	Comparing such a raw value against an already-E.164 row with a bare
	`==` silently never matches even for the same real number (see
	api/lead_lookup._sync_whatsapp_to_contact, which hit exactly this).
	Route both sides of a comparison through this first instead.

	Same name and approach as custom_webshop.signup.identity's own
	try_to_e164 - this app follows that established precedent rather than
	inventing a second one.

	Args:
		phone: the phone number to canonicalize, in any format.
		country: name of a Country record, used only to disambiguate a
			number with no country code of its own. Optional - an
			already-E.164 phone (leading "+") does not need it, since an
			explicit country-code prefix is always honored over any region
			hint.

	Returns:
		The E.164 form, or the original value unchanged if it's blank,
		unparseable, or not valid for the resolved region.
	"""
	if not phone:
		return phone

	region = None
	if country:
		try:
			region = _phonenumbers_region_for_country(country)
		except frappe.ValidationError:
			region = None

	try:
		parsed = phonenumbers.parse(phone, region)
	except phonenumbers.NumberParseException:
		return phone

	if not phonenumbers.is_valid_number(parsed):
		return phone

	return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def _detect_phone_country(phone):
	"""The Country a phone number's own country code identifies, if it
	has one - see this section's own module-level docstring.

	Only ever succeeds for a number written with an unambiguous "+"/"00"
	prefix; a plain local-format number has no country code of its own to
	go by.

	Args:
		phone: the raw phone number as typed.

	Returns:
		Name of the matching Country record, or None if the number has no
		country code of its own, doesn't parse, or isn't valid for
		whatever country it claims.
	"""
	raw = (phone or "").strip()
	if not raw:
		return None

	try:
		parsed = phonenumbers.parse(raw, None)
	except phonenumbers.NumberParseException:
		return None

	if not phonenumbers.is_valid_number(parsed):
		return None

	region = phonenumbers.region_code_for_number(parsed)
	return _country_for_region_code(region)


def _country_for_region_code(region):
	"""Country record name for a phonenumbers region code ("EG" -> "Egypt"),
	memoized for the life of the request.

	Country.code carries no index, so this lookup is a scan of tabCountry,
	and _detect_phone_country calls it once per phone row on every Contact
	save - with every row on a Contact almost always resolving to the same
	country, so the identical scan is repeated N times per save for one
	distinct answer.

	Request-scoped rather than frappe.cache()/get_cached_value: this is a
	reverse lookup by a *field value*, not by docname, so Frappe's own
	document cache can't key it and wouldn't be invalidated if a Country's
	code were ever edited. A per-request memo removes the repetition
	within a save - which is the entire cost here - and cannot go stale.

	Args:
		region: two-letter ISO region code from phonenumbers, or None.

	Returns:
		Name of the matching Country record, or None.
	"""
	if not region:
		return None

	code = region.lower()
	cache = getattr(frappe.local, "contact_enhancements_country_by_code", None)
	if cache is None:
		cache = {}
		frappe.local.contact_enhancements_country_by_code = cache
	if code not in cache:
		cache[code] = frappe.db.get_value("Country", {"code": code}, "name")
	return cache[code]


def normalize_and_validate_contact_phone(phone, country, is_landline=False):
	"""Validate one phone number against a specific country's own
	numbering plan, and return it in international E.164 form (e.g.
	"+201012345678", not "01012345678" or "010 1234 5678").

	This is a deliberate reversal of this app's own earlier convention of
	storing the local dialing form - done for genuine reasons (one
	canonical format instead of one-per-country ambiguity, direct
	WhatsApp/Telegram click-to-chat compatibility, no future dependence on
	the row's own country to interpret the stored value), not a casual
	rename. See docs/e164_impact_audit.md for the bench-wide audit of
	every place this format change could matter, performed before this
	change shipped.

	Args:
		phone: the raw phone number as typed.
		country: name of a Country record - which numbering plan to check
			the number against if it has no country code of its own (see
			this section's own module-level docstring).
		is_landline: the row's own custom_landline flag - used only to
			pick a more relevant example number in a validation error;
			never gates acceptance (see this section's own module-level
			docstring for why).

	Returns:
		The normalized phone number in E.164 form.

	Raises:
		frappe.ValidationError: if the number can't be parsed, or isn't
			valid for the selected country (and carries no country code
			of its own that would say otherwise).
	"""
	region = _phonenumbers_region_for_country(country)
	raw = (phone or "").strip()
	if not raw:
		frappe.throw(_("Enter a phone number."))

	try:
		parsed = phonenumbers.parse(raw, region)
	except phonenumbers.NumberParseException:
		parsed = None

	if not parsed or not phonenumbers.is_valid_number(parsed):
		example = phonenumbers.example_number_for_type(
			region, PhoneNumberType.FIXED_LINE if is_landline else PhoneNumberType.MOBILE
		)
		hint = (
			_(" Example: {0}").format(
				phonenumbers.format_number(example, phonenumbers.PhoneNumberFormat.NATIONAL)
			)
			if example
			else ""
		)
		frappe.throw(_("{0} is not a valid phone number for {1}.{2}").format(raw, country, hint))

	return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def _national_digits(phone_e164):
	"""Bare national-significant-number digits, no country code, no
	formatting - e.g. "01012345678" for "+201012345678" - for populating
	Contact Phone.custom_phone_national.

	Exists because phone itself is now always E.164 (a genuine, deliberate
	convention change from the local form this app used to store), but a
	LIKE search against an E.164 value can't reliably find a Contact by
	the local-format number staff are used to typing - the country-code
	digits get in the way for every country except (coincidentally,
	fragile even then) Egypt. custom_phone_national is a hidden,
	search-only auxiliary field this app's search queries also match
	against - see api/lead_lookup._contact_search_query.

	Never raises: phone_e164 has already been validated E.164 by the
	caller (normalize_and_validate_contact_phone already ran on it), so
	re-parsing it here (region-less - the "+" prefix is self-describing)
	cannot fail. This reproduces exactly the local-form value this app
	used to return as its primary phone format, before the E.164
	conversion - same phonenumbers.PhoneNumberFormat.NATIONAL + digit-
	strip technique, just now feeding a secondary field instead of the
	primary one.

	Args:
		phone_e164: an already-normalized E.164 phone number.

	Returns:
		The national digits only, as a string.
	"""
	parsed = phonenumbers.parse(phone_e164)
	national = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.NATIONAL)
	return re.sub(r"\D", "", national)


def _running_in_background_job():
	"""True only inside a genuine async RQ worker execution - never for an
	interactive Desk save, Data Import, a test, or frappe.enqueue(...,
	now=True).

	Confirmed by reading frappe/utils/background_jobs.py: frappe.local.job
	is set exclusively inside execute_job(), the wrapper RQ actually calls
	for the real is_async=True path. enqueue()'s own now=True/
	(not is_async and frappe.flags.in_test) branch calls the target
	function directly via frappe.call(...) instead, never going through
	execute_job() at all - so a test exercising this has to explicitly set
	frappe.local.job itself to simulate the real worker context, it is
	never set implicitly just by running under FrappeTestCase.

	Returns:
		True if this code is executing inside a real background job.
	"""
	return bool(getattr(frappe.local, "job", None))


def normalize_and_validate_contact_phones(doc, method=None):
	"""Contact validate doc_event - runs every Contact Numbers row through
	normalize_and_validate_contact_phone against that row's own country,
	replacing its phone value with its normalized E.164 form, and
	populating custom_phone_national from that same value (see
	_national_digits) so this app's own phone search can still find a
	Contact by the local-format number staff actually type.

	Before validating each row, checks whether that row's own raw number
	identifies a different, genuine country than its own country field
	currently says (_detect_phone_country) - and if so, corrects that
	row's country to match before validating it. Only ever moves from
	"wrong/default" to "what the number itself says"; a plain local-format
	number (no country code of its own) never triggers this.

	A row with no phone value yet is skipped entirely - including one
	with a blank country, so the real "Country is mandatory" error
	(Frappe's own generic mandatory-field check, which runs regardless of
	this hook) is the one a user sees for an empty new row, not a
	confusing phone-specific error from here first. A row that does have
	a phone value but a blank country falls through to
	normalize_and_validate_contact_phone's own "select a Country" error
	instead - in practice this is essentially never reached, since the
	field has a DocField default.

	Args:
		doc: the Contact document being validated.
		method: unused, present for the doc_events hook signature.

	Inside a genuine background job (frappe.local.job set - see
	_running_in_background_job below), a row that can't be validated is
	dropped instead of raising: confirmed via frappe/utils/background_jobs.py
	that frappe.local.job is only ever set for the real async RQ execution
	path (execute_job()), never for enqueue(..., now=True) or the
	is_async=False/in_test path both of which call the method directly -
	so this only ever softens behavior for a job with no interactive human
	watching, never for an interactive save, Data Import (which wants a
	clear per-row failure reported, not a silent drop), or a test that
	hasn't deliberately simulated a job context. Closes the regression
	this app's own phone validation introduced for User.create_contact()'s
	background sync (see user_hooks.py) generally, for any future
	background job that touches Contact too, not just that one.

	Only ever rescues a plausible-shaped-but-invalid number (wrong prefix,
	wrong digit count) - genuine garbage (letters, symbols outside
	"+-.,*#()") never reaches this function at all. Confirmed by
	reproducing it directly: Contact Phone.phone is itself an
	options="Phone" field, so Frappe's own generic
	Document._validate_data_fields() (base_document.py) already runs
	frappe.utils.validate_phone_number against every row before
	run_method("validate") - where every hook in this file lives - ever
	executes, regardless of background-job context. This app has no way
	to override that earlier, core check.
	"""
	lenient = _running_in_background_job()
	rows_to_drop = []

	for row in doc.get("phone_nos", []):
		if not row.phone:
			continue

		detected_country = _detect_phone_country(row.phone)
		if detected_country and detected_country != row.country:
			row.country = detected_country

		try:
			row.phone = normalize_and_validate_contact_phone(
				row.phone, row.country, is_landline=row.custom_landline
			)
			row.custom_phone_national = _national_digits(row.phone)
		except frappe.ValidationError:
			if not lenient:
				raise
			frappe.log_error(
				title="contact_enhancements: dropped unparseable phone in background job",
				message=f"Contact {doc.name or '(new)'}: could not validate {row.phone!r} "
				f"against {row.country!r} - row dropped, no interactive user to ask.",
			)
			rows_to_drop.append(row)

	for row in rows_to_drop:
		doc.phone_nos.remove(row)

	# Re-sync doc.mobile_no from the now-normalized rows - Contact's own
	# native validate() (Document.hook's compose() ordering guarantees it
	# always runs before this doc_events-registered hook, never after)
	# already called self.set_primary("mobile_no") once, reading row.phone
	# as it stood *before* the normalization loop above - so doc.mobile_no
	# is left holding the pre-normalization raw value for the rest of this
	# save unless something re-derives it. Confirmed the hard way: this
	# matters beyond display - Customer/Supplier's own mobile_no fields
	# are native fetch_from="...primary_contact.mobile_no" (setup/
	# custom_fields.py's own docs), so a stale, wrong-format value here
	# would have propagated there too on their own next fetch. Cheap to
	# re-run (a single pass over rows already in memory, no extra query).
	doc.set_primary("mobile_no")


def enforce_unique_mobile_number(doc, method=None):
	"""Contact validate doc_event - the hard-blocking layer for genuine
	mobile-number duplicates. contact_enhancements.api.contact_dedupe.
	warn_if_duplicate_contact stays the soft, non-blocking layer for
	everything else (email matches, and landline "duplicates" that are
	usually legitimate, like a shared office line); this is specifically
	for mobiles, and it does raise.

	Raises frappe.UniqueValidationError specifically, never a bare
	frappe.ValidationError - this is a deliberate public contract, not an
	implementation detail. Other apps on this bench create Contacts inside
	their own flows (custom_webshop's signup, for one) and need to catch
	"this number is taken" to recover from it, without also swallowing
	every unrelated validation failure this app's other Contact hooks can
	raise (a bad phone format, a name that isn't three words, a blank
	country). A bare ValidationError forces them into a catch broad enough
	to hide real bugs. Keep the exception type stable.

	Only checks a row that's new or has a changed phone value this save -
	the same legacy-safe gating this app already uses for
	_resync_modified_after_side_effect_saves, so an unrelated resave of an
	already-conflicting legacy row (one that predates this check) doesn't
	suddenly start failing. Skips landline rows outright - a shared
	landline was never the problem this exists to solve.

	Checks two distinct kinds of conflict, both of which the database-
	level UNIQUE INDEX (add_contact_phone_unique_mobile_index) rejects
	equally and neither of which this app can tell apart at that layer:
	another Contact already having this number (find_contacts_by_phone),
	and *this same Contact* already having it on a different row of its
	own phone_nos. The second case is not hypothetical - confirmed
	against real legacy data on a production-shaped site where the exact
	same number existed twice on one Contact, differing only in which row
	had is_primary_mobile_no set, and slipped straight past this
	function's own original design (which only ever searched *other*
	Contacts) all the way to a raw DB IntegrityError when the unique
	index patch ran. See contact_dedupe.find_duplicate_phone_rows_within_
	contact for the equivalent one-time/report-side detection of this
	same failure mode in already-saved data.

	Deliberately does NOT use row.has_value_changed("phone") - confirmed
	by reading Document.load_doc_before_save(): it only ever populates
	_doc_before_save on the *top-level* document being saved, never on
	each child row individually, so has_value_changed on a child row
	always sees previous=None and always returns True (has_value_changed's
	own "if not previous: return True" fallback) - silently defeating this
	exact gate for every row, every save, always. Instead, this looks up
	each row's own previous value by name from doc.get_doc_before_save()'s
	own phone_nos, the same snapshot the parent Contact's own has_value_
	changed already relies on being populated by check_if_latest() before
	validate() ever runs.

	This is the friendly, immediate, actionable-error UX layer. It is not
	by itself a concurrency guarantee - two simultaneous inserts can both
	pass this check before either commits (a genuine TOCTOU race). The
	real, unconditional guarantee is the database-level generated-column
	UNIQUE INDEX added by patches/add_contact_phone_unique_mobile_index.py.
	In the rare case a race slips past this hook, that constraint still
	raises a duplicate-key error on the losing insert - Frappe's own
	BaseDocument.db_insert()/db_update() already catches that generically
	(frappe.db.is_unique_key_violation) and re-raises it as a
	frappe.UniqueValidationError with a msgprint, not a raw SQL traceback,
	so this app doesn't need to (and doesn't) duplicate that handling here.

	Must run after normalize_and_validate_contact_phones - it depends on
	row.phone already being validated E.164, not whatever a user typed.

	Args:
		doc: the Contact document being validated.
		method: unused, present for the doc_events hook signature.

	Raises:
		frappe.ValidationError: naming the conflicting Contact, if another
			Contact already has this exact mobile number.
	"""
	from contact_enhancements.api.contact_dedupe import find_contacts_by_phone

	exclude = doc.name if not doc.is_new() else None

	previous_doc = doc.get_doc_before_save()
	previous_phones = (
		{row.name: row.phone for row in previous_doc.get("phone_nos", [])} if previous_doc else {}
	)

	live_rows = [row for row in doc.get("phone_nos", []) if not row.custom_landline and row.phone]

	for row in live_rows:
		if not row.is_new() and previous_phones.get(row.name) == row.phone:
			continue

		# Identity (is not), not row.name equality: a brand-new, unsaved
		# row's own name is None until Document.set_name_in_children()
		# assigns real ones during a genuine save (confirmed by testing
		# this function directly against a freshly frappe.new_doc()'d
		# Contact with two just-appended rows - both still had
		# name=None at that point, making a name-equality check treat
		# every pair of new rows as "the same row" and never fire).
		same_contact_conflict = any(
			other is not row and other.phone == row.phone for other in live_rows
		)
		if same_contact_conflict:
			frappe.throw(
				_("{0} is already used by another number row on this same Contact.").format(row.phone),
				title=_("Duplicate Mobile Number"),
				exc=frappe.UniqueValidationError,
			)

		conflicts = find_contacts_by_phone(row.phone, exclude=exclude)
		if conflicts:
			frappe.throw(
				_("{0} is already used by another Contact ({1}).").format(row.phone, conflicts[0]),
				title=_("Duplicate Mobile Number"),
				exc=frappe.UniqueValidationError,
			)


"""Landline / mobile-channel exclusivity (_enforce_landline_exclusivity /
enforce_contact_phone_channel_exclusivity)
------------------------------------------------------------------------------
A Contact Phone row can't be both a landline and mobile-capable: a
landline can't receive WhatsApp or Telegram, and isn't a primary mobile
number. custom_landline (setup/custom_fields.py) and the three
mobile-oriented flags (is_primary_mobile_no, custom_whatsapp,
custom_telegram) are mutually exclusive on any one row.

_enforce_landline_exclusivity only ever sees a static snapshot at
validate time - it has no idea which checkbox a user clicked most
recently (that ordering only exists live, in the browser - see
public/js/contact.js's own field-by-field handlers, which react to
whichever specific box was just toggled), so the one deterministic rule
it can apply here is: whenever both sides end up set at once,
custom_landline wins and the other three are cleared, never the other
way round.

Silently corrects rather than rejecting - matching this app's own
established "normalize a formatting/consistency mistake instead of
blocking the save over it" philosophy (see this module's Arabic
first-name rules) - a checkbox combination is exactly that kind of
mistake, not a case where the business genuinely needs the save refused.

Mirrored live in public/js/contact.js via frappe.ui.form.on("Contact
Phone", {...}) - Contact Phone is only ever embedded by Contact itself
(confirmed by grepping every doctype definition in this bench for
"options": "Contact Phone" - see contact_enhancements/CLAUDE.md), so
that one client script and this one Contact validate hook are already
the complete picture; there is no other parent doctype's own form or
save cycle this also needs to reach.
"""

MOBILE_ORIENTED_PHONE_FLAGS = ("is_primary_mobile_no", "custom_whatsapp", "custom_telegram")


def _enforce_landline_exclusivity(row):
	"""Make one Contact Phone row's landline/mobile-channel flags
	mutually exclusive - see this section's own module-level docstring.

	Args:
		row: one Contact Phone child row (mutated in place).
	"""
	if row.custom_landline:
		for fieldname in MOBILE_ORIENTED_PHONE_FLAGS:
			row.set(fieldname, 0)
	elif any(row.get(fieldname) for fieldname in MOBILE_ORIENTED_PHONE_FLAGS):
		row.custom_landline = 0


def enforce_contact_phone_channel_exclusivity(doc, method=None):
	"""Contact validate doc_event - runs every Contact Numbers row through
	_enforce_landline_exclusivity.

	Args:
		doc: the Contact document being validated.
		method: unused, present for the doc_events hook signature.
	"""
	for row in doc.get("phone_nos", []):
		_enforce_landline_exclusivity(row)


"""Continuous propagation of Contact's own name/email/phone to every
linked doctype (propagate_contact_changes_to_linked_doctypes)
------------------------------------------------------------------------------
Every other piece of "sync from Contact" logic in this app (customer_hooks,
supplier_hooks, employee_hooks, user_hooks) is deliberately *initial
inheritance, not continuous sync* - each only ever fires once, the moment a
primary-contact field is first set, precisely so a later change to the
Contact never retroactively overwrites a value someone may have since
edited independently (see e.g. tests/test_opportunity_hooks.py's own
dedicated test for that guarantee, back when Opportunity was still part of
this app).

This is the deliberate exception, added on explicit product direction:
"changing the name of the contact... changes in other doctypes linked to
it also... the email and phone nos and so on" - Contact is meant to be
the one genuine, continuously-authoritative source of truth for a
person's own name/email/phone, not just a one-time seed. Whenever any of
those three actually changes on the Contact itself, it's pushed out to
every Customer/Supplier/Employee/User record that names this Contact as
its own primary contact - immediately, via a direct frappe.db.set_value
(not a full doc .save(), which would re-trigger each target's own
doc_events and risk a needless cascade), regardless of whether that
target record is ever independently re-saved again.

Customer/Supplier's own mobile_no/email_id are already native fetch_from
fields (customer_primary_contact.mobile_no, etc. - confirmed by reading
each doctype's own JSON), but fetch_from only ever re-pulls a value when
the *target* document (the Customer/Supplier) is itself saved again - not
reactively the moment the *source* Contact changes independently. This
push-based hook is what actually makes propagation immediate and
unconditional, the same way for all four doctypes; it doesn't conflict
with fetch_from where it does apply - the value it re-fetches on the
target's own next save is already this same, freshly-pushed one.
"""

# name_sync_requires: (fieldname, {values that allow the name sync}).
# A Customer or Supplier is only the *same entity* as its primary contact
# when it's an Individual - a Company or a Partnership has its own trading
# name that has nothing to do with whichever person happens to be the
# contact for it, and overwriting it with that person's name is data loss,
# reported from production. Deliberately an allowlist ("sync only when
# Individual") rather than a denylist ("skip when Company"): both fields
# offer Company/Individual/Partnership, so a denylist would still clobber
# every Partnership. Employee and User carry no such field - both are
# always a person - so neither needs a condition.
_CONTACT_SYNC_TARGETS = {
	"Customer": {
		"contact_fieldname": "customer_primary_contact",
		"name_field": "customer_name",
		"name_sync_requires": ("customer_type", {"Individual"}),
		"email_field": "email_id",
		"phone_field": "mobile_no",
	},
	"Supplier": {
		"contact_fieldname": "supplier_primary_contact",
		"name_field": "supplier_name",
		"name_sync_requires": ("supplier_type", {"Individual"}),
		"email_field": "email_id",
		"phone_field": "mobile_no",
	},
	"Employee": {
		"contact_fieldname": "employee_primary_contact",
		"name_field": "first_name",
		"email_field": "personal_email",
		"phone_field": "cell_number",
	},
	"User": {
		"contact_fieldname": "user_primary_contact",
		"name_field": "first_name",
		# email deliberately excluded - User.email is that document's own
		# naming field; overwriting it means renaming the User
		# (frappe.rename_doc), with real consequences for login/sessions/
		# audit history that a background sync hook has no business
		# deciding on its own.
		"email_field": None,
		"phone_field": "mobile_no",
	},
}


def propagate_contact_changes_to_linked_doctypes(doc, method=None):
	"""Contact on_update doc_event - see this section's own module
	docstring above for the full reasoning (continuous sync, deliberately
	the one exception to this app's usual "initial inheritance only"
	rule).

	Only touches fields that actually changed this save (has_value_changed
	against each of full_name/email_id/mobile_no independently) and only
	ever writes a genuinely non-blank value - never blanks out a target's
	own field just because this Contact's own value happens to be empty.

	Args:
		doc: the Contact document that was just saved.
		method: unused, present for the doc_events hook signature.
	"""
	changed_name = doc.full_name if doc.has_value_changed("full_name") else None
	changed_email = doc.email_id if doc.has_value_changed("email_id") else None
	changed_phone = doc.mobile_no if doc.has_value_changed("mobile_no") else None
	if not (changed_name or changed_email or changed_phone):
		return

	for doctype, cfg in _CONTACT_SYNC_TARGETS.items():
		# The type field (if this doctype has one) is read in the same
		# query as the names - it decides per record whether the name may
		# be synced at all, so it can't be a separate lookup per record.
		name_gate = cfg.get("name_sync_requires")
		fields = ["name"] + ([name_gate[0]] if name_gate else [])
		records = frappe.get_all(doctype, filters={cfg["contact_fieldname"]: doc.name}, fields=fields)
		if not records:
			continue

		# Fields that apply to every record of this doctype regardless of
		# type - only the name is ever conditional.
		shared_updates = {}
		if changed_email and cfg.get("email_field"):
			shared_updates[cfg["email_field"]] = changed_email
		if changed_phone and cfg.get("phone_field"):
			shared_updates[cfg["phone_field"]] = changed_phone

		for record in records:
			updates = dict(shared_updates)
			if changed_name and cfg.get("name_field") and _name_sync_allowed(record, name_gate):
				updates[cfg["name_field"]] = changed_name
			if not updates:
				continue
			_set_values_without_crashing_the_save(doctype, record["name"], updates)


def _name_sync_allowed(record, name_gate):
	"""Whether this particular target record's own name may be overwritten
	with its primary contact's name.

	Args:
		record: the target row, already carrying the gate's own field.
		name_gate: (fieldname, {allowed values}) from _CONTACT_SYNC_TARGETS,
			or None for a doctype that is always a person.

	Returns:
		True when there's no gate, or the record's value is allowed.
	"""
	if not name_gate:
		return True
	fieldname, allowed_values = name_gate
	return record.get(fieldname) in allowed_values


def _set_values_without_crashing_the_save(doctype, record_name, updates):
	"""frappe.db.set_value, but a failure here can never take down the
	Contact save that triggered it - confirmed the hard way in production:
	tabUser.mobile_no carries a native UNIQUE index (frappe/core/doctype/
	user/user.json - not something this app added or controlled), so a
	Contact linked as the primary contact for *two different* Users (a
	real, legitimate case - the same person can genuinely hold more than
	one account) crashed the entire save with a raw pymysql
	IntegrityError the instant the second User's own mobile_no write
	collided with the first. A background "keep things in sync" side
	effect must never be allowed to block the primary action (saving the
	Contact itself) that triggered it - the same principle already
	applied to user_hooks.validate_user_phone_before_contact_sync and
	api.contact_dedupe.warn_if_duplicate_contact elsewhere in this app.

	Wrapped in an explicit SQL SAVEPOINT (frappe.db.savepoint/rollback(
	save_point=...)), NOT a plain try/except calling frappe.db.rollback()
	directly - confirmed by reading frappe.db.rollback()'s own
	implementation (frappe/database/database.py) that a bare rollback()
	with no save_point undoes the *entire* current transaction (`ROLLBACK`
	+ a fresh `BEGIN`), which spans the whole request, not just this one
	statement - it would have silently discarded the Contact's own
	already-applied save alongside the failed propagation, while still
	reporting success to the user. A SAVEPOINT undoes only the statements
	issued inside this one block, leaving everything else in the same
	transaction - including the Contact's own save - intact. The same
	mechanism frappe.core.doctype.doctype.doctype.py's own
	@savepoint(catch=Exception) decorator wraps (frappe.database.
	savepoint) - used directly here instead of that decorator/context-
	manager wrapper so the failure is still visible to log clearly, not
	silently swallowed.

	Deliberately broad (catching Exception, not just the one known
	UNIQUE-index case) - any target field this app doesn't control could
	turn out to have its own constraint or trigger tomorrow; the contract
	here is "never raise", not "never raise for the specific case already
	found once".

	Writes every field for one record in a single statement inside a
	single savepoint, rather than a savepoint plus an UPDATE per field:
	for a Contact that is the primary contact of a Customer, a Supplier,
	an Employee and a User with all three fields changed, that is 4
	savepoints and 4 UPDATEs instead of 11 and 11.

	On failure it retries the same record field by field, so the batching
	never costs granularity: the known production case (a Contact that is
	the primary contact for two Users, whose second mobile_no write hits
	tabUser.mobile_no's native UNIQUE index) must still apply that User's
	*other* fields and skip only the colliding one. Batching alone would
	have rolled back the whole record's update, silently syncing less than
	before - so the fast path is batched and the rare failure path keeps
	the original per-field isolation.

	Args:
		doctype: the target doctype.
		record_name: the target record's name.
		updates: {fieldname: value} to apply to that one record.
	"""
	if not updates:
		return

	if _try_set_values(doctype, record_name, updates):
		return

	if len(updates) == 1:
		# Already as granular as it gets - a retry would just fail again.
		_log_failed_propagation(doctype, record_name, updates)
		return

	for fieldname, value in updates.items():
		if not _try_set_values(doctype, record_name, {fieldname: value}):
			_log_failed_propagation(doctype, record_name, {fieldname: value})


def _try_set_values(doctype, record_name, updates):
	"""One savepoint-guarded frappe.db.set_value. Returns True if it
	applied, False if it failed and was rolled back to the savepoint
	(leaving the surrounding transaction, including the Contact's own
	save, untouched).

	Args:
		doctype, record_name, updates: see
			_set_values_without_crashing_the_save.

	Returns:
		True on success, False if the write failed and was rolled back.
	"""
	savepoint_name = "contact_enhancements_propagate_" + frappe.generate_hash(length=8)
	frappe.db.savepoint(savepoint_name)
	try:
		frappe.db.set_value(doctype, record_name, updates, update_modified=False)
	except Exception:
		frappe.db.rollback(save_point=savepoint_name)
		return False
	return True


def _log_failed_propagation(doctype, record_name, updates):
	"""Record a propagation write that could not be applied, without
	raising - see _set_values_without_crashing_the_save for why this can
	never be allowed to interrupt the Contact save that triggered it.

	Args:
		doctype, record_name, updates: the write that failed.
	"""
	frappe.log_error(
		title="contact_enhancements: could not propagate Contact change",
		message=(
			f"Tried to set {doctype} {record_name} {updates!r} while "
			"propagating a Contact's own name/email/phone change - skipped, likely "
			"a conflicting unique value on the target field."
		),
	)
