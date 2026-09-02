# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

"""Duplicate Mobile Contacts - the admin-facing surface for Phase 0c's
find_duplicate_mobile_contacts(), so resolving pre-existing duplicate
mobile numbers is a real, discoverable workflow instead of something only
findable by reading Error Log entries.

This matters most on a fresh install of this app onto a site that already
has years of real Contact data - a brand new site starts with none of
this, but an existing one can easily have dozens of pre-existing
duplicates, and Phase 0d's own database-level uniqueness constraint
(add_contact_phone_unique_mobile_index) refuses to go live until every
group here is resolved. Backs the "Duplicate Mobile Contacts" Page
(contact_enhancements/page/duplicate_mobile_contacts) - one merged, boxed
cell per duplicate group naming the shared number, with every member
Contact's own row underneath it, ranked richest-business-weight first.

Originally built as a Script Report - moved to a hand-rolled Page instead
because Frappe's own report datatable (frappe-datatable, confirmed by
reading its bundled source directly) has no rowSpan/cell-merging support
at all, only a flat per-row grid, and a real merged group box was
explicitly wanted. A plain, non-virtualized HTML table is fully
appropriate here regardless: this is a small, occasional admin lookup
(a handful of duplicate groups on a real site, not a large dataset), not
something that needs a report view's own pagination/virtualization.

A Business Records column shows whether each Contact actually carries any
real business weight (a linked Customer/Supplier/Lead, or a Quotation/
Sales Order/Sales Invoice/Purchase Order/Purchase Invoice/Opportunity
naming it directly as contact_person) - the actual answer to "which of
these has financial records" an admin needs before picking a survivor.
Within each group, the Contact carrying the most business weight is
sorted first and flagged "Suggested" - a starting point, not a decision
made for the admin; every group's members and their own Business Records
are still shown so that can be overridden. A "Keep this Contact" button on
every row merges everyone else in that group into it in one click (contact_
dedupe.merge_duplicate_mobile_contacts, wrapping Frappe's own native
rename-with-merge) - no separate picker dialog needed. Reclassifying a row
custom_landline=1, or fixing a data-entry typo, are still done directly on
the Contact form - see contact_dedupe.find_duplicate_mobile_contacts's own
docstring for the full resolution workflow.
"""

import frappe
from frappe import _

from contact_enhancements.api.contact_dedupe import find_duplicate_mobile_contacts

# Transactional doctypes checked directly against Contact via their own
# contact_person field (confirmed present on each by reading their own
# doctype JSON) - deliberately a small, targeted list of what actually
# represents "business weight" for this decision, not frappe.desk.form.
# linked_with.get_linked_docs's generic every-linked-doctype sweep
# (Communication, ToDo, Event, etc.), which would be both noisier and
# more expensive to compute for every Contact in every duplicate group.
TRANSACTION_DOCTYPES = [
	"Quotation",
	"Sales Order",
	"Sales Invoice",
	"Purchase Order",
	"Purchase Invoice",
	"Opportunity",
]

# Party doctypes that can genuinely hold real transactions/financial
# history - a Contact Dynamic-Linked to one of these is a meaningfully
# stronger "this one matters" signal than a Lead/Prospect/Opportunity link
# (pre-sales, no money has moved yet), so ranking has to weight them
# separately, not just check "is this Contact linked to *something*".
STRONG_PARTY_DOCTYPES = {"Customer", "Supplier"}


@frappe.whitelist()
def get_report_data():
	"""Whitelisted entry point for the Duplicate Mobile Contacts page.

	Returns:
		{"groups": [...get_duplicate_groups()'s own return value...],
		"summary": {"group_count": <int>, "contact_count": <int>}}.
	"""
	groups = get_duplicate_groups()
	return {
		"groups": groups,
		"summary": {
			"group_count": len(groups),
			"contact_count": sum(len(group["members"]) for group in groups),
		},
	}


