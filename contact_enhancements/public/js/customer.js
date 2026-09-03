// Copyright (c) 2026, Omar Sabry and contributors
// For license information, please see license.txt

// Frappe pre-fills these on a brand-new Customer before the user does
// anything (customer_type defaults to "Company"; territory/customer_group/
// language come from Selling Settings and session defaults) - see
// contact_enhancements/customer_hooks.py's FIELDS_WITH_ENVIRONMENT_DEFAULTS
// for the full explanation and why "still blank" can't be trusted for
// these. On a new form the Lead wins for these, but only while the field
// still exactly matches a pristine, never-touched Customer - not just
// because the form is new - so a value the user already deliberately
// picked is never overwritten. Every other field only fills in when
// genuinely blank.
const FIELDS_WITH_ENVIRONMENT_DEFAULTS = ["customer_type", "customer_group", "territory", "language"];

// Per-country phone validation now happens server-side, for every country,
// not just Egypt - contact_enhancements.contact_hooks.
// normalize_and_validate_contact_phones (a Contact validate hook, so it
// covers every Contact save path, not only this dialog) via the
// phonenumbers library. The old hand-rolled Egypt-only regex that used to
// live here (normalize_egypt_phone / find_phone_violation_for_country) is
// gone - phonenumbers' own per-country rule tables aren't something worth
// reimplementing by hand in JS (see contact_hooks.py's own module
// docstring for the full reasoning). This dialog's phone field still gets
// a clear error the moment "Create New Contact" is submitted - just via
// the frappe.call below and its own error() callback, not an instant
// keystroke check.

// Shared by both the live Lead-snapshot prefill loop (customer_primary_contact
// below) and the mandatory dialog's own customer_group field - a value the
// user already deliberately picked (including one just picked in that
// dialog) is never overwritten, but a field still sitting at its
// environment default (FIELDS_WITH_ENVIRONMENT_DEFAULTS) is fair game.
// This used to be inlined three times (once generically in the forEach
// loop, once by hand for customer_group inside primary_action) - one copy
// now, called from both places.
function apply_environment_default_field(frm, fieldname, value) {
	if (!value) return;
	const still_pristine =
		frm.__pristine_defaults &&
		FIELDS_WITH_ENVIRONMENT_DEFAULTS.includes(fieldname) &&
		frm.doc[fieldname] === frm.__pristine_defaults[fieldname];
	if (still_pristine || !frm.doc[fieldname]) {
		frm.set_value(fieldname, value);
	}
}

// A server error inside the mandatory contact-picker dialog (static: true,
// no_cancel()) leaves the user with no way to close it and start over, so
// every frappe.call in this file needs its own explicit error feedback,
// not just Frappe's own default handling - see contact_enhancements/
// CLAUDE.md's performance checklist.
function show_dialog_call_error(message) {
	frappe.msgprint({
		title: __("Something Went Wrong"),
		message,
		indicator: "red",
	});
}

