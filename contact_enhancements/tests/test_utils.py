"""Tests for contact_enhancements.utils - genuinely shared helpers reused
across every doctype this app links to Contact (Phase 1 extraction, once
Supplier needed the same Dynamic Link lookup and primary-contact-linking
logic Customer's own hooks already had)."""

import frappe
from frappe.tests.utils import FrappeTestCase

from contact_enhancements.tests.test_customer_hooks import make_customer
from contact_enhancements.tests.test_lead_lookup import make_contact, make_lead, make_lead_address
from contact_enhancements.utils import (
	address_source_label,
	dynamic_link_lookup,
	ensure_contact_linked_to_parent,
	ensure_doc_linked_to_parent,
	get_all_addresses_for_contact,
	resolve_address_from_contact_links,
)


def make_address(**kwargs):
	address = frappe.get_doc(
		{
			"doctype": "Address",
			"address_title": frappe.generate_hash(length=10),
			"address_type": "Office",
			"address_line1": "1 Utils Test Street",
			"city": "Cairo",
			"country": "Egypt",
			**kwargs,
		}
	)
	address.insert(ignore_permissions=True)
	return address


class TestDynamicLinkLookup(FrappeTestCase):
	def test_finds_parent_by_link_doctype_and_link_name(self):
		lead = make_lead(company_name="Utils Test Co")
		address = make_lead_address(lead.name)

		found = dynamic_link_lookup(
			{"parenttype": "Address", "link_doctype": "Lead", "link_name": lead.name},
			"parent",
		)

		self.assertEqual(found, address.name)

	def test_returns_none_when_no_match(self):
		found = dynamic_link_lookup(
			{"parenttype": "Address", "link_doctype": "Lead", "link_name": "no-such-lead"},
			"parent",
		)
		self.assertIsNone(found)


