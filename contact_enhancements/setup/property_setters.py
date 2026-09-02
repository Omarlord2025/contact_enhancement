# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

import frappe


def _make_link_field_mandatory(doctype, fieldname):
	"""Make one Link field on `doctype` genuinely required (Desk UI, API,
	Data Import - see this module's own callers for why a Property Setter,
	not just a client-side nudge). Extracted (Phase 1) from what used to be
	inlined twice in create_contact_enhancements_property_setters
	(customer_primary_contact/customer_primary_address) - Supplier needed
	the exact same shape for supplier_primary_contact.

	frappe.make_property_setter is idempotent here: Property Setter's own
	autoname is deterministic ("<doctype>-<fieldname>-reqd"), and its
	validate() deletes any existing one with that same key before
	inserting - safe to call on every install/migrate.
	"""
	frappe.make_property_setter(
		{
			"doctype": doctype,
			"fieldname": fieldname,
			"property": "reqd",
			"value": "1",
			"property_type": "Check",
		}
	)


def _disable_quick_entry(doctype):
	"""Disable `doctype`'s own Quick Entry dialog entirely, via a Property
	Setter on the doctype-level quick_entry property - never a client-side
	is_quick_entry() override (confirmed by testing: a doctype's own
	doctype_js content only gets evaluated once a real frappe.ui.form.Form
	is constructed, and Quick Entry never constructs one until *after* its
	own dialog decision is already made, so there is no reliable point at
	which JS-only logic could intercept that decision in time).

	Extracted (Phase 1) from what used to be inlined in
	create_contact_enhancements_property_setters for Customer alone -
	Supplier needed the same treatment, for the same underlying reason:
	its own Quick Entry field list doesn't include the newly-mandatory
	primary-contact field, so leaving Quick Entry enabled would let it be
	bypassed entirely.
	"""
	frappe.make_property_setter(
		{
			"doctype": doctype,
			"doctype_or_field": "DocType",
			"fieldname": None,
			"property": "quick_entry",
			"value": "0",
			"property_type": "Check",
		}
	)


def create_contact_enhancements_property_setters():
	"""Make Customer Primary Contact and Customer Primary Address mandatory,
	so every Customer is genuinely required to have a linked Contact and
	Address - enforced everywhere (Desk UI, API, Data Import), not just
	nudged. In the Desk UI this is satisfied through the mandatory
	contact-picker dialog in contact_enhancements/public/js/customer.js,
	which also live-prefills customer_primary_address from the picked
	Contact's linked Lead when one is available (contact_enhancements/
	api/lead_lookup.py's _get_lead_address) - if the Lead has no Address
	linked to it yet, the user has to enter one manually before saving,
	same as any other genuinely-missing mandatory field. Nothing here
	synthesizes a placeholder Address from a Lead's flat city/state/country
	fields - there's no clean mapping to Address's own mandatory
	address_line1, and fabricating one would risk polluting real business
	data.

	Also disables Customer's own Quick Entry (its field list has no
	customer_primary_contact or customer_primary_address at all, confirmed
	by driving it directly, so leaving it on would let a user create a
	Customer and never see the mandatory dialog). This has to be a Property
	Setter on quick_entry itself, not a client-side override of
	is_quick_entry() - confirmed by testing that a doctype's doctype_js
	content (which is where such an override would have to live) is only
	evaluated once a real frappe.ui.form.Form is constructed for that
	doctype (frappe.ui.form.ScriptManager.setup() reads it off
	frm.meta.__js), and Quick Entry never constructs one until *after* its
	own dialog decision is already made - there's no reliable point at
	which JS-only logic can intercept that decision in time.

	frappe.make_property_setter is idempotent here (via the shared
	_make_link_field_mandatory/_disable_quick_entry helpers this now
	calls): Property Setter's own autoname is deterministic, and its
	validate() deletes any existing one with that same key before
	inserting - safe to call on every install/migrate.
	"""
	_make_link_field_mandatory("Customer", "customer_primary_contact")
	_make_link_field_mandatory("Customer", "customer_primary_address")
	_disable_quick_entry("Customer")