frappe.ui.form.on("Customer", {
	onload(frm) {
		if (!frm.is_new()) return;

		// Snapshot the actual values Frappe pre-filled on THIS form the
		// moment it was created, before anything else can touch them.
		// Deliberately not re-derived later (inside customer_primary_contact
		// below) via a fresh frappe.model.get_new_doc("Customer") call: that
		// function applies the global frappe.route_options onto whatever it
		// builds, then unconditionally nulls it - and also reads
		// frappe.boot.user.last_selected_values, another live per-session
		// global. Both routinely get set by ordinary Desk flows (e.g.
		// reaching "New Customer" via another doctype's "+ Create New" on a
		// Link field), so a second, later call isn't guaranteed to reproduce
		// what this form actually started with - the "still pristine"
		// comparison would silently mismatch and these fields would never
		// live-update, even though nothing actually changed them. onload
		// fires exactly once, before refresh and before the user can
		// interact with the form, so this is the one moment guaranteed to
		// see the true as-created values.
		frm.__pristine_defaults = {};
		FIELDS_WITH_ENVIRONMENT_DEFAULTS.forEach((fieldname) => {
			frm.__pristine_defaults[fieldname] = frm.doc[fieldname];
		});

		show_mandatory_contact_dialog(frm);
	},

	refresh(frm) {
		frm.set_query("customer_primary_contact", () => ({
			query: "contact_enhancements.api.contact_lookup.search_contact_by_phone",
		}));

		// ERPNext's own native setup() handler (erpnext/selling/doctype/
		// customer/customer.js) already calls frm.set_query on this same
		// field with erpnext.buying...get_customer_primary(customer=doc.name),
		// restricted to Addresses already Dynamic-Linked to THIS Customer -
		// on a brand-new, unsaved Customer (doc.name doesn't exist yet)
		// that always returns zero results. customer_primary_address is
		// mandatory (setup/property_setters.py), so without this override a
		// Customer with no Lead/Prospect/Opportunity source to auto-backfill
		// it from (sync_customer_from_primary_contact) could never actually
		// pick one - confirmed the hard way live in the browser. Clearing
		// the filter here (refresh always runs after that native setup()
		// call) restores Frappe's own plain, unrestricted Address search.
		frm.set_query("customer_primary_address", () => ({}));

		if (frm.is_new() && !frm.doc.customer_primary_contact) {
			frm.disable_save();
		}
	},

	customer_primary_contact(frm) {
		if (frm.is_new() && frm.doc.customer_primary_contact) {
			frm.enable_save();
		}

		if (!frm.doc.customer_primary_contact) return;

		frappe.call({
			method: "contact_enhancements.api.lead_lookup.get_lead_snapshot_for_contact",
			args: { contact_name: frm.doc.customer_primary_contact },
			async callback(r) {
				const snapshot = r.message;
				if (snapshot) {
					await apply_lead_snapshot(frm, snapshot);
				}
				await apply_address_fallback(frm);
				// Last, so the Lead snapshot's own customer_name (a
				// company_name, typically) always wins - this only fills
				// customer_name if nothing else has, which for a Contact
				// with no Lead behind it was previously nothing at all,
				// leaving a mandatory field blank after picking a Contact.
				await contact_enhancements.prefill_from_contact(frm, "customer_primary_contact", {
					customer_name: "full_name",
				});
			},
			error() {
				// Still fill the name from the Contact - customer_name is
				// mandatory, and the snapshot failing shouldn't leave the
				// user unable to save.
				contact_enhancements.prefill_from_contact(frm, "customer_primary_contact", {
					customer_name: "full_name",
				});
				// Not fatal - contact_enhancements.customer_hooks
				// .sync_customer_from_primary_contact re-applies the same
				// snapshot server-side on save regardless, so this is a
				// live-preview convenience failing, not the whole flow -
				// but the user should still know why nothing changed.
				show_dialog_call_error(
					__(
						"Couldn't load this Contact's linked Lead details right now. The fields will still be filled in correctly when you save."
					)
				);
			},
		});
	},
});

async function apply_lead_snapshot(frm, snapshot) {
	// lead_name has to be set, and fully settled, BEFORE every other
	// field - not alongside them in the same loop. ERPNext's own native
	// customer.js registers frm.add_fetch("lead_name", "company_name",
	// "customer_name") (erpnext/selling/doctype/customer/customer.js).
	// The instant lead_name changes, Frappe's Link control asynchronously
	// fetches that Lead's company_name and overwrites customer_name with
	// it - blank or not. For an Individual Lead (company_name blank) that
	// native fetch silently blanks customer_name right back out -
	// confirmed by reproducing it in the browser.
	//
	// That fetch is NOT part of frm.set_value("lead_name", ...)'s own
	// returned promise - confirmed empirically (awaiting it still left
	// customer_name unset moments later) and by reading frappe/public/js/
	// frappe/form/controls/link.js's validate_link_and_fetch/
	// update_dependant_fields, which fires as a detached frappe.xcall.
	// The only reliable way to sequence after it is frappe.after_ajax(),
	// which resolves once every in-flight request - including that
	// detached one - has actually completed (confirmed empirically:
	// customer_name read back as the Lead's, correctly blank,
	// company_name exactly at that point). Only once that's settled is it
	// safe to apply the rest of the snapshot, including the correct
	// customer_name from _lead_snapshot (which already accounts for
	// Individual vs Company), so it applies last and wins.
	if (snapshot.lead_name && !frm.doc.lead_name) {
		await frm.set_value("lead_name", snapshot.lead_name);
		await frappe.after_ajax();
	}

	// image is deliberately not live-prefilled here (no visible UX value
	// for a hidden field) but still gets set server-side by
	// contact_enhancements.customer_hooks.sync_customer_from_primary_contact.
	const fields = [
		"customer_name",
		"customer_type",
		"customer_group",
		"territory",
		"salutation",
		"gender",
		"market_segment",
		"industry",
		"website",
		"language",
		"customer_primary_address",
	];
	fields.forEach((fieldname) => {
		apply_environment_default_field(frm, fieldname, snapshot[fieldname]);
	});

	frappe.show_alert({
		message: __("Prefilled from this Contact's linked Lead."),
		indicator: "green",
	});
}