class TestEnsureContactLinkedToParent(FrappeTestCase):
	"""Generalized (Phase 1) from customer_hooks._ensure_contact_linked_to_
	customer - tested directly here against Customer (the field/doctype
	names it used to hardcode), since customer_hooks's own thin wrapper
	already has its own passing tests proving the wrapper itself still
	works unchanged."""

	def test_returns_none_without_a_primary_contact(self):
		customer = make_customer()
		self.assertIsNone(ensure_contact_linked_to_parent(customer, "customer_primary_contact"))

	def test_links_the_contact_back_to_the_parent(self):
		contact = make_contact()
		customer = make_customer(customer_primary_contact=contact.name)

		ensure_contact_linked_to_parent(customer, "customer_primary_contact")

		contact.reload()
		self.assertTrue(contact.has_link("Customer", customer.name))

	def test_idempotent_when_already_linked(self):
		contact = make_contact()
		customer = make_customer(customer_primary_contact=contact.name)

		ensure_contact_linked_to_parent(customer, "customer_primary_contact")
		contact.reload()
		link_count_before = len(contact.get("links", []))

		ensure_contact_linked_to_parent(customer, "customer_primary_contact")
		contact.reload()
		self.assertEqual(len(contact.get("links", [])), link_count_before)

	def test_does_not_load_the_contact_at_all_when_already_linked(self):
		# The steady state for this hook: it runs unconditionally on every
		# Customer/Supplier/Employee/User save, and the link is almost
		# always already there. Loading the Contact just to reach
		# has_link() costs 4 SELECTs (parent + phone_nos + email_ids +
		# links) to answer a question one indexed Dynamic Link read
		# answers - so in this path it must not load the document at all.
		contact = make_contact()
		customer = make_customer(customer_primary_contact=contact.name)
		ensure_contact_linked_to_parent(customer, "customer_primary_contact")

		# Same for_update exclusion as the test below - see its comment.
		load_calls = []
		original_get_doc = frappe.get_doc

		def counting_get_doc(*args, **kwargs):
			if args[:2] == ("Contact", contact.name) and not kwargs.get("for_update"):
				load_calls.append(args)
			return original_get_doc(*args, **kwargs)

		frappe.get_doc = counting_get_doc
		try:
			result = ensure_contact_linked_to_parent(customer, "customer_primary_contact")
		finally:
			frappe.get_doc = original_get_doc

		self.assertEqual(load_calls, [])
		self.assertIsNone(result)

	def test_still_links_when_the_probe_finds_nothing(self):
		# The other side of the probe: a genuinely unlinked Contact must
		# still be loaded and linked, exactly as before.
		contact = make_contact()
		customer = make_customer()
		frappe.db.set_value(
			"Customer", customer.name, "customer_primary_contact", contact.name, update_modified=False
		)
		customer.reload()

		ensure_contact_linked_to_parent(customer, "customer_primary_contact")

		contact.reload()
		self.assertTrue(contact.has_link("Customer", customer.name))

	def test_linking_does_not_touch_the_contacts_own_modified_timestamp(self):
		# The whole point of inserting the child row directly instead of
		# saving the parent: a parent save bumped Contact.modified, staling
		# every copy anyone else still held (the TimestampMismatchError
		# this app's own tests kept hitting, and the reason
		# _resync_modified_after_side_effect_saves exists).
		contact = make_contact()
		modified_before = frappe.db.get_value("Contact", contact.name, "modified")

		customer = make_customer()
		frappe.db.set_value(
			"Customer", customer.name, "customer_primary_contact", contact.name, update_modified=False
		)
		customer.reload()
		ensure_contact_linked_to_parent(customer, "customer_primary_contact")

		self.assertEqual(frappe.db.get_value("Contact", contact.name, "modified"), modified_before)
		# ...and the link is genuinely there
		self.assertTrue(frappe.get_doc("Contact", contact.name).has_link("Customer", customer.name))

	def test_the_new_link_is_visible_through_the_document_cache(self):
		# A direct child insert bypasses the ORM, so without an explicit
		# invalidation frappe.get_cached_doc keeps serving a Contact whose
		# links table is missing the row just added.
		contact = make_contact()
		customer = make_customer()
		# Warm the cache with the pre-link version.
		frappe.get_cached_doc("Contact", contact.name)

		frappe.db.set_value(
			"Customer", customer.name, "customer_primary_contact", contact.name, update_modified=False
		)
		customer.reload()
		ensure_contact_linked_to_parent(customer, "customer_primary_contact")

		cached = frappe.get_cached_doc("Contact", contact.name)
		self.assertTrue(cached.has_link("Customer", customer.name))

	def test_the_inserted_row_gets_a_sensible_idx(self):
		# Child rows are ordered; a parent save assigned idx for free.
		contact = make_contact()
		customer = make_customer(customer_primary_contact=contact.name)
		supplier_free_contact = frappe.get_doc("Contact", contact.name)
		idxs = [row.idx for row in supplier_free_contact.links]
		self.assertTrue(all(isinstance(i, int) and i > 0 for i in idxs), idxs)
		self.assertEqual(len(idxs), len(set(idxs)), f"duplicate idx values: {idxs}")

	def test_a_passed_in_document_is_refreshed_so_a_later_save_keeps_the_link(self):
		# Frappe's update_child_table deletes child rows absent from the
		# in-memory list, so a caller that saves the document it handed us
		# would wipe the row we inserted behind its back.
		contact = make_contact()
		customer = make_customer()
		frappe.db.set_value(
			"Customer", customer.name, "customer_primary_contact", contact.name, update_modified=False
		)
		customer.reload()

		ensure_contact_linked_to_parent(customer, "customer_primary_contact", contact=contact)
		contact.save(ignore_permissions=True)

		self.assertTrue(frappe.get_doc("Contact", contact.name).has_link("Customer", customer.name))

	def test_the_permission_check_costs_no_document_load_for_administrator(self):
		# frappe.has_permission returns early for Administrator without
		# touching the document, so restoring the check must not undo the
		# "don't load the Contact" win on the link path either.
		contact = make_contact()
		customer = make_customer()
		frappe.db.set_value(
			"Customer", customer.name, "customer_primary_contact", contact.name, update_modified=False
		)
		customer.reload()

		load_calls = []
		original_get_doc = frappe.get_doc

		def counting_get_doc(*args, **kwargs):
			if args[:2] == ("Contact", contact.name) and not kwargs.get("for_update"):
				load_calls.append(args)
			return original_get_doc(*args, **kwargs)

		frappe.get_doc = counting_get_doc
		try:
			customer.flags.ignore_permissions = False
			ensure_contact_linked_to_parent(customer, "customer_primary_contact")
		finally:
			frappe.get_doc = original_get_doc

		self.assertEqual(load_calls, [])
		contact.reload()
		self.assertTrue(contact.has_link("Customer", customer.name))

	def _link_as(self, user_email, customer, contact_name):
		"""Run the link as a specific user, with permissions genuinely
		enforced (the hooks that normally call this pass
		ignore_permissions through from the parent save)."""
		frappe.set_user(user_email)
		try:
			customer.flags.ignore_permissions = False
			return ensure_contact_linked_to_parent(customer, "customer_primary_contact")
		finally:
			frappe.set_user("Administrator")

	def test_refuses_to_link_a_contact_the_user_cannot_write(self):
		# Inserting the child row directly bypasses the whole document
		# lifecycle, so the permission check parent.save() used to apply
		# has to be made explicitly - otherwise anyone able to save a
		# Customer could write a row into any Contact.
		from contact_enhancements.tests.test_user_hooks import make_user

		contact = make_contact()
		customer = make_customer()
		frappe.db.set_value(
			"Customer", customer.name, "customer_primary_contact", contact.name, update_modified=False
		)
		customer.reload()

		# A brand-new User with no roles cannot write a Contact.
		powerless = make_user()

		with self.assertRaises(frappe.PermissionError):
			self._link_as(powerless.name, customer, contact.name)

		# ...and nothing was written behind the check.
		contact.reload()
		self.assertFalse(contact.has_link("Customer", customer.name))

	def test_ignore_permissions_still_bypasses_the_check(self):
		# The escape hatch parent.save(ignore_permissions=...) used to pass
		# through must keep working - every doc_event in this app relies on
		# it for background/system-driven saves.
		from contact_enhancements.tests.test_user_hooks import make_user

		contact = make_contact()
		customer = make_customer()
		frappe.db.set_value(
			"Customer", customer.name, "customer_primary_contact", contact.name, update_modified=False
		)
		customer.reload()
		powerless = make_user()

		frappe.set_user(powerless.name)
		try:
			customer.flags.ignore_permissions = True
			ensure_contact_linked_to_parent(customer, "customer_primary_contact")
		finally:
			frappe.set_user("Administrator")

		contact.reload()
		self.assertTrue(contact.has_link("Customer", customer.name))

	def test_a_permitted_user_can_still_link(self):
		# The check must not block someone who genuinely may write Contacts.
		from contact_enhancements.tests.test_user_hooks import make_user

		contact = make_contact()
		customer = make_customer()
		frappe.db.set_value(
			"Customer", customer.name, "customer_primary_contact", contact.name, update_modified=False
		)
		customer.reload()

		allowed = make_user(roles=[{"role": "System Manager"}])
		self._link_as(allowed.name, customer, contact.name)

		contact.reload()
		self.assertTrue(contact.has_link("Customer", customer.name))

	def test_reuses_a_passed_in_contact_without_reloading(self):
		# customer_primary_contact is set via a raw frappe.db.set_value,
		# bypassing Customer's own on_update hook (which would otherwise
		# call this exact function itself, via a freshly-loaded Contact,
		# before this test's own call ever runs - already linking it and
		# confusing what's actually being proven here).
		contact = make_contact()
		customer = make_customer()
		frappe.db.set_value(
			"Customer", customer.name, "customer_primary_contact", contact.name, update_modified=False
		)
		customer.reload()

		# Excludes calls with for_update=True: Document.save()'s own
		# check_if_latest() legitimately makes exactly one such internal
		# frappe.get_doc(..., for_update=True) call regardless of what
		# ensure_contact_linked_to_parent itself does - a blanket count
		# would also (incorrectly) flag that one.
		load_calls = []
		original_get_doc = frappe.get_doc

		def counting_get_doc(*args, **kwargs):
			if args[:2] == ("Contact", contact.name) and not kwargs.get("for_update"):
				load_calls.append(args)
			return original_get_doc(*args, **kwargs)

		frappe.get_doc = counting_get_doc
		try:
			ensure_contact_linked_to_parent(customer, "customer_primary_contact", contact=contact)
		finally:
			frappe.get_doc = original_get_doc

		self.assertEqual(load_calls, [])


