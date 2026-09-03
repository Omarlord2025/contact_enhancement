"""Tests for contact_enhancements.customer_hooks - the Customer validate/
on_update doc_events that make Customer Primary Contact actually link back
to the Customer, and pull in a linked Lead's identity fields.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from contact_enhancements.customer_hooks import (
	_ensure_contact_linked_to_customer,
	_find_linked_docs_from_source,
	_resync_modified_after_side_effect_saves,
	link_primary_contact,
	sync_customer_from_primary_contact,
)
from contact_enhancements.tests.test_lead_lookup import make_contact, make_lead, make_lead_address


def _bypasses_mandatory_link_fields(kwargs):
	"""customer_primary_contact and customer_primary_address are both
	genuinely mandatory now (contact_enhancements/setup/property_setters.py)
	- satisfied in the real UI by the mandatory contact-picker dialog, which
	live-prefills the address from the picked Contact's linked Lead when
	one exists. ignore_mandatory is doctype-wide, not per-field, so unless a
	caller explicitly supplies *both*, mandatory checks have to be skipped
	entirely for these helpers to keep working for the many tests that are
	deliberately about a Customer that doesn't have one or the other yet
	(simulating data that predates this enforcement, or a caller that
	bypasses it).
	"""
	return not {"customer_primary_contact", "customer_primary_address"} <= kwargs.keys()


def make_customer(**kwargs):
	customer = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": frappe.generate_hash(length=10),
			"customer_type": "Individual",
			"customer_group": "Individual",
			"territory": "Rest Of The World",
			**kwargs,
		}
	)
	customer.flags.ignore_mandatory = _bypasses_mandatory_link_fields(kwargs)
	customer.insert(ignore_permissions=True)
	return customer


def make_quick_entry_style_customer(**kwargs):
	"""A Customer saved the way the real "+ Add Customer" Quick Entry
	dialog would leave it: only customer_name explicitly chosen,
	customer_type left exactly at Frappe's pristine "Company" DocField
	default - Quick Entry's own field list doesn't include
	customer_primary_contact (or customer_primary_address) at all,
	confirmed by driving it directly.

	customer_group is set explicitly to a real leaf group ("Individual"),
	not left at its own pristine default - on this site that default
	happens to resolve to a group-type node ("All Customer Groups"),
	which erpnext's own validate_customer_group() rejects outright,
	unrelated to anything this app does. customer_type is what these
	tests are actually about.
	"""
	customer = frappe.new_doc("Customer")
	customer.customer_name = kwargs.pop("customer_name", frappe.generate_hash(length=10))
	customer.customer_group = kwargs.pop("customer_group", "Individual")
	customer.flags.ignore_mandatory = _bypasses_mandatory_link_fields(kwargs)
	customer.update(kwargs)
	customer.insert(ignore_permissions=True)
	return customer


def lead_contact_name(lead_name):
	return frappe.db.get_value(
		"Dynamic Link",
		{"link_doctype": "Lead", "link_name": lead_name, "parenttype": "Contact"},
		"parent",
	)


class TestFindLinkedDocsFromSource(FrappeTestCase):
	"""_find_linked_docs_from_source resolves Contact and/or Address in one
	Dynamic Link query regardless of how many parenttypes are requested -
	see its own docstring for why this replaced two independent
	single-parenttype calls in sync_customer_from_primary_contact.
	"""

	def test_resolves_both_parenttypes_in_one_call(self):
		lead = make_lead(company_name="Acme Corp")
		contact_name = lead_contact_name(lead.name)
		address = make_lead_address(lead.name)

		customer = frappe.new_doc("Customer")
		customer.lead_name = lead.name

		found = _find_linked_docs_from_source(customer, ("Contact", "Address"))

		self.assertEqual(found, {"Contact": contact_name, "Address": address.name})

	def test_missing_parenttype_is_none_not_omitted(self):
		lead = make_lead(company_name="Acme Corp")  # no Address linked

		customer = frappe.new_doc("Customer")
		customer.lead_name = lead.name

		found = _find_linked_docs_from_source(customer, ("Contact", "Address"))

		self.assertIsNotNone(found["Contact"])
		self.assertIsNone(found["Address"])

	def test_no_source_field_set_returns_all_none(self):
		customer = frappe.new_doc("Customer")
		found = _find_linked_docs_from_source(customer, ("Contact", "Address"))
		self.assertEqual(found, {"Contact": None, "Address": None})


class TestSyncCustomerFromPrimaryContact(FrappeTestCase):
	def test_noop_without_primary_contact(self):
		customer = frappe.get_doc({"doctype": "Customer", "customer_name": frappe.generate_hash(10)})
		sync_customer_from_primary_contact(customer)
		self.assertIsNone(customer.lead_name)

	def test_noop_when_contact_has_no_lead(self):
		contact = make_contact()
		customer = frappe.new_doc("Customer")
		customer.customer_primary_contact = contact.name
		sync_customer_from_primary_contact(customer)
		self.assertIsNone(customer.lead_name)

	def test_prefills_blank_fields_from_lead(self):
		lead = make_lead(company_name="Acme Corp")
		contact_name = lead_contact_name(lead.name)

		customer = frappe.new_doc("Customer")
		customer.customer_primary_contact = contact_name
		sync_customer_from_primary_contact(customer)

		self.assertEqual(customer.lead_name, lead.name)
		self.assertEqual(customer.customer_type, "Company")
		self.assertEqual(customer.customer_name, "Acme Corp")

	def test_does_not_overwrite_non_blank_fields(self):
		lead = make_lead(company_name="Acme Corp")
		contact_name = lead_contact_name(lead.name)

		customer = frappe.new_doc("Customer")
		customer.customer_primary_contact = contact_name
		customer.customer_group = "Commercial"
		sync_customer_from_primary_contact(customer)

		self.assertEqual(customer.customer_group, "Commercial")

	def test_prefills_customer_primary_address_from_lead(self):
		lead = make_lead(company_name="Acme Corp")
		contact_name = lead_contact_name(lead.name)
		address = make_lead_address(lead.name)

		customer = frappe.new_doc("Customer")
		customer.customer_primary_contact = contact_name
		sync_customer_from_primary_contact(customer)

		self.assertEqual(customer.customer_primary_address, address.name)

	def test_customer_primary_address_blank_when_lead_has_none(self):
		# Documents the boundary: no placeholder Address is synthesized from
		# the Lead's flat city/state/country fields - there's no clean
		# mapping to Address's own mandatory address_line1, and the field
		# being genuinely mandatory means a user hits this as an ordinary
		# "please fill this in" block, not a silent gap.
		lead = make_lead(company_name="Acme Corp")
		contact_name = lead_contact_name(lead.name)

		customer = frappe.new_doc("Customer")
		customer.customer_primary_contact = contact_name
		sync_customer_from_primary_contact(customer)

		self.assertFalse(customer.customer_primary_address)

	def test_falls_back_to_the_general_contact_links_chain_when_no_lead(self):
		# A Contact with no Lead at all, but already linked to a Supplier
		# with an address - resolve_address_from_contact_links's own
		# broader cross-doctype fallback, tried once the Lead-based path
		# above finds nothing.
		from contact_enhancements.tests.test_supplier_hooks import make_address, make_supplier

		contact = make_contact()
		address = make_address()
		make_supplier(supplier_primary_contact=contact.name, supplier_primary_address=address.name)

		customer = frappe.new_doc("Customer")
		customer.customer_primary_contact = contact.name
		sync_customer_from_primary_contact(customer)

		self.assertEqual(customer.customer_primary_address, address.name)

	def test_does_not_overwrite_existing_customer_primary_address(self):
		lead = make_lead(company_name="Acme Corp")
		contact_name = lead_contact_name(lead.name)
		make_lead_address(lead.name)
		other_address = make_lead_address(make_lead(company_name="Other Co").name)

		customer = frappe.new_doc("Customer")
		customer.customer_primary_contact = contact_name
		customer.customer_primary_address = other_address.name
		sync_customer_from_primary_contact(customer)

		self.assertEqual(customer.customer_primary_address, other_address.name)

	def test_corrects_environment_default_for_individual_lead(self):
		# Regression: frappe.new_doc("Customer") pre-fills customer_type
		# "Company" (its DocField default) before this hook ever runs, so a
		# plain "only set if blank" guard silently never corrects it - a
		# Lead with no company_name (Individual) was staying "Company".
		lead = make_lead(first_name="Ahmed", last_name="Amin", company_name=None)
		contact_name = lead_contact_name(lead.name)

		customer = frappe.new_doc("Customer")
		self.assertEqual(customer.customer_type, "Company")  # sanity: the pristine default
		customer.customer_primary_contact = contact_name
		sync_customer_from_primary_contact(customer)

		self.assertEqual(customer.customer_type, "Individual")
		self.assertEqual(customer.customer_name, lead.lead_name)

	def test_does_not_overwrite_environment_default_field_already_changed(self):
		# The other half of the same fix: a value the user deliberately
		# picked away from the pristine default (even pre-save) must still
		# never be clobbered, same guarantee as any other field.
		lead = make_lead(company_name="Acme Corp")
		contact_name = lead_contact_name(lead.name)

		customer = frappe.new_doc("Customer")
		customer.customer_type = "Partnership"
		customer.customer_primary_contact = contact_name
		sync_customer_from_primary_contact(customer)

		self.assertEqual(customer.customer_type, "Partnership")

	def test_environment_default_fields_untouched_on_existing_customer(self):
		# A value that differs from the pristine default (here,
		# "Individual" vs the DocField default "Company") is never
		# overwritten on an already-saved Customer, whether or not the
		# pristine comparison itself runs - see
		# test_corrects_environment_default_on_existing_customer_when_contact_first_set
		# for the case where it *does* need to run and correct a field
		# still sitting at that default.
		lead = make_lead(company_name="Acme Corp")
		contact_name = lead_contact_name(lead.name)
		customer = make_customer(customer_name="Acme Corp Existing", customer_type="Individual")

		customer.customer_primary_contact = contact_name
		sync_customer_from_primary_contact(customer)

		self.assertEqual(customer.customer_type, "Individual")

	def test_corrects_environment_default_on_existing_customer_when_contact_first_set(self):
		# The confirmed real-world gap: Quick Entry (the actual "+ Add
		# Customer" flow) has no customer_primary_contact field at all, so
		# customer_type is already saved as "Company" (its pristine
		# default) by the time a user opens the full record and picks a
		# primary contact for the first time. Needs the real .save()
		# pipeline, not a direct hook call, for has_value_changed to mean
		# anything.
		lead = make_lead(company_name=None, first_name="Ahmed", last_name="Amin")
		contact_name = lead_contact_name(lead.name)
		customer = make_quick_entry_style_customer(customer_name="Ahmed Amin")
		self.assertEqual(customer.customer_type, "Company")  # sanity: the pristine default

		customer.customer_primary_contact = contact_name
		customer.save()

		self.assertEqual(customer.customer_type, "Individual")

	def test_does_not_correct_environment_default_on_existing_customer_when_contact_unchanged(self):
		# Proves this doesn't regress into "silently re-corrects on every
		# save" - only fires the save where customer_primary_contact
		# itself actually changes. After the first save correctly sets
		# customer_type from the Lead (observable here since the Lead is
		# Individual - Company is the pristine default, so a coincidental
		# match wouldn't prove anything), a later deliberate change away
		# from it must survive an unrelated resave (customer_primary_contact
		# itself untouched that time).
		lead = make_lead(company_name=None, first_name="Sara", last_name="Tarek")
		contact_name = lead_contact_name(lead.name)
		customer = make_quick_entry_style_customer(customer_name="Sara Tarek")

		customer.customer_primary_contact = contact_name
		customer.save()
		self.assertEqual(customer.customer_type, "Individual")  # confirms the correction actually ran

		customer.customer_type = "Partnership"  # deliberate change, unrelated to the primary contact
		customer.customer_details = "unrelated edit"
		customer.save()

		self.assertEqual(customer.customer_type, "Partnership")

	def test_does_not_overwrite_deliberately_chosen_type_on_existing_customer(self):
		lead = make_lead(company_name="Acme Corp")
		contact_name = lead_contact_name(lead.name)
		customer = make_quick_entry_style_customer(customer_name="Acme Corp Existing")
		customer.customer_type = "Partnership"
		customer.save()

		customer.customer_primary_contact = contact_name
		customer.save()

		self.assertEqual(customer.customer_type, "Partnership")

	def test_backfills_primary_contact_and_address_from_lead_name_when_blank(self):
		# The other entry point into the same Lead/Address/Contact triangle:
		# a Customer built with lead_name already set (e.g. ERPNext's own
		# native "Create > Customer" button on Lead) but neither
		# customer_primary_contact nor customer_primary_address populated
		# yet - link_address_and_contact() (erpnext core) only Dynamic-Links
		# these, it never sets either field itself. Both need backfilling
		# during validate(), before the mandatory check runs.
		lead = make_lead(company_name="Acme Corp")
		contact_name = lead_contact_name(lead.name)
		address = make_lead_address(lead.name)

		customer = frappe.new_doc("Customer")
		customer.lead_name = lead.name
		sync_customer_from_primary_contact(customer)

		self.assertEqual(customer.customer_primary_contact, contact_name)
		self.assertEqual(customer.customer_primary_address, address.name)


class TestLinkPrimaryContact(FrappeTestCase):
	def test_links_contact_back_to_customer(self):
		contact = make_contact()
		customer = make_customer(customer_primary_contact=contact.name)

		link_primary_contact(customer)

		contact.reload()
		self.assertTrue(contact.has_link("Customer", customer.name))

	def test_idempotent_does_not_duplicate_link(self):
		contact = make_contact()
		customer = make_customer(customer_primary_contact=contact.name)

		_ensure_contact_linked_to_customer(customer)
		_ensure_contact_linked_to_customer(customer)

		contact.reload()
		matches = [
			link
			for link in contact.links
			if link.link_doctype == "Customer" and link.link_name == customer.name
		]
		self.assertEqual(len(matches), 1)

	def test_links_even_when_contact_predates_customer_and_has_no_lead(self):
		# The exact scenario the user flagged: a Contact created before any
		# Customer exists, unrelated to a Lead - should still end up linked.
		contact = make_contact()
		customer = make_customer(customer_primary_contact=contact.name)

		link_primary_contact(customer)

		contact.reload()
		self.assertTrue(contact.has_link("Customer", customer.name))

	def test_links_the_address_back_to_the_customer(self):
		from contact_enhancements.tests.test_supplier_hooks import make_address

		contact = make_contact()
		address = make_address()
		customer = make_customer(customer_primary_contact=contact.name, customer_primary_address=address.name)

		link_primary_contact(customer)

		address.reload()
		self.assertTrue(address.has_link("Customer", customer.name))

	def test_converts_lead_when_lead_name_set(self):
		lead = make_lead(company_name="Acme Corp")
		contact_name = lead_contact_name(lead.name)
		customer = make_customer(customer_name="Acme Corp Existing", customer_primary_contact=contact_name)
		customer.lead_name = lead.name
		customer.save()

		link_primary_contact(customer)

		lead.reload()
		self.assertEqual(lead.status, "Converted")

	def test_shares_address_linked_to_lead_with_customer(self):
		# The other half of "one Contact, linked to both" - an Address
		# Dynamic-Linked to the Lead should end up Dynamic-Linked to the
		# Customer too (not duplicated), via link_address_and_contact(),
		# which is confirmed to handle parenttype "Address" the same way it
		# handles "Contact".
		lead = make_lead(company_name="Acme Corp")
		contact_name = lead_contact_name(lead.name)
		address = frappe.get_doc(
			{
				"doctype": "Address",
				"address_title": "Acme HQ",
				"address_type": "Office",
				"address_line1": "1 Acme Way",
				"city": "Cairo",
				"country": "Egypt",
				"links": [{"link_doctype": "Lead", "link_name": lead.name}],
			}
		)
		address.insert(ignore_permissions=True)

		customer = make_customer(customer_name="Acme Corp Existing", customer_primary_contact=contact_name)
		customer.lead_name = lead.name
		customer.save()

		link_primary_contact(customer)

		address.reload()
		self.assertTrue(address.has_link("Customer", customer.name))
		self.assertTrue(address.has_link("Lead", lead.name))

	def test_syncs_whatsapp_number_via_hook(self):
		lead = make_lead(company_name="Acme Corp", whatsapp_no="01077788811")
		contact_name = lead_contact_name(lead.name)
		customer = make_customer(customer_name="Acme Corp Existing", customer_primary_contact=contact_name)
		customer.lead_name = lead.name
		customer.save()

		link_primary_contact(customer)

		contact = frappe.get_doc("Contact", contact_name)
		matches = [row for row in contact.phone_nos if row.phone == "+201077788811"]
		self.assertEqual(len(matches), 1)
		self.assertEqual(matches[0].custom_whatsapp, 1)

	def test_does_not_redo_lead_conversion_work_on_unrelated_resave(self):
		# Needs the real save() pipeline (not a direct hook call) to
		# actually exercise has_value_changed - a direct call never has
		# doc_before_save populated, so it always falls back to "changed".
		lead = make_lead(company_name="Acme Corp")
		contact_name = lead_contact_name(lead.name)
		customer = make_customer(customer_name="Acme Corp Existing", customer_primary_contact=contact_name)
		customer.lead_name = lead.name
		customer.save()  # first save: does the real conversion work

		lead.reload()
		modified_after_first_save = lead.modified

		customer.customer_details = "unrelated edit"
		customer.save()  # second save: nothing lead-relevant changed

		lead.reload()
		self.assertEqual(lead.modified, modified_after_first_save)

	def test_updates_stale_lead_name_when_primary_contact_changes_to_different_lead(self):
		old_lead = make_lead(company_name="Old Co")
		old_contact_name = lead_contact_name(old_lead.name)
		new_lead = make_lead(company_name="New Co")
		new_contact_name = lead_contact_name(new_lead.name)

		customer = make_customer(customer_name="Old Co Existing", customer_primary_contact=old_contact_name)
		customer.lead_name = old_lead.name
		customer.save()

		customer.customer_primary_contact = new_contact_name
		customer.save()

		self.assertEqual(customer.lead_name, new_lead.name)
		new_lead.reload()
		self.assertEqual(new_lead.status, "Converted")

	def test_ensure_contact_linked_reuses_a_passed_in_contact_without_reloading(self):
		# The DRY/perf fix: _ensure_contact_linked_to_customer must not call
		# frappe.get_doc again when the caller already has the Contact loaded.
		contact = make_contact()
		customer = make_customer(customer_primary_contact=contact.name)
		# make_customer's own insert already ran link_primary_contact once
		# (a real doc_events on_update), which already linked and saved
		# this Contact - reload so this test's own local copy matches the
		# DB before re-exercising the reuse path in isolation, or the
		# stale in-memory .modified here would fail check_if_latest() on
		# any further .save(), same class of bug this app fixed earlier.
		contact.reload()

		load_calls = []
		original_get_doc = frappe.get_doc

		def counting_get_doc(*args, **kwargs):
			if args[:2] == ("Contact", contact.name):
				load_calls.append(args)
			return original_get_doc(*args, **kwargs)

		frappe.get_doc = counting_get_doc
		try:
			returned = _ensure_contact_linked_to_customer(customer, contact=contact)
		finally:
			frappe.get_doc = original_get_doc

		self.assertEqual(load_calls, [])
		self.assertIs(returned, contact)
		contact.reload()
		self.assertTrue(contact.has_link("Customer", customer.name))

	def test_link_primary_contact_does_not_reload_an_already_linked_contact(self):
		# History of this assertion: originally
		# _ensure_contact_linked_to_customer and
		# _sync_whatsapp_to_customer_contact each loaded their own copy of
		# the same Contact (2 loads); that was cut to 1 by loading once and
		# passing it through. It is now 0, because
		# ensure_doc_linked_to_parent answers "is it already linked?" with
		# one indexed Dynamic Link read instead of loading the whole
		# Contact (4 SELECTs) just to call has_link() - and on this
		# already-saved Customer the link is of course already there.
		# The WhatsApp sync itself is covered by
		# test_syncs_whatsapp_number_via_hook, not by this test.
		lead = make_lead(company_name="Acme Corp", whatsapp_no="01077788899")
		contact_name = lead_contact_name(lead.name)
		customer = make_customer(customer_name="Acme Corp Existing", customer_primary_contact=contact_name)
		customer.lead_name = lead.name
		customer.save()

		load_calls = []
		original_get_doc = frappe.get_doc

		def counting_get_doc(*args, **kwargs):
			if args[:2] == ("Contact", contact_name):
				load_calls.append(args)
			return original_get_doc(*args, **kwargs)

		frappe.get_doc = counting_get_doc
		try:
			link_primary_contact(customer)
		finally:
			frappe.get_doc = original_get_doc

		self.assertEqual(len(load_calls), 0)
		# The WhatsApp number is still on the Contact (synced by the
		# customer.save() above, which ran the same hook for real).
		contact = frappe.get_doc("Contact", contact_name)
		matches = [row for row in contact.phone_nos if row.phone == "+201077788899"]
		self.assertEqual(len(matches), 1)

	def test_resync_modified_runs_when_customer_primary_address_is_new(self):
		# The gating fix: _resync_modified_after_side_effect_saves must run
		# on the save where customer_primary_address is genuinely new
		# (is_new() here) - the one save where link_address_and_contact()
		# might actually Dynamic-Link that Address for the first time.
		lead = make_lead(company_name="Acme Corp")
		contact_name = lead_contact_name(lead.name)
		address = make_lead_address(lead.name)

		customer = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": frappe.generate_hash(length=10),
				"customer_type": "Individual",
				"customer_group": "Individual",
				"territory": "Rest Of The World",
				"customer_primary_contact": contact_name,
				"customer_primary_address": address.name,
				"lead_name": lead.name,
			}
		)

		resync_calls = []
		import contact_enhancements.customer_hooks as customer_hooks_module

		original_resync = customer_hooks_module._resync_modified_after_side_effect_saves

		def counting_resync(doc):
			resync_calls.append(doc.name)
			return original_resync(doc)

		customer_hooks_module._resync_modified_after_side_effect_saves = counting_resync
		try:
			customer.insert(ignore_permissions=True)
		finally:
			customer_hooks_module._resync_modified_after_side_effect_saves = original_resync

		self.assertEqual(len(resync_calls), 1)

	def test_resync_modified_skipped_when_customer_primary_address_unchanged(self):
		# The other half: a routine resave where customer_primary_address
		# was already linked in a prior save (unchanged this time) must
		# NOT re-run the resync - only lead_name changes here, which alone
		# used to be enough to trigger it unconditionally.
		lead = make_lead(company_name="Acme Corp")
		contact_name = lead_contact_name(lead.name)
		address = make_lead_address(lead.name)
		customer = make_customer(
			customer_name="Acme Corp Existing",
			customer_primary_contact=contact_name,
			customer_primary_address=address.name,
		)
		customer.lead_name = lead.name
		customer.save()  # customer_primary_address already set since insert - unchanged here too

		resync_calls = []
		import contact_enhancements.customer_hooks as customer_hooks_module

		original_resync = customer_hooks_module._resync_modified_after_side_effect_saves

		def counting_resync(doc):
			resync_calls.append(doc.name)
			return original_resync(doc)

		customer_hooks_module._resync_modified_after_side_effect_saves = counting_resync
		try:
			customer.customer_details = "unrelated edit"
			customer.save()  # customer_primary_address still unchanged this save
		finally:
			customer_hooks_module._resync_modified_after_side_effect_saves = original_resync

		self.assertEqual(resync_calls, [])


class TestPrimaryContactRequirementIsGrandfathered(FrappeTestCase):
	"""The requirement applies to records created from now on, never
	retroactively. A static reqd=1 was evaluated on every save, not just
	inserts, so it froze every Customer/Supplier that predated this app -
	a no-op re-save raised MandatoryError, blocking ERPNext's own flows,
	Data Import and other apps as well as the Desk form."""

	def test_a_new_customer_still_needs_a_contact_and_address(self):
		doc = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": "Grandfather New " + frappe.generate_hash(length=6),
				"customer_type": "Individual",
				"customer_group": "Commercial",
				"territory": "All Territories",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_an_existing_customer_without_them_stays_editable(self):
		# Built bypassing validation, the way a pre-existing record looks.
		name = "Grandfather Legacy " + frappe.generate_hash(length=6)
		doc = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": name,
				"customer_type": "Individual",
				"customer_group": "Commercial",
				"territory": "All Territories",
			}
		)
		doc.flags.ignore_validate = True
		doc.insert(ignore_permissions=True)

		reloaded = frappe.get_doc("Customer", doc.name)
		self.assertFalse(reloaded.customer_primary_contact)
		reloaded.save(ignore_permissions=True)  # must not raise

	def test_the_fields_are_no_longer_statically_mandatory(self):
		# Guards against the reqd Property Setter creeping back: it is what
		# made the requirement retroactive in the first place.
		meta = frappe.get_meta("Customer")
		self.assertFalse(meta.get_field("customer_primary_contact").reqd)
		self.assertFalse(meta.get_field("customer_primary_address").reqd)

	def test_a_new_customer_with_both_set_saves_normally(self):
		contact = make_contact()
		address = frappe.get_doc(
			{
				"doctype": "Address",
				"address_title": frappe.generate_hash(length=10),
				"address_type": "Billing",
				"address_line1": "1 Grandfather Street",
				"city": "Cairo",
				"country": "Egypt",
			}
		).insert(ignore_permissions=True)
		customer = make_customer(
			customer_primary_contact=contact.name, customer_primary_address=address.name
		)
		self.assertTrue(customer.name)


class TestCustomerIdentityFromContactCompanyName(FrappeTestCase):
	"""A Contact with a company_name represents someone AT a company, so
	the Customer created for them is that company - not a person named
	after the contact. The Lead snapshot already applied this rule to a
	Lead's own company_name; a Contact with no Lead behind it had no
	equivalent."""

	def test_a_contact_with_a_company_name_produces_a_company_customer(self):
		contact = make_contact(first_name="Omar Ahmed Sabry")
		frappe.db.set_value("Contact", contact.name, "company_name", "Acme Trading Co")

		customer = frappe.new_doc("Customer")
		customer.customer_primary_contact = contact.name
		sync_customer_from_primary_contact(customer)

		self.assertEqual(customer.customer_name, "Acme Trading Co")
		self.assertEqual(customer.customer_type, "Company")

	def test_a_contact_without_one_produces_an_individual(self):
		contact = make_contact(first_name="Omar Ahmed Sabry")

		customer = frappe.new_doc("Customer")
		customer.customer_primary_contact = contact.name
		sync_customer_from_primary_contact(customer)

		self.assertEqual(customer.customer_name, "Omar Ahmed Sabry")
		self.assertEqual(customer.customer_type, "Individual")

	def test_a_name_already_chosen_is_never_overwritten(self):
		# The guard that keeps this from fighting the dialog's own
		# "create a new Contact" path, where the user has already stated
		# Individual/Company and typed the matching name.
		contact = make_contact(first_name="Omar Ahmed Sabry")
		frappe.db.set_value("Contact", contact.name, "company_name", "Acme Trading Co")

		customer = frappe.new_doc("Customer")
		customer.customer_primary_contact = contact.name
		customer.customer_name = "Deliberately Chosen Name"
		customer.customer_type = "Individual"
		sync_customer_from_primary_contact(customer)

		self.assertEqual(customer.customer_name, "Deliberately Chosen Name")
		self.assertEqual(customer.customer_type, "Individual")

	def test_the_lead_snapshot_still_wins_when_there_is_one(self):
		lead = make_lead(company_name="Lead Company Ltd")
		contact_name = lead_contact_name(lead.name)
		frappe.db.set_value("Contact", contact_name, "company_name", "Contact Company Co")

		customer = frappe.new_doc("Customer")
		customer.customer_primary_contact = contact_name
		sync_customer_from_primary_contact(customer)

		self.assertEqual(customer.customer_name, "Lead Company Ltd")
		self.assertEqual(customer.customer_type, "Company")

	def test_it_saves_end_to_end_as_a_company(self):
		contact = make_contact(first_name="Omar Ahmed Sabry")
		frappe.db.set_value("Contact", contact.name, "company_name", "Acme End To End")
		address = frappe.get_doc(
			{
				"doctype": "Address",
				"address_title": frappe.generate_hash(length=10),
				"address_type": "Billing",
				"address_line1": "1 Company Street",
				"city": "Cairo",
				"country": "Egypt",
			}
		).insert(ignore_permissions=True)

		customer = frappe.new_doc("Customer")
		customer.customer_primary_contact = contact.name
		customer.customer_primary_address = address.name
		customer.customer_group = "Commercial"
		customer.territory = "All Territories"
		customer.insert(ignore_permissions=True)

		self.assertEqual(customer.customer_name, "Acme End To End")
		self.assertEqual(customer.customer_type, "Company")