async function apply_address_fallback(frm) {
	// Broader fallback, tried after apply_lead_snapshot above (whether or
	// not it actually ran, or found an address of its own) - if this
	// Contact is already linked to some other doctype this app tracks (a
	// Supplier, a Lead with no Contact-level snapshot data, or a User)
	// that already has an address, reuse it. Needed client-side, not
	// just customer_hooks.sync_customer_from_primary_contact's own
	// server-side version of this same fallback: customer_primary_address
	// is mandatory, and Frappe's own native client-side check blocks the
	// save before that server-side hook ever gets a chance to run if the
	// field is still blank at that moment (contact_enhancements/CLAUDE.md).
	if (frm.doc.customer_primary_address) return;

	// This runs on the non-cancelable mandatory-contact dialog's own flow,
	// and customer_primary_address is reqd - so a silent failure here
	// leaves the user stuck at Save with a blank mandatory field, no
	// message, and (the dialog being no_cancel) no way out. Tell them
	// instead, so they know to pick an address themselves.
	// Note frappe.call resolves with exc set rather than rejecting, so
	// both that and a genuine transport rejection have to be handled.
	let r;
	try {
		r = await frappe.call({
			method: "contact_enhancements.api.contact_lookup.get_address_from_contact_links",
			args: { contact: frm.doc.customer_primary_contact },
		});
	} catch (e) {
		r = null;
	}
	if (!r || r.exc) {
		frappe.msgprint({
			title: __("Could not look up an address"),
			message: __("Pick a Customer Primary Address manually before saving."),
			indicator: "orange",
		});
		return;
	}
	if (r.message && !frm.doc.customer_primary_address) {
		await frm.set_value("customer_primary_address", r.message);
	}
}

// Customer's own Quick Entry has no customer_primary_contact field at all
// (confirmed by driving it directly), so it's disabled entirely via a
// Property Setter on quick_entry itself (contact_enhancements/setup/
// property_setters.py) rather than a client-side override here - a
// doctype's doctype_js content only gets evaluated once a real
// frappe.ui.form.Form is constructed for it, and Quick Entry never
// constructs one until after its own dialog decision is already made, so
// there's no reliable point in this file where JS alone could intercept
// that decision in time (confirmed by testing: an is_quick_entry()
// override placed here never actually took effect).

function show_mandatory_contact_dialog(frm) {
	if (frm.doc.customer_primary_contact) return;

	// This dialog is the enforcement mechanism for the customer_primary_
	// contact reqd Property Setter (contact_enhancements/setup/
	// property_setters.py) - no_cancel: true means no closing without
	// picking or creating a Contact.
	contact_enhancements.show_contact_picker_dialog(frm, {
		primary_contact_fieldname: "customer_primary_contact",
		title: __("Link a Contact to this Customer"),
		no_cancel: true,
		extra_dialog_fields: [
			// Customer Group only applies when creating a new Contact here
			// (set on this Customer once creation succeeds, in
			// on_before_finish below) - it's never asked for on the "pick
			// an existing Contact" path.
			{
				fieldtype: "Link",
				fieldname: "customer_group",
				label: __("Customer Group"),
				options: "Customer Group",
			},
			// This pair only decides the CUSTOMER's customer_type (a native
			// ERPNext Select field, "Individual"/"Company") and, for Company,
			// the Customer's own name - it never touches the new Contact
			// itself, which is always a person with the full name above
			// regardless of which one is picked here.
			{ fieldtype: "Check", fieldname: "new_is_individual", label: __("Individual"), default: 1 },
			{ fieldtype: "Check", fieldname: "new_is_company", label: __("Company") },
			{
				fieldtype: "Data",
				fieldname: "new_company_name",
				label: __("Company Name"),
				description: __("Used as this Customer's name."),
				depends_on: "eval:doc.new_is_company",
			},
		],
		on_dialog_ready(dialog) {
			// Individual/Company mutual exclusivity, purely local to this
			// dialog - Frappe has no native mutually-exclusive-checkbox
			// fieldtype, so this is hand-built. dialog.set_value() only
			// fires change on an actual value change, so this can't recurse.
			dialog.fields_dict.new_is_individual.$input.on("change", () => {
				if (dialog.get_value("new_is_individual")) {
					dialog.set_value("new_is_company", 0);
				} else if (!dialog.get_value("new_is_company")) {
					dialog.set_value("new_is_individual", 1);
				}
			});
			dialog.fields_dict.new_is_company.$input.on("change", () => {
				if (dialog.get_value("new_is_company")) {
					dialog.set_value("new_is_individual", 0);
				} else if (!dialog.get_value("new_is_individual")) {
					dialog.set_value("new_is_company", 1);
				}
			});
		},
		validate_extra_fields(values) {
			if (values.new_is_company && !values.new_company_name) {
				return __("Enter a company name.");
			}
		},
		on_before_finish(values, contact_name) {
			frm.set_value("customer_type", values.new_is_company ? "Company" : "Individual");
			apply_environment_default_field(frm, "customer_group", values.customer_group);

			// A brand-new Contact has no Lead to prefill customer_name
			// from (unlike picking an existing one), so it would
			// otherwise stay blank - also separately mandatory on
			// Customer, so leaving it unset here would just trade one
			// required-field block for another right after closing this
			// dialog.
			const customer_name_hint = values.new_is_company ? values.new_company_name : values.new_first_name;
			if (customer_name_hint && !frm.doc.customer_name) {
				frm.set_value("customer_name", customer_name_hint);
			}
		},
	});
}
