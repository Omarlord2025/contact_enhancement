// Copyright (c) 2026, Omar Sabry and contributors
// For license information, please see license.txt

// Two separate features on this one file:
//
// 1. user_primary_contact (setup/custom_fields.py) - a proactive
//    onboarding dialog shown on a new User, reusing the same shared
//    contact_picker_dialog.js every other doctype in this app uses, with
//    two User-specific extra fields (Email, Role Profile) appended after
//    the dialog's own "Full Name" field.
//
//    Deliberately dismissible (no_cancel: false), NOT the non-cancelable
//    posture Customer/Supplier use, and user_primary_contact itself is
//    NOT made reqd via a Property Setter - unlike those, User is reused
//    by native flows this app doesn't control and that never go through
//    Desk's own "+ New User" form at all (e.g. Employee's own native
//    "Create User" button inserts a User document directly, server-side -
//    confirmed by reading erpnext/setup/doctype/employee/employee.py).
//    Forcing this field would repeat the exact Contact.country regression
//    already documented in contact_enhancements/CLAUDE.md - a hard-
//    mandatory field on a widely-shared core doctype breaking a native
//    flow that's never heard of it.
//
// 2. "A User can have multiple linked Addresses" - the user's own chosen
//    design: reuse Address's existing Dynamic Link mechanism directly
//    (the same "links" table every other doctype in this app already
//    Dynamic-Links a Contact/Address through), rendered as a custom list
//    in a new HTML field with an "Add Address" button, rather than a new
//    child doctype or a plain Link field (which could only ever hold
//    one).

frappe.ui.form.on("User", {
	onload(frm) {
		if (!frm.is_new()) return;
		show_user_onboarding_dialog(frm);
	},

	refresh(frm) {
		render_linked_addresses(frm);
	},

	user_primary_contact(frm) {
		// Live, not just on save/reload: the moment a Contact is picked
		// or changed, immediately re-fetch and re-render - the whole
		// point of this being a "live" section rather than a one-time
		// seeded copy. Fires for every path user_primary_contact can end
		// up set through (the onboarding dialog's own finish(), or a
		// direct pick via the native Link dropdown).
		render_linked_addresses(frm);
	},
});

function show_user_onboarding_dialog(frm) {
	if (frm.doc.user_primary_contact) return;

	contact_enhancements.show_contact_picker_dialog(frm, {
		primary_contact_fieldname: "user_primary_contact",
		title: __("Set Up this User"),
		no_cancel: false,
		extra_dialog_fields: [
			{
				// Deliberately not reqd: 1, and validate_extra_fields below
				// deliberately never blocks on this being blank either - see
				// on_before_finish's own comment for why: a picked EXISTING
				// Contact's own email_id (if it has one) is a valid source
				// too, "sync his email if found" per explicit product
				// direction, and that fallback can only run once finish()
				// itself is allowed to proceed. If neither this field nor
				// the Contact supplies one, User's own native email reqd
				// check is the final backstop at save time.
				fieldtype: "Data",
				fieldname: "email",
				label: __("Email"),
				description: __("This User's own login email - left blank to reuse the picked Contact's own email, if it has one."),
			},
			{
				fieldtype: "Link",
				fieldname: "role_profile",
				label: __("Role Profile"),
				options: "Role Profile",
			},
		],
		on_before_finish(values, contact_name) {
			// Fires on either path - a picked existing Contact or a newly
			// created one (contact_picker_dialog.js's own finish()) -
			// role_profile is this User's own native field, applies
			// regardless of which Contact ends up linked.
			if (values.role_profile && !frm.doc.role_profile_name) {
				frm.set_value("role_profile_name", values.role_profile);
			}

			// User's own native first_name is mandatory and separate from
			// the Contact's own name field - confirmed live it's never
			// otherwise filled in, leaving a new User unsavable. email is
			// this User's own native naming field. Both need a fallback to
			// the Contact's own data when the dialog's own fields
			// (new_first_name on the create-new path, email on either
			// path) don't already supply them - confirmed live: without
			// this, picking an EXISTING Contact synced neither, since
			// nothing else in this dialog ever reads that Contact's own
			// full_name/email_id.
			const need_name = !frm.doc.first_name;
			const need_email = !frm.doc.email;

			if (values.new_first_name && need_name) {
				frm.set_value("first_name", values.new_first_name);
			}
			if (values.email && need_email) {
				frm.set_value("email", values.email);
			}

			if ((need_name && !values.new_first_name) || (need_email && !values.email)) {
				frappe.db
					.get_value("Contact", contact_name, ["full_name", "email_id"])
					.then(({ message }) => {
						if (!message) return;
						if (need_name && !values.new_first_name && !frm.doc.first_name && message.full_name) {
							frm.set_value("first_name", message.full_name);
						}
						if (need_email && !values.email && !frm.doc.email && message.email_id) {
							frm.set_value("email", message.email_id);
						}
					})
					// This is the ONLY thing filling first_name/email on the
					// pick-an-existing-Contact path, and both are mandatory
					// on User - a silent failure here leaves the form
					// unsavable with no explanation of what's missing.
					.catch(() => {
						frappe.msgprint({
							title: __("Could not read that Contact"),
							message: __("Enter the Full Name and Email for this user manually."),
							indicator: "orange",
						});
					});
			}
		},
	});
}