def create_contact_enhancements_index_property_setters():
	"""Index Customer.customer_primary_contact/customer_primary_address -
	confirmed missing on this site (SHOW INDEX FROM `tabCustomer` has no
	entry for either column, only customer_name/customer_group/mobile_no/
	modified) despite customer_primary_address specifically being hit with
	an exact-match WHERE filter on every single Address save site-wide,
	not just ones this app cares about: erpnext.accounts.custom.address.
	ERPNextAddress.on_update() unconditionally runs
	frappe.db.get_all("Customer", filters={"customer_primary_address":
	self.name}) - confirmed by reading that method (it's also the exact
	method already documented in customer_hooks.py's
	_resync_modified_after_side_effect_saves for an unrelated reason).
	Before this app existed that column was rarely populated at all;
	now that create_contact_enhancements_property_setters makes it
	genuinely mandatory and backfills it aggressively, that native query
	matches something on every Address save far more often, making an
	unindexed full scan of tabCustomer a real, evidenced cost - cheap to
	add now while the table is small, expensive to retrofit later once
	it isn't. customer_primary_contact gets the same treatment for
	symmetry and because Desk's own generic list-view filters let a user
	filter Customer by either field directly, hitting the same
	unindexed-scan cost.

	Deliberately NOT indexing the columns this app's own *search* queries
	filter on with a leading wildcard (Contact.full_name, in
	api/lead_lookup.py's search_contact_by_phone/search_contacts_with_details/
	_contact_search_query) - those are `LIKE "%txt%"` filters, which a
	standard B-tree index cannot use efficiently regardless of whether one
	exists (MariaDB/InnoDB can only use a B-tree index for a *prefix* LIKE,
	e.g. "txt%"). A FULLTEXT index would genuinely help there, but that's
	a materially bigger, separate schema decision this app's existing
	idempotent Property Setter mechanism doesn't express - flagged as a
	real option for later, not added speculatively here. Contact.email_id
	and Contact Phone.phone get indexed separately below
	(create_contact_dedupe_index_property_setters), for a completely
	different, genuinely indexable reason: exact-equality lookups.

	frappe.make_property_setter is idempotent here, same as
	create_contact_enhancements_property_setters - safe to call on every
	install/migrate.

	Setting the property alone does NOT alter the table - confirmed by
	reading frappe/model/sync.py's import_file_by_path: `bench migrate`'s
	schema sync skips re-importing a DocType entirely when its own JSON
	file's md5 hash (stored as DocType.migration_hash) hasn't changed,
	which is exactly the case here since this only adds a Property
	Setter, not a change to Customer's JSON file - so a plain `bench
	migrate` after this runs would silently leave the index missing,
	looking like it worked (no errors) while doing nothing. The correct
	mechanism, confirmed by reading frappe/custom/doctype/customize_form/
	customize_form.py (what "Customize Form" itself calls after saving a
	Property Setter), is to explicitly call frappe.db.updatedb(doctype)
	right here - it re-reads the doctype's merged meta (JSON + Property
	Setters) and issues the ALTER TABLE itself, immediately and
	idempotently.
	"""
	frappe.make_property_setter(
		{
			"doctype": "Customer",
			"fieldname": "customer_primary_address",
			"property": "search_index",
			"value": "1",
			"property_type": "Check",
		}
	)
	frappe.make_property_setter(
		{
			"doctype": "Customer",
			"fieldname": "customer_primary_contact",
			"property": "search_index",
			"value": "1",
			"property_type": "Check",
		}
	)
	frappe.db.updatedb("Customer")