def _business_records(contact_names):
	"""For every given Contact, which party (Customer/Supplier/Lead/
	Prospect) it's Dynamic-Linked to, whether that party is a "strong"
	one (STRONG_PARTY_DOCTYPES), and how many transactional documents
	(TRANSACTION_DOCTYPES) directly name it as contact_person - the
	actual "does this Contact carry real business weight" signal,
	computed in a fixed, small number of batched queries (one for
	Dynamic Link, one per transaction doctype) regardless of how many
	Contacts are involved - never one query per Contact.

	Args:
		contact_names: list of Contact names to check.

	Returns:
		{contact_name: {"party": "Customer: ABC Corp" or "",
		"has_strong_party": bool, "transactions": <int>}}.
	"""
	if not contact_names:
		return {}

	party_by_contact = {}
	strong_party_contacts = set()
	for row in frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Contact", "parent": ["in", contact_names]},
		fields=["parent", "link_doctype", "link_name"],
	):
		party_by_contact.setdefault(row.parent, []).append(f"{row.link_doctype}: {row.link_name}")
		if row.link_doctype in STRONG_PARTY_DOCTYPES:
			strong_party_contacts.add(row.parent)

	transaction_counts = dict.fromkeys(contact_names, 0)
	for doctype in TRANSACTION_DOCTYPES:
		if not frappe.db.exists("DocType", doctype):
			# This app doesn't hard-depend on every one of these existing -
			# tolerate a site where e.g. Purchase Order's app isn't installed.
			continue
		for row in frappe.get_all(
			doctype, filters={"contact_person": ["in", contact_names]}, fields=["contact_person"]
		):
			transaction_counts[row.contact_person] = transaction_counts.get(row.contact_person, 0) + 1

	return {
		name: {
			"party": ", ".join(party_by_contact.get(name, [])),
			"has_strong_party": name in strong_party_contacts,
			"transactions": transaction_counts.get(name, 0),
		}
		for name in contact_names
	}


def _business_records_html(summary):
	"""Render one Contact's _business_records entry as a colored badge:
	grey "No business records" for nothing at all, blue for a link to a
	pre-sales-only party (Lead/Prospect - no money has moved), orange for
	genuine weight - a linked Customer/Supplier, or an actual transaction
	naming this Contact directly - with the specifics spelled out rather
	than just a bare count."""
	party = summary["party"]
	transactions = summary["transactions"]
	has_weight = summary["has_strong_party"] or transactions > 0

	if not party and not transactions:
		return f'<span class="indicator-pill grey no-indicator-dot">{_("No business records")}</span>'

	parts = []
	if party:
		parts.append(frappe.utils.escape_html(party))
	if transactions:
		parts.append(_("{0} transaction(s)").format(transactions))
	label = " · ".join(parts)
	color = "orange" if has_weight else "blue"
	return f'<span class="indicator-pill {color} no-indicator-dot">{label}</span>'


def get_duplicate_groups():
	"""Every duplicate-mobile group, each with its own members ranked by
	business weight (richest first) and flagged "suggested" where
	applicable - the Page's own source of truth for one merged/boxed row
	group.

	Returns:
		A list of {"phone": <E.164 value>, "members": [{"contact": name,
		"full_name": ..., "email_id": ..., "business_records_html": ...,
		"suggested": bool}, ...]} dicts, one per duplicate group, members
		already sorted richest-business-weight first.
	"""
	duplicates = find_duplicate_mobile_contacts()
	if not duplicates:
		return []

	contact_names = [name for entry in duplicates for name in entry["contacts"]]
	contact_details = {
		row.name: row
		for row in frappe.get_all(
			"Contact",
			filters={"name": ["in", contact_names]},
			fields=["name", "first_name", "email_id"],
		)
	}
	records = _business_records(contact_names)

	groups = []
	for entry in duplicates:
		# Sort the group's own members by business weight, richest first -
		# a starting-point suggestion for who should survive, never a
		# decision made silently: every member and its own Business
		# Records badge are still shown, so this can be overridden. A
		# linked Customer/Supplier (has_strong_party) ranks above a bare
		# transaction count, which ranks above a merely-linked Lead/
		# Prospect - a pre-sales link alone is a much weaker signal that
		# this is "the real one" than an actual paying-party relationship.
		ranked = sorted(
			entry["contacts"],
			key=lambda name: (
				records[name]["has_strong_party"],
				records[name]["transactions"],
				bool(records[name]["party"]),
			),
			reverse=True,
		)
		top = ranked[0]
		top_has_any_weight = records[top]["has_strong_party"] or records[top]["transactions"] > 0

		members = []
		for contact_name in ranked:
			detail = contact_details.get(contact_name)
			members.append(
				{
					"contact": contact_name,
					"full_name": detail.first_name if detail else "",
					"email_id": detail.email_id if detail else "",
					"business_records_html": _business_records_html(records[contact_name]),
					"suggested": contact_name == top and top_has_any_weight,
				}
			)
		groups.append({"phone": entry["phone"], "members": members})
	return groups
