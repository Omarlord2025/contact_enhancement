// Copyright (c) 2026, Omar Sabry and contributors
// For license information, please see license.txt

frappe.provide("contact_enhancements");

frappe.ui.form.on("Lead", {
	onload(frm) {
		if (!frm.is_new()) {
			frm.__ce_original_party_name = frm.doc.first_name;
			return;
		}
		show_lead_contact_dialog(frm);
	},

	refresh(frm) {
		if (!frm.is_new() && !frm.is_dirty()) {
			frm.__ce_original_party_name = frm.doc.first_name;
		}

		frm.set_query("lead_primary_contact", () => ({
			query: "contact_enhancements.api.contact_lookup.search_contact_by_phone",
		}));

		if (frm.is_new()) {
			frm.add_custom_button(__("قائمة التعبئة السريعة"), () => {
				show_lead_contact_dialog(frm);
			});
		}
	},

	lead_primary_contact(frm) {
		if (!frm.doc.lead_primary_contact) return;

		frappe.call({
			method: "contact_enhancements.api.lead_lookup.get_contact_snapshot_for_lead",
			args: { contact_name: frm.doc.lead_primary_contact },
			callback(r) {
				if (r && r.message) {
					apply_lead_contact_snapshot(frm, r.message);
				}
			},
		});
	},

	before_save(frm) {
		// Offer to propagate a Lead name change to linked Contacts & linked records before saving.
		const config = {
			doctype: "Lead",
			name_field: "first_name",
			type_field: null,
			primary_contact_field: "lead_primary_contact",
		};
		if (window.contact_enhancements && contact_enhancements.maybe_show_name_sync_dialog) {
			return contact_enhancements.maybe_show_name_sync_dialog(frm, config);
		} else {
			return new Promise((resolve) => {
				frappe.require("/assets/contact_enhancements/js/name_sync_dialog.js", () => {
					contact_enhancements.maybe_show_name_sync_dialog(frm, config).then(resolve).catch(resolve);
				});
			});
		}
	},
});

function apply_lead_contact_snapshot(frm, snapshot) {
	const field_map = [
		"first_name",
		"last_name",
		"lead_name",
		"company_name",
		"mobile_no",
		"phone",
		"whatsapp_no",
		"email_id",
		"country",
		"gender",
		"salutation",
		"job_title",
	];

	field_map.forEach((field) => {
		if (snapshot[field]) {
			frm.set_value(field, snapshot[field]);
		}
	});

	if (snapshot.address) {
		frappe.show_alert({
			message: __("تم العثور على عنوان مرتبط بهذا جهة الاتصال وسيتم ربطه تلقائياً."),
			indicator: "green",
		});
	}
}

function show_lead_contact_dialog(frm) {
	if (frm.doc.lead_primary_contact) return;

	contact_enhancements.show_contact_picker_dialog(frm, {
		primary_contact_fieldname: "lead_primary_contact",
		title: __("قائمة التعبئة السريعة"),
		no_cancel: false,
		extra_dialog_fields: [
			{
				fieldtype: "Data",
				fieldname: "company_name",
				label: __("Organization Name"),
				placeholder: __("ادخل اسم المؤسسة / الشركة"),
			},
		],
		on_before_finish(values, contact_name) {
			if (values.company_name && !frm.doc.company_name) {
				frm.set_value("company_name", values.company_name);
			}
		},
	});
}