def create_contact_dedupe_index_property_setters():
	"""Index Contact.email_id and Contact Phone.phone for
	api/contact_dedupe.py's find_contacts_by_email/find_contacts_by_phone -
	both are exact-equality lookups (unlike this app's own *search*
	queries, which filter with a leading-wildcard LIKE a B-tree index
	can't serve regardless - see create_contact_enhancements_index_
	property_setters's own docstring for that distinction), run on every
	single Contact validate via warn_if_duplicate_contact, so an unindexed
	full scan here is a real, evidenced cost, not a speculative one.

	Same frappe.db.updatedb() requirement as every other schema-affecting
	Property Setter in this app - setting the property alone does not
	alter the table (see create_contact_enhancements_index_property_setters's
	own docstring for the full mechanism).
	"""
	frappe.make_property_setter(
		{
			"doctype": "Contact",
			"fieldname": "email_id",
			"property": "search_index",
			"value": "1",
			"property_type": "Check",
		}
	)
	frappe.make_property_setter(
		{
			"doctype": "Contact Phone",
			"fieldname": "phone",
			"property": "search_index",
			"value": "1",
			"property_type": "Check",
		}
	)
	# Contact Phone is a child doctype with its own table (tabContact
	# Phone) - updatedb() operates per-doctype, so indexing a field on it
	# needs its own call, separate from Contact's.
	frappe.db.updatedb("Contact")
	frappe.db.updatedb("Contact Phone")


def create_primary_contact_fan_out_indexes():
	"""Index every "primary contact" field contact_hooks.
	propagate_contact_changes_to_linked_doctypes fans out across, so that
	hook's own lookup is an indexed read rather than a table scan.

	That hook runs on every Contact save whose full_name/email_id/mobile_no
	changed, and asks each target doctype "which of your records point at
	this Contact?" - one frappe.get_all filtered on that doctype's own
	primary-contact field. Only Customer's was indexed (by
	create_contact_enhancements_index_property_setters, for a different
	reason), so Supplier, Employee and User were each scanned in full every
	time. tabUser and tabEmployee are precisely the tables you don't want
	scanned on a hot write path.

	Employee's and User's fields are this app's own Custom Fields, so they
	carry "search_index": 1 in setup/custom_fields.py directly - the
	natural home for a property of a field this app defines. Only
	Supplier's is a native ERPNext field, so only it needs a Property
	Setter here.

	Same frappe.db.updatedb() requirement as every other schema-affecting
	Property Setter in this app - setting the property (or the Custom
	Field flag) alone does not alter the table (see
	create_contact_enhancements_index_property_setters's own docstring for
	the full mechanism). All three doctypes are updated here, not just
	Supplier, because the two Custom Field flags need the same ALTER TABLE
	pass and nothing else triggers one for them.
	"""
	frappe.make_property_setter(
		{
			"doctype": "Supplier",
			"fieldname": "supplier_primary_contact",
			"property": "search_index",
			"value": "1",
			"property_type": "Check",
		}
	)
	frappe.db.updatedb("Supplier")
	frappe.db.updatedb("Employee")
	frappe.db.updatedb("User")


def create_transaction_contact_person_indexes():
	"""Index contact_person on every transaction doctype the Duplicate
	Mobile Contacts page counts transactions against.

	api/duplicate_mobile_contacts._business_records asks each of these
	"how many of your records name these Contacts?" - unindexed, that is a
	full scan of Sales Invoice, Sales Order, Purchase Invoice and friends,
	which are the largest tables in an ERPNext site, six of them, inside a
	single page load.

	Deliberately its own patch, separate from the rest: unlike every other
	index this app adds, these ALTER TABLE statements run against
	genuinely large production tables and are not instant. Run it in a
	maintenance window. It is also the one index set that buys nothing on
	a small site - it exists for the 10k+ Contact case, where the page
	otherwise risks timing out.

	Reads the doctype list from api/duplicate_mobile_contacts rather than
	repeating it, so the two can't drift: any doctype added to that count
	needs this index too.

	Same frappe.db.updatedb() requirement as every other schema-affecting
	Property Setter here (see create_contact_enhancements_index_property_
	setters's own docstring for the mechanism). Doctypes that aren't
	installed on this site are skipped rather than erroring.
	"""
	from contact_enhancements.api.duplicate_mobile_contacts import TRANSACTION_DOCTYPES

	installed = set(
		frappe.get_all("DocType", filters={"name": ["in", list(TRANSACTION_DOCTYPES)]}, pluck="name")
	)
	for doctype in TRANSACTION_DOCTYPES:
		if doctype not in installed:
			continue
		frappe.make_property_setter(
			{
				"doctype": doctype,
				"fieldname": "contact_person",
				"property": "search_index",
				"value": "1",
				"property_type": "Check",
			}
		)
		frappe.db.updatedb(doctype)


