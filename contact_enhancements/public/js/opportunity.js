// Copyright (c) 2026, Omar Sabry and contributors
// For license information, please see license.txt

frappe.provide("contact_enhancements");

frappe.ui.form.on("Opportunity", {
	onload(frm) {
		if (!frm.is_new()) return;
		show_opportunity_contact_dialog(frm);
	},

	refresh(frm) {
		frm.set_query("contact_person", () => ({
			query: "contact_enhancements.api.contact_lookup.search_contact_by_phone",
		}));

		// ERPNext restricts customer_address by default pre-save; unrestrict it
		frm.set_query("customer_address", () => ({}));

		if (frm.is_new()) {
			frm.add_custom_button(__("قائمة التعبئة السريعة"), () => {
				if (frm.doc.contact_person) {
					frm.set_value("contact_person", "");
				}
				show_opportunity_contact_dialog(frm);
			});
		}
	},

	contact_person(frm) {
		if (!frm.doc.contact_person) return;

		const company_name = frm.__ce_dialog_company_name || "";
		frappe.call({
			method: "contact_enhancements.api.opportunity_lookup.get_party_and_contact_details_for_opportunity",
			args: {
				contact_name: frm.doc.contact_person,
				company_name: company_name,
			},
			callback(r) {
				if (r && r.message) {
					apply_opportunity_details(frm, r.message);
				}
			},
		});
	},
});

function show_opportunity_contact_dialog(frm) {
	if (frm.doc.contact_person) return;

	contact_enhancements.show_contact_picker_dialog(frm, {
		primary_contact_fieldname: "contact_person",
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
			if (values && values.company_name) {
				frm.__ce_dialog_company_name = values.company_name;
			}
		},
	});
}

async function apply_opportunity_details(frm, details) {
	if (!details) return;

	// Set opportunity_from and party_name if present
	if (details.opportunity_from && details.party_name) {
		if (frm.doc.opportunity_from !== details.opportunity_from) {
			await frm.set_value("opportunity_from", details.opportunity_from);
		}
		if (frm.doc.party_name !== details.party_name) {
			await frm.set_value("party_name", details.party_name);
		}
	}

	// Sequence after native ERPNext party_name change handlers (e.g. map_current_doc)
	if (window.frappe && frappe.after_ajax) {
		await frappe.after_ajax();
	}

	const field_map = [
		["contact_person", details.contact_person],
		["customer_name", details.customer_name],
		["contact_mobile", details.contact_mobile],
		["contact_email", details.contact_email],
		["phone", details.phone],
		["whatsapp", details.whatsapp],
		["customer_address", details.customer_address],
		["address_display", details.address_display],
		["customer_group", details.customer_group],
		["territory", details.territory],
	];

	for (const [field, val] of field_map) {
		if (val && (!frm.doc[field] || field === "contact_person")) {
			await frm.set_value(field, val);
		}
	}
}