class TestEnsureDocLinkedToParent(FrappeTestCase):
	"""ensure_contact_linked_to_parent's own generalization, exercised here
	against Address specifically (the Contact case is already covered
	above and by ensure_contact_linked_to_parent's own callers)."""

	def test_returns_none_without_a_value(self):
		customer = make_customer()
		self.assertIsNone(ensure_doc_linked_to_parent(customer, "customer_primary_address", "Address"))

	def test_links_the_address_back_to_the_parent(self):
		address = make_address()
		customer = make_customer(customer_primary_address=address.name)

		ensure_doc_linked_to_parent(customer, "customer_primary_address", "Address")

		address.reload()
		self.assertTrue(address.has_link("Customer", customer.name))

	def test_idempotent_when_already_linked(self):
		address = make_address()
		customer = make_customer(customer_primary_address=address.name)

		ensure_doc_linked_to_parent(customer, "customer_primary_address", "Address")
		address.reload()
		link_count_before = len(address.get("links", []))

		ensure_doc_linked_to_parent(customer, "customer_primary_address", "Address")
		address.reload()
		self.assertEqual(len(address.get("links", [])), link_count_before)


class TestResolveAddressFromContactLinks(FrappeTestCase):
	def test_returns_none_when_contact_has_no_links(self):
		contact = make_contact()
		self.assertIsNone(resolve_address_from_contact_links(contact.name))

	def test_resolves_from_a_linked_customer(self):
		contact = make_contact()
		address = make_address()
		make_customer(customer_primary_contact=contact.name, customer_primary_address=address.name)

		self.assertEqual(resolve_address_from_contact_links(contact.name), address.name)

	def test_customer_wins_over_a_linked_lead(self):
		contact = make_contact()
		lead = make_lead(company_name="Utils Priority Lead Co")
		lead_address = make_lead_address(lead.name)
		contact.append("links", {"link_doctype": "Lead", "link_name": lead.name})
		contact.save(ignore_permissions=True)

		customer_address = make_address()
		make_customer(customer_primary_contact=contact.name, customer_primary_address=customer_address.name)

		self.assertEqual(resolve_address_from_contact_links(contact.name), customer_address.name)

	def test_falls_through_to_lead_when_no_customer_or_supplier_matches(self):
		contact = make_contact()
		lead = make_lead(company_name="Utils Fallback Lead Co")
		lead_address = make_lead_address(lead.name)
		contact.append("links", {"link_doctype": "Lead", "link_name": lead.name})
		contact.save(ignore_permissions=True)

		self.assertEqual(resolve_address_from_contact_links(contact.name), lead_address.name)

	def test_excludes_the_callers_own_in_progress_record(self):
		# A doctype must never resolve its own address from itself
		# circularly - e.g. a Supplier already Dynamic-Linked to this same
		# Contact from an earlier save (supplier_hooks.link_primary_contact's
		# own on_update hook already creates that link - no need to add it
		# by hand here too).
		contact = make_contact()
		address = make_address()
		from contact_enhancements.tests.test_supplier_hooks import make_supplier

		supplier = make_supplier(supplier_primary_contact=contact.name, supplier_primary_address=address.name)

		result = resolve_address_from_contact_links(
			contact.name, exclude_doctype="Supplier", exclude_name=supplier.name
		)
		self.assertIsNone(result)

	def test_ignores_a_link_that_resolves_to_no_address(self):
		contact = make_contact()
		lead = make_lead(company_name="Utils No Address Lead Co")
		contact.append("links", {"link_doctype": "Lead", "link_name": lead.name})
		contact.save(ignore_permissions=True)

		self.assertIsNone(resolve_address_from_contact_links(contact.name))


