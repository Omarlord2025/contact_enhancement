"""Tests for contact_enhancements.user_hooks - the regression fix for
User.create_contact()'s native background Contact sync
(validate_user_phone_before_contact_sync) and contact_hooks.py's
background-job leniency it depends on (_running_in_background_job /
normalize_and_validate_contact_phones).
"""

import frappe
from frappe.core.doctype.user.user import create_contact
from frappe.tests.utils import FrappeTestCase

from contact_enhancements.tests.test_lead_lookup import make_contact
from contact_enhancements.user_hooks import link_user_contact, validate_user_phone_before_contact_sync


def make_user(**kwargs):
	email = kwargs.pop("email", None) or f"{frappe.generate_hash(length=10)}@example.com"
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": kwargs.pop("first_name", "Test"),
			"send_welcome_email": 0,
			**kwargs,
		}
	)
	user.insert(ignore_permissions=True)
	return user


class TestValidateUserPhoneBeforeContactSync(FrappeTestCase):
	def test_strips_formatting_noise_from_phone(self):
		doc = frappe.new_doc("User")
		doc.phone = "010 1234-5678"
		validate_user_phone_before_contact_sync(doc)
		self.assertEqual(doc.phone, "01012345678")

	def test_strips_formatting_noise_from_mobile_no(self):
		doc = frappe.new_doc("User")
		doc.mobile_no = "(010) 1234.5678"
		validate_user_phone_before_contact_sync(doc)
		self.assertEqual(doc.mobile_no, "01012345678")

	def test_blank_is_noop(self):
		doc = frappe.new_doc("User")
		validate_user_phone_before_contact_sync(doc)  # must not raise
		self.assertFalse(doc.phone)
		self.assertFalse(doc.mobile_no)

	def test_never_raises_on_a_plausible_but_invalid_number(self):
		# Formatting cleanup only - it can't and shouldn't try to validate,
		# since User has no country field to check against. 01312345678 is
		# an unissued Egyptian prefix (013) - genuinely invalid, but
		# nothing here is in a position to know that.
		doc = frappe.new_doc("User")
		doc.mobile_no = "013-1234-5678"
		validate_user_phone_before_contact_sync(doc)  # must not raise
		self.assertEqual(doc.mobile_no, "01312345678")


class TestCreateContactRegressionFix(FrappeTestCase):
	"""Reproduces the regression documented in user_hooks.py's own module
	docstring: User.on_update() enqueues create_contact(user,
	ignore_mandatory=True) as a background job, and ignore_mandatory never
	protected against contact_hooks.normalize_and_validate_contact_phones's
	own frappe.throw() on an unparseable phone number - confirmed by
	tracing frappe/model/document.py directly.

	Uses "01312345678" (an unissued Egyptian mobile prefix - all digits,
	plausible-shaped, but genuinely invalid) as the "malformed" example
	throughout, not literal garbage like "abc" - Frappe's own generic
	Document._validate_data_fields() applies its own, separate, far more
	permissive native check (frappe.utils.validate_phone_number, a bare
	regex allowing only digits/spaces/+-.,*#()) to every options="Phone"
	field, including each Contact Phone row's own phone value - confirmed
	by reproducing it directly. That check runs before this app's own
	hooks ever get a chance to, rejects genuine garbage (letters) on its
	own regardless of background-job context, and this app has no way to
	override core Frappe's own validation there. This fix is specifically
	about a number that's plausible-shaped but not genuinely valid for its
	claimed country - the case this app's own phonenumbers-based
	validation exists to catch in the first place.

	User.insert() inside a test runs create_contact via frappe.enqueue's
	own now=True/in_test path (call_directly = True), which calls the
	function directly, bypassing execute_job() and never setting
	frappe.local.job - the exact same synchronous path an interactive save
	would take. So a malformed number set *before* insert reproduces the
	original bug immediately, during setup; testing the fix needs the
	malformed value injected *after* insert (bypassing doc_events, via a
	direct frappe.db.set_value) and frappe.local.job set explicitly to
	simulate the real async worker context - see contact_hooks.
	_running_in_background_job's own docstring for why FrappeTestCase
	never sets that implicitly.
	"""

	def test_invalid_mobile_still_raises_outside_a_background_job(self):
		# The synchronous in-test path is not a background job - this is
		# the original bug, still present when there's genuinely someone
		# interactive who could see the error (matches an interactive Desk
		# save, not the real production background-worker path).
		self.assertRaises(frappe.ValidationError, make_user, mobile_no="01312345678")

	def test_invalid_mobile_no_longer_blocks_a_real_background_sync(self):
		user = make_user()
		frappe.db.set_value("User", user.name, "mobile_no", "01312345678", update_modified=False)
		user.reload()

		frappe.local.job = frappe._dict(site="erpnext")
		try:
			create_contact(user, ignore_mandatory=True)  # must not raise
		finally:
			frappe.local.job = None

		contact_name = frappe.db.get_value("Contact", {"user": user.name}, "name")
		self.assertTrue(contact_name)
		contact = frappe.get_doc("Contact", contact_name)
		matches = [row for row in contact.phone_nos if row.phone == "01312345678"]
		self.assertEqual(matches, [])  # dropped, not left in an invalid state

	def test_valid_mobile_still_syncs_normally_in_a_background_job(self):
		user = make_user()
		frappe.db.set_value("User", user.name, "mobile_no", "01033344455", update_modified=False)
		user.reload()

		frappe.local.job = frappe._dict(site="erpnext")
		try:
			create_contact(user, ignore_mandatory=True)
		finally:
			frappe.local.job = None

		contact_name = frappe.db.get_value("Contact", {"user": user.name}, "name")
		contact = frappe.get_doc("Contact", contact_name)
		matches = [row for row in contact.phone_nos if row.phone == "+201033344455"]
		self.assertEqual(len(matches), 1)


class TestLinkUserContact(FrappeTestCase):
	"""user_primary_contact - set by public/js/user.js's own onboarding
	dialog - Dynamic-Linked back to this User, the same fix every other
	doctype in this app applies for its own primary-contact field."""

	def test_links_the_contact_back_to_the_user(self):
		contact = make_contact()
		user = make_user(user_primary_contact=contact.name)

		link_user_contact(user)

		contact.reload()
		self.assertTrue(contact.has_link("User", user.name))

	def test_noop_without_a_primary_contact(self):
		user = make_user()
		link_user_contact(user)  # must not raise