def create_contact_full_name_property_setters():
	"""Relabel Contact.first_name to "Full Name" (Phase 0e) - the practical
	pattern already in use everywhere a Contact gets created through this
	app (Lead auto-creation, the mandatory contact-picker dialog,
	create_minimal_contact) has only ever asked for one name field. No
	change to the underlying fieldname or normalize_arabic_first_name/
	normalize_contact_first_name, which already operate per-word - a
	multi-word full name is already handled correctly, not just a single
	first name.

	Also hides Contact.middle_name, but deliberately does NOT hide
	last_name - the plan's own companion recommendation was explicitly
	flagged as an inference needing a frappe.db.count confirmation first,
	not an instruction to hide both unconditionally. Confirmed directly
	on this site: middle_name is blank on every existing Contact (safe to
	hide - no data becomes unreachable), but last_name has real,
	non-blank values on 6 existing Contacts - hiding it would make that
	data invisible in the UI, a real regression for those records, so it
	stays visible.
	"""
	frappe.make_property_setter(
		{
			"doctype": "Contact",
			"fieldname": "first_name",
			"property": "label",
			"value": "Full Name",
			"property_type": "Data",
		}
	)
	frappe.make_property_setter(
		{
			"doctype": "Contact",
			"fieldname": "middle_name",
			"property": "hidden",
			"value": "1",
			"property_type": "Check",
		}
	)


def create_contact_phone_hidden_field_property_setters():
	"""Phase 0f: hide Contact Phone.is_primary_phone via a real Property
	Setter, replacing the client-side grid.update_docfield_property hack
	public/js/contact.js used to carry.

	That hack only ever patched the *live-edit* grid rendering path
	(grid.update_docfield_property + resetting the memoized grid.
	visible_columns - see contact_enhancements/CLAUDE.md's own gotcha on
	why that reset was needed even for the live-edit path). A saved/
	reloaded document's *static* row rendering builds its own column list
	from the docfield's meta directly - a different code path the
	client-side patch never touched at all, which is exactly why a stray
	"Is Primary Phone" edit-pencil kept showing up between the Telegram
	and Landline columns specifically after save, never on a fresh
	unsaved form. A real hidden=1 here covers every rendering context,
	both live-edit and static, in one place.

	Deliberately Contact Phone.is_primary_phone, not is_primary_mobile_no
	(the field this app's own dialogs and hooks actually read/set) -
	is_primary_phone is a separate, legacy field this app doesn't use.
	Safe to hide unconditionally: Contact Phone is only ever embedded by
	Contact itself (confirmed by grepping every doctype definition in this
	bench for "options": "Contact Phone" - see CLAUDE.md), so there is no
	second parent doctype's own form this could affect.
	"""
	frappe.make_property_setter(
		{
			"doctype": "Contact Phone",
			"fieldname": "is_primary_phone",
			"property": "hidden",
			"value": "1",
			"property_type": "Check",
		}
	)