class TestGetAllAddressesForContactQueryCount(FrappeTestCase):
	"""This runs on every Contact and every User form render, so its cost
	must not grow with how many records a Contact is linked to. It used to
	issue 2 queries per linked record (a primary-address read and a Dynamic
	Link read), plus one more per address to label its source."""

	def _count_reads(self, contact_name):
		reads = []
		original_get_all = frappe.get_all
		original_get_value = frappe.db.get_value

		def counting_get_all(*args, **kwargs):
			reads.append(("get_all",) + args[:1])
			return original_get_all(*args, **kwargs)

		def counting_get_value(*args, **kwargs):
			reads.append(("get_value",) + args[:1])
			return original_get_value(*args, **kwargs)

		frappe.get_all = counting_get_all
		frappe.db.get_value = counting_get_value
		try:
			get_all_addresses_for_contact(contact_name)
		finally:
			frappe.get_all = original_get_all
			frappe.db.get_value = original_get_value
		return reads

	def test_cost_does_not_grow_with_more_records_of_the_same_doctype(self):
		# Two Customers sharing one Contact must cost the same as one:
		# the per-doctype read is batched with a "name in (...)" filter.
		contact = make_contact()
		for _ in range(2):
			make_customer(
				customer_primary_contact=contact.name, customer_primary_address=make_address().name
			)
		two_customers = len(self._count_reads(contact.name))

		contact_one = make_contact()
		make_customer(
			customer_primary_contact=contact_one.name, customer_primary_address=make_address().name
		)
		one_customer = len(self._count_reads(contact_one.name))

		self.assertEqual(two_customers, one_customer)

	def test_stays_within_a_small_fixed_number_of_reads(self):
		# One read for the Contact's links, one per source doctype, and
		# one covering every Dynamic Link at once - no per-record or
		# per-address reads, including for the source labels.
		contact = make_contact()
		make_customer(customer_primary_contact=contact.name, customer_primary_address=make_address().name)

		from contact_enhancements.tests.test_supplier_hooks import make_supplier

		make_supplier(
			supplier_primary_contact=contact.name, supplier_primary_address=make_address().name
		)

		reads = self._count_reads(contact.name)
		self.assertLessEqual(len(reads), 4, f"expected <= 4 reads, got: {reads}")

	def test_returns_the_source_title_so_labelling_needs_no_extra_query(self):
		contact = make_contact()
		customer = make_customer(
			customer_name="Title Carrying Customer",
			customer_primary_contact=contact.name,
			customer_primary_address=make_address().name,
		)

		rows = get_all_addresses_for_contact(contact.name)
		self.assertEqual([row["source_title"] for row in rows], ["Title Carrying Customer"])
		# and the label uses it without going back to the database
		self.assertEqual(
			address_source_label("Customer", customer.name, title=rows[0]["source_title"]),
			"via Customer: Title Carrying Customer",
		)


