"""Tests for contact_enhancements.api.duplicate_mobile_contacts - the
admin-facing surface backing the "Duplicate Mobile Contacts" Page
(contact_enhancements/page/duplicate_mobile_contacts), one merged group
box per duplicate mobile number with each member Contact's own row
beneath it (Phase 0c)."""

from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from contact_enhancements.api.duplicate_mobile_contacts import (
	_business_records,
	get_duplicate_groups,
	get_report_data,
)
from contact_enhancements.tests.test_customer_hooks import make_customer
from contact_enhancements.tests.test_lead_lookup import make_contact, make_lead

_FIND_DUPLICATES_PATH = (
	"contact_enhancements.api.duplicate_mobile_contacts.find_duplicate_mobile_contacts"
)


class TestGetDuplicateGroups(FrappeTestCase):
	"""get_duplicate_groups's own find_duplicate_mobile_contacts() call is
	mocked throughout - the real UNIQUE INDEX (Phase 0d, live on any site
	that has fully run bench migrate) makes it permanently impossible to
	construct two Contacts genuinely sharing a mobile number in the
	database - see tests/test_contact_dedupe.py's own _add_duplicate_
	phone docstring for the full story. Every Contact referenced by a
	mocked duplicate group is still a real, saved Contact, so everything
	get_duplicate_groups does *with* that group (name/email lookup,
	business-records ranking) is exercised for real."""

	def test_empty_when_no_duplicates(self):
		with patch(_FIND_DUPLICATES_PATH, return_value=[]):
			groups = get_duplicate_groups()
		self.assertEqual(groups, [])

	def test_one_member_per_contact_in_a_group(self):
		first = make_contact()
		second = make_contact()
		fake_duplicates = [{"phone": "+201099733302", "contacts": [first.name, second.name]}]
		with patch(_FIND_DUPLICATES_PATH, return_value=fake_duplicates):
			groups = get_duplicate_groups()

		group = next(g for g in groups if g["phone"] == "+201099733302")
		self.assertEqual(len(group["members"]), 2)
		self.assertEqual({m["contact"] for m in group["members"]}, {first.name, second.name})

	def test_includes_full_name_and_email(self):
		first = make_contact(
			first_name="Zoltan",
			email_ids=[{"email_id": "zoltan-dup-test@example.com", "is_primary": 1}],
		)
		second = make_contact()
		fake_duplicates = [{"phone": "+201099733303", "contacts": [first.name, second.name]}]
		with patch(_FIND_DUPLICATES_PATH, return_value=fake_duplicates):
			groups = get_duplicate_groups()

		group = next(g for g in groups if g["phone"] == "+201099733303")
		member = next(m for m in group["members"] if m["contact"] == first.name)
		self.assertEqual(member["full_name"], "Zoltan")
		self.assertEqual(member["email_id"], "zoltan-dup-test@example.com")

	def test_suggests_the_contact_with_a_linked_party(self):
		# Answers "which of these has financial/business records" -
		# the Contact Dynamic-Linked to a real Customer (link_primary_
		# contact's own on_update hook creates the reverse link
		# automatically) sorts first and gets flagged suggested; the bare
		# duplicate with nothing linked does not.
		with_party = make_contact()
		make_customer(customer_primary_contact=with_party.name)
		bare = make_contact()

		fake_duplicates = [{"phone": "+201099733305", "contacts": [with_party.name, bare.name]}]
		with patch(_FIND_DUPLICATES_PATH, return_value=fake_duplicates):
			groups = get_duplicate_groups()

		group = next(g for g in groups if g["phone"] == "+201099733305")
		self.assertEqual(group["members"][0]["contact"], with_party.name)
		self.assertTrue(group["members"][0]["suggested"])
		self.assertFalse(group["members"][1]["suggested"])
		self.assertIn("Customer", group["members"][0]["business_records_html"])
		self.assertIn("No business records", group["members"][1]["business_records_html"])

	def test_prefers_a_linked_customer_over_a_merely_linked_lead(self):
		# Regression test for a real bug found via live testing: a Contact
		# only Dynamic-Linked to a Lead (pre-sales, no money moved) was
		# outranking one Dynamic-Linked to an actual Customer, because the
		# original ranking only checked "is this Contact linked to
		# *something*" rather than distinguishing which kind of party.
		# lead_linked sorts alphabetically before customer_linked, so it
		# would have won any tie-breaking-by-original-order bug too -
		# this only passes if has_strong_party is genuinely doing its job.
		customer_linked = make_contact(first_name="Zed")
		make_customer(customer_primary_contact=customer_linked.name)

		lead_linked = make_contact(first_name="Aaa")
		lead = make_lead(company_name="Weak Signal Co")
		lead_linked.append("links", {"link_doctype": "Lead", "link_name": lead.name})
		lead_linked.save(ignore_permissions=True)

		fake_duplicates = [
			{"phone": "+201099733307", "contacts": [customer_linked.name, lead_linked.name]}
		]
		with patch(_FIND_DUPLICATES_PATH, return_value=fake_duplicates):
			groups = get_duplicate_groups()

		group = next(g for g in groups if g["phone"] == "+201099733307")
		self.assertEqual(group["members"][0]["contact"], customer_linked.name)
		self.assertTrue(group["members"][0]["suggested"])
		self.assertEqual(group["members"][1]["contact"], lead_linked.name)
		self.assertFalse(group["members"][1]["suggested"])

	def test_no_one_is_suggested_when_nobody_has_any_records(self):
		first = make_contact()
		second = make_contact()
		fake_duplicates = [{"phone": "+201099733306", "contacts": [first.name, second.name]}]
		with patch(_FIND_DUPLICATES_PATH, return_value=fake_duplicates):
			groups = get_duplicate_groups()

		group = next(g for g in groups if g["phone"] == "+201099733306")
		self.assertTrue(all(not m["suggested"] for m in group["members"]))


