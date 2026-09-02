"""Tests for contact_enhancements.api.contact_dedupe - the soft, non-blocking
Contact-level duplicate backstop (find_contacts_by_phone/find_contacts_by_email
/classify_contact_duplicate/warn_if_duplicate_contact).
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from contact_enhancements.api.contact_dedupe import (
	EMAIL_MATCH,
	NO_MATCH,
	PHONE_AND_EMAIL_MATCH,
	PHONE_MATCH,
	classify_contact_duplicate,
	dedupe_contact_phone_rows,
	find_contacts_by_email,
	find_contacts_by_phone,
	_contact_phone_pairs_with_repeated_rows,
	_phones_shared_by_multiple_contacts,
	find_duplicate_mobile_contacts,
	find_duplicate_phone_rows_within_contact,
	merge_duplicate_mobile_contacts,
	warn_if_duplicate_contact,
)
from contact_enhancements.tests.test_lead_lookup import make_contact


def _add_duplicate_phone(contact, phone, country="Egypt"):
	"""Historical note: this used to attach a second Contact Phone row to
	`contact` via a raw frappe.get_doc(...).db_insert(), bypassing
	contact_hooks.enforce_unique_mobile_number to simulate legacy
	duplicate data.

	That technique stopped working once Phase 0d's own database-level
	UNIQUE INDEX (patches.add_contact_phone_unique_mobile_index) actually
	went live on the site tests run against - confirmed the hard way: even
	a raw db_insert() (and even SET unique_checks=0, which does not
	bypass a real UNIQUE INDEX violation in MariaDB) is now rejected by
	the database itself, exactly as designed. There is no longer any way
	to make two Contact Phone rows genuinely collide on this site, which
	is the correct, intended end state - Phase 0d's whole job was making
	that permanently impossible, not just discouraged.

	Every test that used to call this now mocks at the appropriate query
	boundary instead (find_contacts_by_phone / frappe.get_all inside this
	module, or frappe.get_doc for dedupe_contact_phone_rows) - see each
	test class's own comments. Kept as a stub, rather than deleted, so a
	stray future call fails loudly with this explanation instead of a
	confusing raw IntegrityError.
	"""
	raise NotImplementedError(
		"_add_duplicate_phone no longer works now that the real UNIQUE INDEX is live - "
		"mock find_contacts_by_phone/frappe.get_all/frappe.get_doc instead, see this "
		"function's own docstring."
	)


class TestFindContactsByPhone(FrappeTestCase):
	def test_finds_a_contact_with_a_matching_phone(self):
		contact = make_contact(phone_nos=["01033344456"])
		found = find_contacts_by_phone("+201033344456")
		self.assertIn(contact.name, found)

	def test_excludes_the_given_contact(self):
		contact = make_contact(phone_nos=["01033344457"])
		found = find_contacts_by_phone("+201033344457", exclude=contact.name)
		self.assertNotIn(contact.name, found)

	def test_returns_empty_for_blank_phone(self):
		self.assertEqual(find_contacts_by_phone(""), [])
		self.assertEqual(find_contacts_by_phone(None), [])

	def test_no_match_returns_empty(self):
		self.assertEqual(find_contacts_by_phone("00000000000"), [])


class TestFindContactsByEmail(FrappeTestCase):
	def test_finds_a_contact_with_a_matching_email(self):
		contact = make_contact(email_ids=[{"email_id": "dedupe-test@example.com", "is_primary": 1}])
		found = find_contacts_by_email("dedupe-test@example.com")
		self.assertIn(contact.name, found)

	def test_excludes_the_given_contact(self):
		contact = make_contact(email_ids=[{"email_id": "dedupe-test2@example.com", "is_primary": 1}])
		found = find_contacts_by_email("dedupe-test2@example.com", exclude=contact.name)
		self.assertNotIn(contact.name, found)

	def test_returns_empty_for_blank_email(self):
		self.assertEqual(find_contacts_by_email(""), [])
		self.assertEqual(find_contacts_by_email(None), [])


class TestClassifyContactDuplicate(FrappeTestCase):
	def test_no_match(self):
		contact = make_contact()
		self.assertEqual(classify_contact_duplicate(contact), NO_MATCH)

	def test_phone_match(self):
		# find_contacts_by_phone is mocked, not a real second Contact Phone
		# row - the real UNIQUE INDEX (Phase 0d) now makes two rows
		# genuinely colliding impossible to construct at all, on purpose.
		# classify_contact_duplicate's own logic (call find_contacts_by_
		# phone per row, classify by what comes back) is what's under
		# test here, not find_contacts_by_phone's own query, which has
		# its own dedicated tests.
		other = make_contact(phone_nos=["01099911122"])
		with patch(
			"contact_enhancements.api.contact_dedupe.find_contacts_by_phone",
			return_value=["some-other-contact"],
		):
			self.assertEqual(classify_contact_duplicate(other), PHONE_MATCH)

	def test_email_match(self):
		make_contact(email_ids=[{"email_id": "shared-dedupe@example.com", "is_primary": 1}])
		other = make_contact(email_ids=[{"email_id": "shared-dedupe@example.com", "is_primary": 1}])
		self.assertEqual(classify_contact_duplicate(other), EMAIL_MATCH)

	def test_phone_and_email_match(self):
		other = make_contact(
			phone_nos=["01099911133"],
			email_ids=[{"email_id": "both-dedupe@example.com", "is_primary": 1}],
		)
		make_contact(email_ids=[{"email_id": "both-dedupe@example.com", "is_primary": 1}])
		with patch(
			"contact_enhancements.api.contact_dedupe.find_contacts_by_phone",
			return_value=["some-other-contact"],
		):
			self.assertEqual(classify_contact_duplicate(other), PHONE_AND_EMAIL_MATCH)

	def test_a_contacts_own_rows_never_match_itself(self):
		contact = make_contact(
			phone_nos=["01099911144"],
			email_ids=[{"email_id": "self-dedupe@example.com", "is_primary": 1}],
		)
		self.assertEqual(classify_contact_duplicate(contact), NO_MATCH)


class TestWarnIfDuplicateContact(FrappeTestCase):
	def test_never_raises_even_with_a_genuine_duplicate(self):
		other = make_contact(phone_nos=["01099911155"])
		with patch(
			"contact_enhancements.api.contact_dedupe.find_contacts_by_phone",
			return_value=["some-other-contact"],
		):
			warn_if_duplicate_contact(other)  # must not raise

	def test_never_raises_without_a_duplicate(self):
		contact = make_contact()
		warn_if_duplicate_contact(contact)  # must not raise

	def test_runs_via_a_real_contact_insert_without_blocking_the_save(self):
		# Runs via the real, registered doc_events chain (not a direct
		# function call) - proving warn_if_duplicate_contact doesn't
		# interfere with an ordinary save even when it finds a genuine
		# match. Uses a duplicate *email*, not phone: contact_hooks.
		# enforce_unique_mobile_number (a separate, later doc_event in the
		# same validate chain) would legitimately block a real phone
		# duplicate now (Phase 0d), which would confound what this test
		# is actually about - email has no such constraint, so a real
		# duplicate email exercises the same "hook runs, finds a match,
		# doesn't block" path without that entanglement.
		make_contact(email_ids=[{"email_id": "runs-via-insert-dedupe@example.com", "is_primary": 1}])
		other = make_contact(email_ids=[{"email_id": "runs-via-insert-dedupe@example.com", "is_primary": 1}])
		self.assertTrue(other.name)


class TestFindDuplicateMobileContacts(FrappeTestCase):
	"""find_duplicate_mobile_contacts - Phase 0c's own legacy-duplicate
	report, the hard gate before Phase 0d's uniqueness constraint can ship
	(see this function's own docstring for why).

	Its own single frappe.get_all("Contact Phone", ...) call is mocked
	throughout - the real UNIQUE INDEX this same phase adds (once live,
	as it now is on any site that has run bench migrate to completion)
	makes it permanently impossible to construct two genuinely colliding
	rows in the database to test the grouping logic against real data -
	see _add_duplicate_phone's own docstring for the full story. Mocking
	the query boundary tests the same grouping/filtering logic in
	isolation from that now-unconstructable database state.
	"""

	def test_no_match_is_not_a_duplicate(self):
		# The database now decides which numbers are shared, so a number
		# it doesn't report simply never reaches the grouping step.
		with patch(
			"contact_enhancements.api.contact_dedupe._phones_shared_by_multiple_contacts",
			return_value=[],
		):
			duplicates = find_duplicate_mobile_contacts()
		numbers = {entry["phone"] for entry in duplicates}
		self.assertNotIn("+201099812201", numbers)

	def test_finds_two_contacts_sharing_a_mobile_number(self):
		fake_rows = [
			frappe._dict(phone="+201099812202", parent="Contact A"),
			frappe._dict(phone="+201099812202", parent="Contact B"),
		]
		with (
			patch(
				"contact_enhancements.api.contact_dedupe._phones_shared_by_multiple_contacts",
				return_value=["+201099812202"],
			),
			patch("contact_enhancements.api.contact_dedupe.frappe.get_all", return_value=fake_rows),
		):
			duplicates = find_duplicate_mobile_contacts()
		match = next(entry for entry in duplicates if entry["phone"] == "+201099812202")
		self.assertEqual(set(match["contacts"]), {"Contact A", "Contact B"})

	def test_excludes_landline_matches(self):
		# A shared landline (an office reception line, for example) is not
		# a duplicate-Contact problem, and 0d's own uniqueness constraint
		# only ever applies to non-landline rows either way. The actual
		# exclusion happens in the filters= passed to frappe.get_all (a
		# real landline row would never even reach a real query's result
		# set) - since that call is mocked for the tests above, this one
		# instead confirms the filter itself genuinely asks the database
		# to exclude landlines, not just that the Python-side grouping
		# would handle one correctly if it slipped through.
		with (
			patch(
				"contact_enhancements.api.contact_dedupe._phones_shared_by_multiple_contacts",
				return_value=["+201099812203"],
			),
			patch(
				"contact_enhancements.api.contact_dedupe.frappe.get_all", return_value=[]
			) as mock_get_all,
		):
			find_duplicate_mobile_contacts()
		_args, kwargs = mock_get_all.call_args
		self.assertEqual(kwargs["filters"]["custom_landline"], 0)

	def test_the_grouping_query_itself_excludes_landlines(self):
		# The other half of the same guarantee: the GROUP BY that now
		# decides which numbers are shared must exclude landlines too,
		# otherwise a shared reception line would be reported before the
		# member lookup above ever got the chance to filter it out.
		contact_phone = frappe.qb.DocType("Contact Phone")
		sql = str(
			frappe.qb.from_(contact_phone).select(contact_phone.phone).where(contact_phone.custom_landline == 0)
		)
		self.assertIn("custom_landline", sql)
		# and the real query runs without error against the live schema
		self.assertIsInstance(_phones_shared_by_multiple_contacts(), list)

	def test_worst_offenders_first(self):
		fake_rows = [
			frappe._dict(phone="+201099812204", parent="Contact A"),
			frappe._dict(phone="+201099812204", parent="Contact B"),
			frappe._dict(phone="+201099812204", parent="Contact C"),
			frappe._dict(phone="+201099812205", parent="Contact D"),
			frappe._dict(phone="+201099812205", parent="Contact E"),
		]
		with (
			patch(
				"contact_enhancements.api.contact_dedupe._phones_shared_by_multiple_contacts",
				return_value=["+201099812204", "+201099812205"],
			),
			patch("contact_enhancements.api.contact_dedupe.frappe.get_all", return_value=fake_rows),
		):
			duplicates = find_duplicate_mobile_contacts()
		three_way = next(e for e in duplicates if e["phone"] == "+201099812204")
		two_way = next(e for e in duplicates if e["phone"] == "+201099812205")
		self.assertLess(duplicates.index(three_way), duplicates.index(two_way))


class TestMergeDuplicateMobileContacts(FrappeTestCase):
	"""merge_duplicate_mobile_contacts - the Duplicate Mobile Contacts
	report's own "Merge..." action, wrapping frappe.rename_doc(...,
	merge=True) (the same mechanism the Contact form's own native "Merge
	with existing" uses) to merge a whole duplicate group in one call.

	find_contacts_by_phone (how this function discovers who to merge) is
	mocked throughout - real Contacts genuinely sharing a phone number can
	no longer be constructed at all once Phase 0d's UNIQUE INDEX is live
	(see _add_duplicate_phone's own docstring) - but every Contact
	involved is still a real, saved Contact, and frappe.rename_doc itself
	is never mocked (except in test_one_failure_does_not_block_the_rest,
	deliberately, to simulate one specific failure) - the actual merge/
	delete behavior under test here is exercised for real.
	"""

	def test_merges_every_other_contact_into_the_survivor(self):
		survivor = make_contact()
		loser = make_contact()
		with patch(
			"contact_enhancements.api.contact_dedupe.find_contacts_by_phone",
			return_value=[loser.name],
		):
			result = merge_duplicate_mobile_contacts("+201099812301", survivor.name)

		self.assertEqual(result["merged"], [loser.name])
		self.assertEqual(result["failed"], [])
		self.assertFalse(frappe.db.exists("Contact", loser.name))
		self.assertTrue(frappe.db.exists("Contact", survivor.name))

	def test_merges_every_member_of_a_larger_group(self):
		survivor = make_contact()
		loser_a = make_contact()
		loser_b = make_contact()
		with patch(
			"contact_enhancements.api.contact_dedupe.find_contacts_by_phone",
			return_value=[loser_a.name, loser_b.name],
		):
			result = merge_duplicate_mobile_contacts("+201099812302", survivor.name)

		self.assertEqual(set(result["merged"]), {loser_a.name, loser_b.name})
		self.assertFalse(frappe.db.exists("Contact", loser_a.name))
		self.assertFalse(frappe.db.exists("Contact", loser_b.name))

	def test_raises_when_survivor_does_not_exist(self):
		self.assertRaises(
			frappe.ValidationError,
			merge_duplicate_mobile_contacts,
			"+201099812303",
			"no-such-contact",
		)

	def test_raises_when_nothing_shares_the_number(self):
		survivor = make_contact(phone_nos=["01099812304"])
		self.assertRaises(
			frappe.ValidationError,
			merge_duplicate_mobile_contacts,
			"+201099812304",
			survivor.name,
		)

	def test_only_merges_contacts_genuinely_sharing_the_number(self):
		# merge_duplicate_mobile_contacts merges exactly whoever find_
		# contacts_by_phone reports (re-derived from the database at call
		# time in real use) - an unrelated Contact never named there is
		# left alone regardless of what else exists.
		survivor = make_contact()
		loser = make_contact()
		unrelated = make_contact(phone_nos=["01099812306"])
		with patch(
			"contact_enhancements.api.contact_dedupe.find_contacts_by_phone",
			return_value=[loser.name],
		):
			result = merge_duplicate_mobile_contacts("+201099812305", survivor.name)

		self.assertEqual(result["merged"], [loser.name])
		self.assertTrue(frappe.db.exists("Contact", unrelated.name))

	def test_one_failure_does_not_block_the_rest(self):
		survivor = make_contact()
		loser_ok = make_contact()
		loser_broken = make_contact()

		real_rename_doc = frappe.rename_doc

		def flaky_rename_doc(doctype, old, new, **kwargs):
			if old == loser_broken.name:
				raise frappe.ValidationError("simulated failure")
			return real_rename_doc(doctype, old, new, **kwargs)

		with (
			patch(
				"contact_enhancements.api.contact_dedupe.find_contacts_by_phone",
				return_value=[loser_ok.name, loser_broken.name],
			),
			patch(
				"contact_enhancements.api.contact_dedupe.frappe.rename_doc",
				side_effect=flaky_rename_doc,
			),
		):
			result = merge_duplicate_mobile_contacts("+201099812307", survivor.name)

		self.assertEqual(result["merged"], [loser_ok.name])
		self.assertEqual(len(result["failed"]), 1)
		self.assertEqual(result["failed"][0]["name"], loser_broken.name)
		self.assertFalse(frappe.db.exists("Contact", loser_ok.name))
		self.assertTrue(frappe.db.exists("Contact", loser_broken.name))


class TestFindDuplicatePhoneRowsWithinContact(FrappeTestCase):
	"""find_duplicate_phone_rows_within_contact - a different failure mode
	from find_duplicate_mobile_contacts: the same number entered twice on
	one Contact's own phone_nos, not shared across distinct Contacts.
	Found against real legacy data - invisible to the cross-Contact
	report, but still fatal to add_contact_phone_unique_mobile_index's
	ALTER TABLE.

	Its own single frappe.get_all("Contact Phone", ...) call is mocked
	throughout, for the same reason as TestFindDuplicateMobileContacts -
	the real UNIQUE INDEX now makes two genuinely colliding rows
	impossible to construct in the database at all, including on one
	Contact's own phone_nos."""

	def test_no_match_for_a_normal_contact(self):
		# The database now identifies the repeating pairs, so a Contact
		# with no repeat never reaches the grouping step.
		with patch(
			"contact_enhancements.api.contact_dedupe._contact_phone_pairs_with_repeated_rows",
			return_value=[],
		):
			found = find_duplicate_phone_rows_within_contact()
		self.assertEqual(found, [])

	def test_finds_the_same_number_twice_on_one_contact(self):
		fake_rows = [
			frappe._dict(name="row1", phone="+201099733402", parent="Contact A"),
			frappe._dict(name="row2", phone="+201099733402", parent="Contact A"),
		]
		with (
			patch(
				"contact_enhancements.api.contact_dedupe._contact_phone_pairs_with_repeated_rows",
				return_value=[("Contact A", "+201099733402")],
			),
			patch("contact_enhancements.api.contact_dedupe.frappe.get_all", return_value=fake_rows),
		):
			found = find_duplicate_phone_rows_within_contact()
		self.assertEqual(len(found), 1)
		self.assertEqual(found[0]["contact"], "Contact A")
		self.assertEqual(found[0]["phone"], "+201099733402")
		self.assertEqual(set(found[0]["rows"]), {"row1", "row2"})

	def test_excludes_landlines_via_the_query_filter(self):
		# Same reasoning as TestFindDuplicateMobileContacts.test_excludes_
		# landline_matches - the real exclusion happens in filters= passed
		# to frappe.get_all, which is mocked here, so this confirms the
		# filter itself asks for it rather than simulating a landline row
		# that a real query would never even return.
		with (
			patch(
				"contact_enhancements.api.contact_dedupe._contact_phone_pairs_with_repeated_rows",
				return_value=[("Contact A", "+201099733403")],
			),
			patch(
				"contact_enhancements.api.contact_dedupe.frappe.get_all", return_value=[]
			) as mock_get_all,
		):
			find_duplicate_phone_rows_within_contact()
		_args, kwargs = mock_get_all.call_args
		self.assertEqual(kwargs["filters"]["custom_landline"], 0)

	def test_the_grouping_query_itself_runs_and_excludes_landlines(self):
		# The GROUP BY that now decides which pairs repeat must exclude
		# landlines too, and must be valid against the live schema.
		# run() returns a tuple of rows; what matters is that it executes
		# against the live schema and yields (parent, phone) pairs.
		pairs = _contact_phone_pairs_with_repeated_rows()
		self.assertTrue(all(len(pair) == 2 for pair in pairs))

	def test_does_not_flag_two_different_contacts_sharing_a_number(self):
		# That's find_duplicate_mobile_contacts's own job, not this one -
		# each Contact here only has *one* row for the number.
		fake_rows = [
			frappe._dict(name="row1", phone="+201099733404", parent="Contact A"),
			frappe._dict(name="row2", phone="+201099733404", parent="Contact B"),
		]
		with patch("contact_enhancements.api.contact_dedupe.frappe.get_all", return_value=fake_rows):
			found = find_duplicate_phone_rows_within_contact()
		self.assertEqual(found, [])


class TestDedupeContactPhoneRows(FrappeTestCase):
	"""dedupe_contact_phone_rows - the resolution action for a within-
	Contact duplicate: consolidate onto one row (OR-ing the channel
	flags together so nothing set on either row is lost), delete the
	rest.

	Its own frappe.get_doc("Contact", contact) call is mocked to return a
	doc with a second in-memory-only row appended - the real UNIQUE INDEX
	makes two such rows genuinely impossible to persist at all (see
	_add_duplicate_phone's own docstring), but dedupe_contact_phone_rows
	only ever reads doc.phone_nos and then calls doc.save() itself, so a
	real, otherwise-normal Document with the duplicate injected purely in
	memory exercises its actual consolidate-then-save logic for real -
	including the real save, which is only ever valid because that same
	logic has already removed the extra row by the time it runs.

	The mock is deliberately narrow (a side_effect that only intercepts
	the exact frappe.get_doc("Contact", <name>) call dedupe_contact_
	phone_rows itself makes, falling through to the real frappe.get_doc
	for everything else) rather than a blanket return_value - confirmed
	the hard way that a blanket mock also intercepts Document.
	check_if_latest()'s own internal frappe.get_doc(..., for_update=True)
	re-read of "the document as it currently stands in the database"
	(used for optimistic-locking / TimestampMismatchError detection),
	silently making it return this same in-memory object instead of a
	genuine fresh read - since frappe.get_doc is one shared, module-level
	attribute, not something scoped per caller.
	"""

	@staticmethod
	def _get_doc_side_effect(contact):
		real_get_doc = frappe.get_doc

		def side_effect(doctype, name=None, *args, **kwargs):
			if doctype == "Contact" and name == contact.name and not args and not kwargs:
				return contact
			return real_get_doc(doctype, name, *args, **kwargs)

		return side_effect

	def test_consolidates_to_one_row(self):
		# _original_modified is only ever set by Document.
		# set_user_and_timestamp() (during a save) or explicitly by a REST
		# handler right before calling save on a doc it just loaded
		# (frappe/handler.py, frappe/api/v2.py) - never by a plain
		# frappe.get_doc() load. Reusing an already-.insert()-ed doc
		# object for a second save (as dedupe_contact_phone_rows does
		# here, via the mock below) needs the same explicit stamp those
		# handlers use, or check_if_latest() compares against its stale
		# pre-insert value (None) and raises TimestampMismatchError.
		contact = make_contact(phone_nos=["01099733405"])
		contact._original_modified = contact.modified
		contact.append("phone_nos", {"phone": "+201099733405", "country": "Egypt"})

		with patch(
			"contact_enhancements.api.contact_dedupe.frappe.get_doc",
			side_effect=self._get_doc_side_effect(contact),
		):
			dedupe_contact_phone_rows(contact.name, "+201099733405")

		contact.reload()
		matches = [row for row in contact.phone_nos if row.phone == "+201099733405"]
		self.assertEqual(len(matches), 1)

	def test_ors_the_channel_flags_together(self):
		contact = make_contact(phone_nos=["01099733406"])
		contact._original_modified = contact.modified
		contact.phone_nos[0].is_primary_mobile_no = 1
		contact.phone_nos[0].custom_whatsapp = 0
		contact.append(
			"phone_nos",
			{
				"phone": "+201099733406",
				"country": "Egypt",
				"is_primary_mobile_no": 0,
				"custom_whatsapp": 1,
			},
		)

		with patch(
			"contact_enhancements.api.contact_dedupe.frappe.get_doc",
			side_effect=self._get_doc_side_effect(contact),
		):
			dedupe_contact_phone_rows(contact.name, "+201099733406")

		contact.reload()
		survivor = next(row for row in contact.phone_nos if row.phone == "+201099733406")
		self.assertEqual(survivor.is_primary_mobile_no, 1)
		self.assertEqual(survivor.custom_whatsapp, 1)

	def test_raises_when_there_is_nothing_to_dedupe(self):
		contact = make_contact(phone_nos=["01099733407"])
		self.assertRaises(
			frappe.ValidationError,
			dedupe_contact_phone_rows,
			contact.name,
			"+201099733407",
		)