class TestGetAllAddressesForContact(FrappeTestCase):
	def test_returns_empty_list_when_contact_has_no_links(self):
		contact = make_contact()
		self.assertEqual(get_all_addresses_for_contact(contact.name), [])

	def test_returns_every_distinct_address_across_every_source(self):
		contact = make_contact()
		customer_address = make_address()
		supplier_address = make_address()
		make_customer(customer_primary_contact=contact.name, customer_primary_address=customer_address.name)

		from contact_enhancements.tests.test_supplier_hooks import make_supplier

		make_supplier(supplier_primary_contact=contact.name, supplier_primary_address=supplier_address.name)

		results = get_all_addresses_for_contact(contact.name)
		addresses = {row["address"] for row in results}
		self.assertEqual(addresses, {customer_address.name, supplier_address.name})

	def test_deduplicates_the_same_address_shared_by_two_sources(self):
		contact = make_contact()
		address = make_address()
		make_customer(customer_primary_contact=contact.name, customer_primary_address=address.name)

		from contact_enhancements.tests.test_supplier_hooks import make_supplier

		make_supplier(supplier_primary_contact=contact.name, supplier_primary_address=address.name)

		results = get_all_addresses_for_contact(contact.name)
		self.assertEqual(len(results), 1)
		self.assertEqual(results[0]["address"], address.name)

	def test_includes_a_customer_address_dynamic_linked_but_not_primary(self):
		# Reported live: ERPNext's own native "Address & Contacts" widget
		# (and Customer.link_address_and_contact(), triggered by a Lead/
		# Opportunity/Prospect conversion) Dynamic-Links an Address to a
		# Customer without ever touching customer_primary_address itself -
		# an earlier version of this resolver, checking only the field,
		# silently missed it entirely.
		contact = make_contact()
		customer = make_customer(customer_primary_contact=contact.name)
		address = make_address(links=[{"link_doctype": "Customer", "link_name": customer.name}])

		results = get_all_addresses_for_contact(contact.name)

		self.assertEqual([row["address"] for row in results], [address.name])
		self.assertIsNone(customer.customer_primary_address)

	def test_includes_employee_as_a_source(self):
		# employee_hooks.link_employee_contact's own on_update hook already
		# Dynamic-Links the Contact -> Employee - no need to add it by
		# hand here too (and doing so via a stale in-memory contact object
		# would trip the same TimestampMismatchError documented in
		# contact_enhancements/CLAUDE.md).
		contact = make_contact()
		address = make_address()
		from contact_enhancements.tests.test_employee_hooks import make_employee

		employee = make_employee(employee_primary_contact=contact.name, employee_primary_address=address.name)

		results = get_all_addresses_for_contact(contact.name)
		self.assertEqual([row["address"] for row in results], [address.name])
		self.assertEqual(results[0]["source_doctype"], "Employee")

	def test_includes_every_dynamic_linked_address_for_lead_and_user(self):
		contact = make_contact()
		lead = make_lead(company_name="Utils All Addresses Lead Co")
		address1 = make_lead_address(lead.name)
		address2 = make_address(links=[{"link_doctype": "Lead", "link_name": lead.name}])
		contact.append("links", {"link_doctype": "Lead", "link_name": lead.name})
		contact.save(ignore_permissions=True)

		results = get_all_addresses_for_contact(contact.name)
		addresses = {row["address"] for row in results}
		self.assertEqual(addresses, {address1.name, address2.name})

	def test_respects_exclude_params(self):
		contact = make_contact()
		address = make_address()
		make_customer(customer_primary_contact=contact.name, customer_primary_address=address.name)

		customer_name = frappe.db.get_value(
			"Dynamic Link", {"parenttype": "Contact", "parent": contact.name, "link_doctype": "Customer"}, "link_name"
		)
		results = get_all_addresses_for_contact(contact.name, exclude_doctype="Customer", exclude_name=customer_name)
		self.assertEqual(results, [])


