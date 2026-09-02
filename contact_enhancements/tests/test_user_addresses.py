"""Tests for contact_enhancements.api.user_addresses - a User's own
"Linked Addresses" section (public/js/user.js), backed directly by
Address's existing Dynamic Link mechanism rather than a new field or
child doctype."""

import frappe
from frappe.tests.utils import FrappeTestCase

from contact_enhancements.api.user_addresses import (
	create_and_link_address,
	get_all_addresses_for_user,
	get_user_addresses,
	link_existing_address_to_user,
	unlink_address_from_user,
)


def make_user(**kwargs):
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": frappe.generate_hash(length=8) + "@example.com",
			"first_name": "Test",
			"send_welcome_email": 0,
			**kwargs,
		}
	)
	user.flags.ignore_mandatory = True
	user.insert(ignore_permissions=True)
	return user


def make_address(**kwargs):
	address = frappe.get_doc(
		{
			"doctype": "Address",
			"address_title": frappe.generate_hash(length=10),
			"address_type": "Personal",
			"address_line1": "1 Test Street",
			"city": "Cairo",
			"country": "Egypt",
			**kwargs,
		}
	)
	address.insert(ignore_permissions=True)
	return address


class TestGetUserAddresses(FrappeTestCase):
	def test_lists_every_linked_address(self):
		user = make_user()
		address1 = make_address(links=[{"link_doctype": "User", "link_name": user.name}])
		address2 = make_address(links=[{"link_doctype": "User", "link_name": user.name}])

		names = {a.name for a in get_user_addresses(user.name)}

		self.assertEqual(names, {address1.name, address2.name})

	def test_empty_for_a_user_with_no_linked_addresses(self):
		user = make_user()
		self.assertEqual(get_user_addresses(user.name), [])

	def test_does_not_include_another_users_address(self):
		user = make_user()
		other_user = make_user()
		make_address(links=[{"link_doctype": "User", "link_name": other_user.name}])

		self.assertEqual(get_user_addresses(user.name), [])


class TestLinkExistingAddressToUser(FrappeTestCase):
	def test_links_the_address(self):
		user = make_user()
		address = make_address()

		link_existing_address_to_user(address.name, user.name)

		address.reload()
		self.assertTrue(address.has_link("User", user.name))

	def test_idempotent(self):
		user = make_user()
		address = make_address()

		link_existing_address_to_user(address.name, user.name)
		link_existing_address_to_user(address.name, user.name)

		address.reload()
		links = [l for l in address.links if l.link_doctype == "User" and l.link_name == user.name]
		self.assertEqual(len(links), 1)


class TestCreateAndLinkAddress(FrappeTestCase):
	def test_creates_and_links_a_new_address(self):
		user = make_user()

		address_name = create_and_link_address(
			user=user.name,
			address_title="New Addr",
			address_type="Personal",
			address_line1="2 New Street",
			city="Giza",
			country="Egypt",
		)

		address = frappe.get_doc("Address", address_name)
		self.assertTrue(address.has_link("User", user.name))
		self.assertEqual(address.city, "Giza")


class TestUnlinkAddressFromUser(FrappeTestCase):
	def test_removes_the_link_without_deleting_the_address(self):
		user = make_user()
		address = make_address(links=[{"link_doctype": "User", "link_name": user.name}])

		unlink_address_from_user(address.name, user.name)

		address.reload()
		self.assertFalse(address.has_link("User", user.name))
		self.assertTrue(frappe.db.exists("Address", address.name))

	def test_noop_when_no_link_exists(self):
		user = make_user()
		address = make_address()
		unlink_address_from_user(address.name, user.name)  # must not raise

	def test_only_removes_this_users_own_link(self):
		user = make_user()
		other_user = make_user()
		address = make_address(
			links=[
				{"link_doctype": "User", "link_name": user.name},
				{"link_doctype": "User", "link_name": other_user.name},
			]
		)

		unlink_address_from_user(address.name, user.name)

		address.reload()
		self.assertFalse(address.has_link("User", user.name))
		self.assertTrue(address.has_link("User", other_user.name))


class TestGetAllAddressesForUser(FrappeTestCase):
	def test_returns_empty_list_with_no_user_and_no_contact(self):
		self.assertEqual(get_all_addresses_for_user(), [])

	def test_includes_directly_linked_addresses_as_removable(self):
		user = make_user()
		address = make_address(links=[{"link_doctype": "User", "link_name": user.name}])

		results = get_all_addresses_for_user(user=user.name)

		self.assertEqual(len(results), 1)
		self.assertEqual(results[0]["name"], address.name)
		self.assertTrue(results[0]["removable"])
		self.assertEqual(results[0]["source_label"], "Linked directly")

	def test_includes_cross_doctype_addresses_as_not_removable(self):
		from contact_enhancements.tests.test_customer_hooks import make_customer
		from contact_enhancements.tests.test_lead_lookup import make_contact

		contact = make_contact()
		address = make_address()
		customer = make_customer(customer_primary_contact=contact.name, customer_primary_address=address.name)

		results = get_all_addresses_for_user(contact=contact.name)

		self.assertEqual(len(results), 1)
		self.assertEqual(results[0]["name"], address.name)
		self.assertFalse(results[0]["removable"])
		self.assertEqual(results[0]["source_label"], f"via Customer: {customer.customer_name}")

	def test_works_without_a_saved_user_as_long_as_contact_is_given(self):
		# The whole point of "sync live, before Save" - a brand-new,
		# unsaved User (no real name yet) still sees cross-doctype
		# addresses as soon as a Contact is picked.
		from contact_enhancements.tests.test_customer_hooks import make_customer
		from contact_enhancements.tests.test_lead_lookup import make_contact

		contact = make_contact()
		address = make_address()
		make_customer(customer_primary_contact=contact.name, customer_primary_address=address.name)

		results = get_all_addresses_for_user(user=None, contact=contact.name)

		self.assertEqual([r["name"] for r in results], [address.name])

	def test_merges_and_dedupes_direct_and_cross_doctype_results(self):
		from contact_enhancements.tests.test_customer_hooks import make_customer
		from contact_enhancements.tests.test_lead_lookup import make_contact

		contact = make_contact()
		direct_address = make_address()
		shared_address = make_address()
		user = make_user(user_primary_contact=contact.name)
		direct_address.append("links", {"link_doctype": "User", "link_name": user.name})
		direct_address.save(ignore_permissions=True)
		make_customer(customer_primary_contact=contact.name, customer_primary_address=shared_address.name)

		results = get_all_addresses_for_user(user=user.name, contact=contact.name)

		names = {r["name"] for r in results}
		self.assertEqual(names, {direct_address.name, shared_address.name})
		by_name = {r["name"]: r for r in results}
		self.assertTrue(by_name[direct_address.name]["removable"])
		self.assertFalse(by_name[shared_address.name]["removable"])