class TestBusinessRecords(FrappeTestCase):
	"""_business_records - the "does this Contact carry real business
	weight" signal (linked Customer/Supplier/Lead, or a transaction
	naming it as contact_person) shown on the page."""

	def test_empty_for_no_names(self):
		self.assertEqual(_business_records([]), {})

	def test_no_records_by_default(self):
		contact = make_contact()
		result = _business_records([contact.name])
		self.assertEqual(
			result[contact.name], {"party": "", "has_strong_party": False, "transactions": 0}
		)

	def test_finds_a_dynamic_linked_customer_as_a_strong_party(self):
		contact = make_contact()
		customer = make_customer(customer_primary_contact=contact.name)

		result = _business_records([contact.name])
		self.assertIn(f"Customer: {customer.name}", result[contact.name]["party"])
		self.assertTrue(result[contact.name]["has_strong_party"])

	def test_a_linked_lead_is_not_a_strong_party(self):
		# A Lead is pre-sales - no money has moved yet - so it's shown
		# (party is non-empty) but doesn't count toward has_strong_party
		# the way a Customer/Supplier link does. Regression coverage for
		# the ranking bug this distinction exists to fix - see
		# TestGetDuplicateGroups.test_prefers_a_linked_customer_over_a_merely_linked_lead.
		contact = make_contact()
		lead = make_lead(company_name="Weak Signal Direct Test Co")
		contact.append("links", {"link_doctype": "Lead", "link_name": lead.name})
		contact.save(ignore_permissions=True)

		result = _business_records([contact.name])
		self.assertIn(f"Lead: {lead.name}", result[contact.name]["party"])
		self.assertFalse(result[contact.name]["has_strong_party"])


class TestGetReportData(FrappeTestCase):
	"""get_report_data - the whitelisted entry point the Page's own JS
	calls, wrapping get_duplicate_groups with a summary."""

	def test_summary_for_no_duplicates(self):
		with patch(_FIND_DUPLICATES_PATH, return_value=[]):
			result = get_report_data()
		self.assertEqual(result["groups"], [])
		self.assertEqual(result["summary"], {"group_count": 0, "contact_count": 0})

	def test_summary_counts_groups_and_contacts(self):
		a, b, c, d = make_contact(), make_contact(), make_contact(), make_contact()
		fake_duplicates = [
			{"phone": "+201099733308", "contacts": [a.name, b.name]},
			{"phone": "+201099733309", "contacts": [c.name, d.name]},
		]
		with patch(_FIND_DUPLICATES_PATH, return_value=fake_duplicates):
			result = get_report_data()

		self.assertEqual(result["summary"], {"group_count": 2, "contact_count": 4})
		self.assertEqual(len(result["groups"]), 2)