def create_supplier_contact_property_setters():
	"""Phase 1: make Supplier Primary Contact genuinely mandatory, and
	disable Supplier's own Quick Entry - the same treatment Customer got,
	using the shared _make_link_field_mandatory/_disable_quick_entry
	helpers those were extracted into.

	Deliberately asymmetric to Customer in two ways, each verified before
	writing this function, not assumed by analogy:

	1. supplier_primary_address is left optional. Unlike Customer, Supplier
	   has no Lead/Opportunity/Prospect conversion source feeding an
	   address automatically, so forcing it via a non-cancelable dialog
	   would add friction with no duplicate-prevention benefit - Suppliers
	   are routinely onboarded with just a contact person, address
	   following at first PO.
	2. Supplier.create_primary_contact() (native on_update) only fires
	   `if not self.supplier_primary_contact` - confirmed by reading
	   erpnext/buying/doctype/supplier/supplier.py directly. Once that
	   field is reqd=1, _validate_mandatory() (which runs before
	   on_update) guarantees no doc ever reaches on_update blank, so this
	   native path becomes dead code automatically - no defensive code
	   needed, just documented here. create_primary_address() reads
	   self.get("address_line1"), a field that doesn't exist on Supplier's
	   own schema at all (confirmed the same way) - inert regardless.

	No bulk backfill patch for existing legacy Suppliers - mirrors the
	same, already-accepted precedent set for Customer
	(patches/make_customer_primary_contact_mandatory.py): self-heals the
	next time a record is touched; an untouched legacy record stays
	unsavable until someone supplies the missing link, the same tradeoff
	already made for Customer.
	"""
	_make_link_field_mandatory("Supplier", "supplier_primary_contact")
	_disable_quick_entry("Supplier")




def create_employee_full_name_property_setters():
	"""Relabel Employee.first_name to "Full Name", the same practical
	pattern already established for Contact.first_name (Phase 0e,
	create_contact_full_name_property_setters) - once employee.js's own
	employee_primary_contact field handler prefills it from a picked
	Contact's own full_name (a whole name, not just a first name), the
	native "First Name" label would be actively misleading.

	Also hides Employee.employee_name - unlike Contact.full_name (already
	hidden natively), Employee's own native read-only "Full Name" field
	(auto-computed by joining first_name/middle_name/last_name,
	Employee.set_employee_name()) is visible by default and already
	carries that exact label - confirmed live via frappe.get_meta -
	leaving both visible at once would show two identically-labeled
	fields. No functional loss: employee_name keeps computing and
	populating correctly under the hood (every other doctype's own
	"Employee" Link field title lookup reads its stored value, not its
	form visibility), it just isn't shown a second time on this form.
	"""
	frappe.make_property_setter(
		{
			"doctype": "Employee",
			"fieldname": "first_name",
			"property": "label",
			"value": "Full Name",
			"property_type": "Data",
		}
	)
	frappe.make_property_setter(
		{
			"doctype": "Employee",
			"fieldname": "employee_name",
			"property": "hidden",
			"value": "1",
			"property_type": "Check",
		}
	)


def create_user_contact_property_setters():
	"""Disable User's own native Quick Entry (quick_entry: 1 natively,
	confirmed via frappe.get_meta) - without this, clicking "+ New User"
	shows Frappe's own short Quick Entry dialog (Email/First Name/Send
	Welcome Email only) and lets a User be created and saved without ever
	reaching the full form at all - the one and only place public/js/
	user.js's own onboarding dialog (user_primary_contact) can run, since
	a doctype's own doctype_js content only gets evaluated once a real
	frappe.ui.form.Form is constructed, and Quick Entry never constructs
	one until *after* its own dialog decision is already made (the same
	reasoning _disable_quick_entry's own docstring already documents for
	Customer/Supplier, confirmed live for User specifically here too).

	Also relabels User.first_name to "Full Name", the same practical
	pattern already established for Contact.first_name (Phase 0e) and
	Employee.first_name (Phase 3) - user.js's own onboarding dialog
	prefills it from a picked Contact's own full_name (a whole name, not
	just a first name), so the native "First Name" label would be
	actively misleading here too.
	"""
	_disable_quick_entry("User")
	frappe.make_property_setter(
		{
			"doctype": "User",
			"fieldname": "first_name",
			"property": "label",
			"value": "Full Name",
			"property_type": "Data",
		}
	)
