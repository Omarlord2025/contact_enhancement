// Copyright (c) 2026, Omar Sabry and contributors
// For license information, please see license.txt

// Phase 1 - the closest analog to customer.js, since Supplier already has
// the same supplier_primary_contact Link-field shape Customer does.
// Mirrors customer.js's own onload/refresh/disable-save posture exactly;
// see that file's own comments for the reasoning behind each piece this
// repeats (Quick Entry disabled via Property Setter not JS, forced-onload
// dialog, disable_save() until a primary contact exists).

frappe.ui.form.on("Supplier", {
	onload(frm) {
		if (!frm.is_new()) return;
		show_mandatory_contact_dialog(frm);
	},

	refresh(frm) {
		frm.set_query("supplier_primary_contact", () => ({
			query: "contact_enhancements.api.contact_lookup.search_contact_by_phone",
		}));

		// Same fix as customer.js's own refresh handler - ERPNext's native
		// setup() (erpnext/buying/doctype/supplier/supplier.js) restricts
		// this field to Addresses already Dynamic-Linked to THIS Supplier
		// (supplier=doc.name), which is always empty pre-save. Unlike
		// Customer, supplier_primary_address stays optional here, but it
		// still needs to be genuinely pickable - confirmed live that it
		// wasn't.
		frm.set_query("supplier_primary_address", () => ({}));

		if (frm.is_new() && !frm.doc.supplier_primary_contact) {
			frm.disable_save();
		}
	},

	supplier_primary_contact(frm) {
		if (frm.is_new() && frm.doc.supplier_primary_contact) {
			frm.enable_save();
		}

		// Live-prefill supplier_name from the picked Contact's own full
		// name, only if still blank - mirrors customer.js's own
		// customer_primary_contact field handler, which always reacts to
		// the field changing (a live server call to fetch/apply data),
		// regardless of *how* it was set: picking an existing Contact via
		// this dialog's own search, picking one via the native Link
		// field's dropdown directly, or creating a new one. Supplier has
		// no Lead-equivalent source to pull a fuller snapshot from the
		// way Customer's own get_lead_snapshot_for_contact does, but the
		// same "don't leave a newly-linked Contact's own name unused"
		// reasoning still applies - this is what was missing before: only
		// the dialog's own "Create New Contact" path filled in
		// supplier_name, never the "pick an existing Contact" path.
		if (frm.doc.supplier_primary_contact && !frm.doc.supplier_name) {
			frappe.db
				.get_value("Contact", frm.doc.supplier_primary_contact, "full_name")
				.then(({ message }) => {
					if (message && message.full_name && !frm.doc.supplier_name) {
						frm.set_value("supplier_name", message.full_name);
					}
				})
				// supplier_name is editable and this is only a convenience
				// prefill, so a failure shouldn't interrupt anyone - but it
				// must not surface as an unhandled rejection either.
				.catch(() => {});
		}

		apply_address_fallback(frm);
	},
});

async function apply_address_fallback(frm) {
	// Same cross-doctype address backfill supplier_hooks.sync_supplier_
	// address_from_contact_links already applies server-side, done
	// proactively client-side too so it's visible before Save rather than
	// only appearing after - the same "sync before save" guarantee
	// customer.js applies for its own mandatory address field, kept
	// consistent here even though
	// supplier_primary_address itself stays optional.
	if (!frm.doc.supplier_primary_contact || frm.doc.supplier_primary_address) return;

	// supplier_primary_address is optional (unlike Customer's), so a
	// failed lookup is a missed convenience, not a blocked save - stay
	// quiet rather than interrupting, but don't leave the promise
	// rejecting unhandled. frappe.call resolves with exc set instead of
	// rejecting, so check both.
	let r;
	try {
		r = await frappe.call({
			method: "contact_enhancements.api.contact_lookup.get_address_from_contact_links",
			args: { contact: frm.doc.supplier_primary_contact, exclude_doctype: "Supplier", exclude_name: frm.doc.name },
		});
	} catch (e) {
		return;
	}
	if (!r || r.exc) return;
	if (r.message && !frm.doc.supplier_primary_address) {
		await frm.set_value("supplier_primary_address", r.message);
	}
}

function show_mandatory_contact_dialog(frm) {
	if (frm.doc.supplier_primary_contact) return;

	// This dialog is the enforcement mechanism for the
	// supplier_primary_contact reqd Property Setter (contact_enhancements/
	// setup/property_setters.py) - no_cancel: true means no closing
	// without picking or creating a Contact.
	contact_enhancements.show_contact_picker_dialog(frm, {
		primary_contact_fieldname: "supplier_primary_contact",
		title: __("Link a Contact to this Supplier"),
		no_cancel: true,
		extra_dialog_fields: [
			// A plain Select bound to Supplier's own 3 native options -
			// not a hand-rolled checkbox pair like Customer's Individual/
			// Company toggle. Customer's own customer_type is a 2-option
			// field (Individual/Company); supplier_type already has 3
			// (Company/Individual/Partnership, confirmed by reading
			// supplier.json directly), so a binary toggle would be the
			// wrong UI here.
			{
				fieldtype: "Select",
				fieldname: "supplier_type",
				label: __("Supplier Type"),
				options: "Company\nIndividual\nPartnership",
				default: "Company",
				reqd: 1,
			},
			// Native Supplier.supplier_group is already mandatory on the
			// form itself - added here too so it can be set without ever
			// leaving this dialog, the same way Customer's own dialog
			// already captures customer_group.
			{
				fieldtype: "Link",
				fieldname: "supplier_group",
				label: __("Supplier Group"),
				options: "Supplier Group",
			},
		],
		on_before_finish(values) {
			frm.set_value("supplier_type", values.supplier_type);
			if (values.supplier_group && !frm.doc.supplier_group) {
				frm.set_value("supplier_group", values.supplier_group);
			}
			// supplier_name itself is prefilled by the supplier_primary_
			// contact field handler above, which fires for this path too
			// (frm.set_value below triggers it) - no need to duplicate
			// that logic here.
		},
	});
}