class TestAddressSourceLabel(FrappeTestCase):
	def test_direct_link_label(self):
		label = address_source_label("User", "someone@example.com", direct_doctype="User", direct_name="someone@example.com")
		self.assertEqual(label, "Linked directly")

	def test_indirect_link_label_uses_title_field(self):
		customer = make_customer()
		label = address_source_label("Customer", customer.name)
		self.assertEqual(label, f"via Customer: {customer.customer_name}")

	def test_falls_back_to_bare_name_when_no_title_field(self):
		# User isn't in the title-field map at all (its own "title" is
		# just its email, i.e. its own name) - falls back to the bare name.
		label = address_source_label("User", "someone-else@example.com")
		self.assertEqual(label, "via User: someone-else@example.com")


class TestBackfillNameFromPrimaryContact(FrappeTestCase):
	"""The server-side half of "picking a Contact fills in the name" -
	covers every path with no browser (REST API, Data Import, another app
	creating records), which the client-side handler can never reach."""

	def test_supplier_name_is_filled_from_the_contact(self):
		from contact_enhancements.tests.test_supplier_hooks import make_supplier

		contact = make_contact(first_name="Ahmed Mohamed Sabry")
		supplier = make_supplier(supplier_primary_contact=contact.name, supplier_name=None)
		self.assertEqual(supplier.supplier_name, "Ahmed Mohamed Sabry")

	def test_employee_name_is_filled_from_the_contact(self):
		from contact_enhancements.tests.test_employee_hooks import make_employee

		contact = make_contact(first_name="Ahmed Mohamed Sabry")
		employee = make_employee(employee_primary_contact=contact.name, first_name=None)
		self.assertEqual(employee.first_name, "Ahmed Mohamed Sabry")

	def test_an_existing_name_is_never_overwritten(self):
		from contact_enhancements.tests.test_supplier_hooks import make_supplier

		contact = make_contact(first_name="Ahmed Mohamed Sabry")
		supplier = make_supplier(
			supplier_primary_contact=contact.name, supplier_name="Nile Parts Company"
		)
		self.assertEqual(supplier.supplier_name, "Nile Parts Company")

	def test_is_a_noop_without_a_primary_contact(self):
		from contact_enhancements.utils import backfill_name_from_primary_contact

		doc = frappe.new_doc("Supplier")
		backfill_name_from_primary_contact(doc, "supplier_primary_contact", "supplier_name")
		self.assertFalse(doc.supplier_name)
