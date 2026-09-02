// Copyright (c) 2026, Omar Sabry and contributors
// For license information, please see license.txt

// Phase 3 - proactive onload dialog, same UX as Customer/Supplier, but
// dismissible (no_cancel: false) and never blocks save:
// employee_primary_contact/employee_primary_address are both optional
// (setup/custom_fields.py) - Employee records are routinely created in
// batches by a small trusted HR group, so nothing here should obstruct
// that the way a non-cancelable dialog would.

frappe.ui.form.on("Employee", {
	onload(frm) {
		if (!frm.is_new()) return;
		show_reactive_contact_dialog(frm);
	},

	refresh(frm) {
		frm.set_query("employee_primary_contact", () => ({
			query: "contact_enhancements.api.contact_lookup.search_contact_by_phone",
		}));
	},

	employee_primary_contact(frm) {
		// Live-prefill first_name (relabeled "Full Name" - setup/
		// property_setters.py's create_employee_full_name_property_setters)
		// from the picked Contact's own full_name, only if still blank -
		// mirrors supplier.js's own supplier_primary_contact handler,
		// which fires for every path the field can end up set (this
		// dialog's own search, the native Link dropdown directly, or
		// creating a new one), not just the dialog's create-new path.
		if (frm.doc.employee_primary_contact && !frm.doc.first_name) {
			frappe.db.get_value("Contact", frm.doc.employee_primary_contact, "full_name").then(({ message }) => {
				if (message && message.full_name && !frm.doc.first_name) {
					frm.set_value("first_name", message.full_name);
				}
			});
		}

		apply_address_fallback(frm);
	},
});

async function apply_address_fallback(frm) {
	// Same cross-doctype address backfill employee_hooks.sync_employee_
	// address_from_contact_links already applies server-side, done
	// proactively client-side too so it's visible before Save - the same
	// "sync before save" guarantee this app's other doctypes apply for
	// their own address fields.
	if (!frm.doc.employee_primary_contact || frm.doc.employee_primary_address) return;

	const r = await frappe.call({
		method: "contact_enhancements.api.contact_lookup.get_address_from_contact_links",
		args: { contact: frm.doc.employee_primary_contact, exclude_doctype: "Employee", exclude_name: frm.doc.name },
	});
	if (r.message && !frm.doc.employee_primary_address) {
		await frm.set_value("employee_primary_address", r.message);
	}
}

function show_reactive_contact_dialog(frm) {
	if (frm.doc.employee_primary_contact) return;

	contact_enhancements.show_contact_picker_dialog(frm, {
		primary_contact_fieldname: "employee_primary_contact",
		title: __("Link a Contact to this Employee"),
		no_cancel: false,
		// Gender/Date of Birth/Date of Joining are all natively reqd=1 on
		// Employee (confirmed via frappe.get_meta) - captured here too, in
		// the same window, so filling them in doesn't need a separate trip
		// to the main form. Not marked reqd on these dialog fields
		// themselves (same reasoning as User's own Email field): native
		// mandatory enforcement on the main form is still the real
		// backstop if left blank here.
		extra_dialog_fields: [
			{ fieldtype: "Link", fieldname: "gender", label: __("Gender"), options: "Gender" },
			{ fieldtype: "Date", fieldname: "date_of_birth", label: __("Date of Birth") },
			{ fieldtype: "Date", fieldname: "date_of_joining", label: __("Date of Joining") },
		],
		on_before_finish(values) {
			if (values.gender && !frm.doc.gender) {
				frm.set_value("gender", values.gender);
			}
			if (values.date_of_birth && !frm.doc.date_of_birth) {
				frm.set_value("date_of_birth", values.date_of_birth);
			}
			if (values.date_of_joining && !frm.doc.date_of_joining) {
				frm.set_value("date_of_joining", values.date_of_joining);
			}
		},
	});
}