function render_linked_addresses(frm) {
	const field = frm.get_field("linked_addresses_html");
	if (!field) return;

	// "Add Address" needs a real, saved User to Dynamic-Link against -
	// a brand-new form's own frm.doc.name isn't a persisted document
	// name yet. The live *view* itself still works before save (it only
	// needs a Contact, not a saved User - see get_all_addresses_for_user's
	// own docstring), so only the add flow is gated on is_new(), not the
	// whole section.
	// Rebuild the whole component only when there isn't one yet, when the
	// is_new() state it was built for has changed (that decides whether
	// the "Add Address" button exists at all), or when its DOM was
	// discarded by a form re-render. Otherwise reuse the handle and just
	// re-fetch in place: refresh(frm) fires on load, on every save and on
	// every route back to the form, and re-invoking the component each
	// time empties the wrapper and repaints from scratch. Reusing it also
	// fixes a real bug - a rebuild while the Add Address dialog is open
	// detached the list its on_done callback still pointed at, so the
	// newly linked address never appeared.
	const is_new = !!frm.is_new();
	const existing = frm.__ce_linked_addresses;
	const still_attached = existing && $.contains(document, existing.wrapper);
	if (existing && existing.is_new === is_new && still_attached) {
		existing.handle.refresh();
		return;
	}

	const handle = contact_enhancements.render_linked_addresses(field, {
		fetch: () => {
			// A brand-new User with no Contact picked yet has nothing the
			// server could possibly find - don't ask it.
			if (frm.is_new() && !frm.doc.user_primary_contact) return Promise.resolve([]);
			return frappe
				.call({
					method: "contact_enhancements.api.user_addresses.get_all_addresses_for_user",
					args: { user: frm.is_new() ? null : frm.doc.name, contact: frm.doc.user_primary_contact },
				})
				.then((r) => {
					// frappe.call resolves (with exc set) rather than
					// rejecting on a server error, so a try/catch around
					// it never fires - surface it as a rejection so the
					// component shows its own error state.
					if (r.exc) throw new Error(r.exc);
					return r.message;
				});
		},
		on_add: is_new ? null : () => show_add_address_dialog(frm, () => handle.refresh()),
		on_remove: (address) =>
			frappe
				.call({
					method: "contact_enhancements.api.user_addresses.unlink_address_from_user",
					args: { address, user: frm.doc.name },
					error: () => frappe.msgprint(__("Could not remove that address. Please try again.")),
				})
				.then((r) => {
					if (r.exc) throw new Error(r.exc);
					return r.message;
				}),
		empty_message: __("No addresses yet - link one directly, or pick a Contact that already has one via a Customer, Supplier, Employee, or Lead."),
	});
	frm.__ce_linked_addresses = { handle, is_new, wrapper: field.$wrapper[0] };
}

// Same data-entry window pattern as contact_picker_dialog.js - one dialog,
// search-an-existing-record on top, create-a-new-one below, single
// primary action branching on which path was actually filled in.
function show_add_address_dialog(frm, on_done) {
	const dialog = new frappe.ui.Dialog({
		title: __("Link an Address to this User"),
		fields: [
			{
				fieldtype: "Link",
				fieldname: "existing_address",
				label: __("Search for an Existing Address"),
				options: "Address",
				get_query: () => ({
					query: "contact_enhancements.api.user_addresses.search_addresses_for_user",
				}),
			},
			{ fieldtype: "Section Break", label: __("Or Create a New Address") },
			{ fieldtype: "Data", fieldname: "address_title", label: __("Address Title") },
			{
				fieldtype: "Select",
				fieldname: "address_type",
				label: __("Address Type"),
				options:
					"Billing\nShipping\nOffice\nPersonal\nPlant\nPostal\nShop\nSubsidiary\nWarehouse\nCurrent\nPermanent\nOther",
				default: "Personal",
			},
			{ fieldtype: "Column Break" },
			{ fieldtype: "Data", fieldname: "address_line1", label: __("Address Line 1") },
			{ fieldtype: "Data", fieldname: "city", label: __("City") },
			{
				fieldtype: "Link",
				fieldname: "country",
				label: __("Country"),
				options: "Country",
				default: "Egypt",
			},
		],
		primary_action_label: __("Link"),
		primary_action(values) {
			if (values.existing_address) {
				frappe.call({
					method: "contact_enhancements.api.user_addresses.link_existing_address_to_user",
					args: { address: values.existing_address, user: frm.doc.name },
					callback() {
						dialog.hide();
						on_done();
					},
					error() {
						show_dialog_call_error(__("Couldn't link that Address right now. Please try again."));
					},
				});
				return;
			}

			if (!values.address_title || !values.address_line1 || !values.city || !values.country) {
				frappe.msgprint(
					__(
						"Enter an Address Title, Address Line 1, City, and Country to create a new Address, or search for an existing one above."
					)
				);
				return;
			}

			frappe.call({
				method: "contact_enhancements.api.user_addresses.create_and_link_address",
				args: {
					user: frm.doc.name,
					address_title: values.address_title,
					address_type: values.address_type,
					address_line1: values.address_line1,
					city: values.city,
					country: values.country,
				},
				callback() {
					dialog.hide();
					on_done();
				},
				error() {
					show_dialog_call_error(__("Couldn't create that Address right now. Please try again."));
				},
			});
		},
	});
	dialog.show();
}

function show_dialog_call_error(message) {
	frappe.msgprint({ title: __("Something Went Wrong"), message, indicator: "red" });
}
