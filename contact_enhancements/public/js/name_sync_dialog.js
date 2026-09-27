// Copyright (c) 2026, Omar Sabry and contributors
// For license information, please see license.txt

// نافذة مزامنة الأسماء ثنائية الاتجاه (الطرف ← جهة الاتصال والسجلات المرتبطة).
// تظهر قبل حفظ العميل أو المورد عند تغيير الاسم الفعلي وتوفر جهات اتصال مرتبطة.

frappe.provide("contact_enhancements");

const DOCTYPE_AR = {
	Customer: "العميل",
	Supplier: "المورد",
	Employee: "الموظف",
	User: "المستخدم",
	Contact: "جهة الاتصال",
	Lead: "العميل المحتمل",
	Address: "العنوان",
};

const LINK_DOCTYPE_AR = {
	Customer: "عميل",
	Supplier: "مورد",
	Employee: "موظف",
	User: "مستخدم",
	Lead: "عميل محتمل",
};

const ADDRESS_TYPE_AR = {
	Billing: "فاتورة",
	Shipping: "شحن",
	Office: "مكتب",
	Personal: "شخصي",
	Plant: "مصنع",
	Postal: "بريدي",
	Shop: "متجر",
	Subsidiary: "فرعي",
	Warehouse: "مستودع",
	Current: "حالي",
	Permanent: "دائم",
	Other: "أخرى",
};

/**
 * التحقق مما إذا كان اسم الطرف قد تغير، وعرض نافذة المزامنة قبل الحفظ.
 * يتم استدعاؤها من قبل قبل_الحفظ (before_save) لكل نوع مستند.
 * تُرجع Promise يؤجل الحفظ حتى يتخذ المستخدم قراره.
 */
contact_enhancements.maybe_show_name_sync_dialog = function (frm, config) {
	if (frm.is_new()) return Promise.resolve();

	const old_name = frm.__ce_original_party_name;
	const new_name = frm.doc[config.name_field];

	// تجاهل التغييرات في المسافات فقط
	if (!old_name || !new_name || old_name.trim() === new_name.trim()) {
		return Promise.resolve();
	}

	return new Promise((resolve, reject) => {
		frappe.call({
			method: "contact_enhancements.api.name_sync.get_linked_contacts_for_name_sync",
			args: { doctype: config.doctype, docname: frm.doc.name },
			callback(r) {
				if (!r.message || !r.message.length) {
					return resolve();
				}
				contact_enhancements._show_name_sync_dialog(
					old_name.trim(),
					new_name.trim(),
					r.message,
					config,
					frm,
					resolve,
					reject
				);
			},
			error() {
				// في حال فشل الاستدعاء لا نوقف حفظ النموذج
				resolve();
			},
		});
	});
};

// ---------------------------------------------------------------------------
// بناء النافذة
// ---------------------------------------------------------------------------

contact_enhancements._show_name_sync_dialog = function (
	old_name,
	new_name,
	contacts,
	config,
	frm,
	resolve,
	reject
) {
	const esc = frappe.utils.escape_html;
	const party_type = config.type_field ? (frm.doc[config.type_field] || "") : "Individual";
	const is_individual = config.doctype === "Contact" || config.doctype === "Employee" || party_type === "Individual";
	const doctype_ar = DOCTYPE_AR[config.doctype] || config.doctype;

	let is_settled = false;

	const source_address = (contacts[0] && contacts[0].source_address) || "";
	const source_address_title = (contacts[0] && contacts[0].source_address_title) || source_address;
	const linked_addresses = (contacts[0] && contacts[0].linked_addresses) || [];

	const fields = [
		{
			// ملخص الاسم القديم والجديد
			fieldtype: "HTML",
			fieldname: "change_summary",
			options: `
				<div style="margin-bottom:14px;padding:10px 12px;
				            background:var(--subtle-bg);border-radius:var(--border-radius);
				            font-size:var(--text-base);direction:rtl;text-align:right;">
					<span class="text-muted">${esc(doctype_ar)}:</span>
					<span style="text-decoration:line-through;margin:0 8px;
					             color:var(--text-muted);">${esc(old_name)}</span>
					<span style="color:var(--text-muted);">&#8594;</span>
					<span style="margin-right:8px;font-weight:600;">${esc(new_name)}</span>
				</div>`,
		},
		{
			// حقل الاسم الجديد القابل للتعديل
			fieldtype: "Data",
			fieldname: "new_name",
			label: __("الاسم الجديد لكتابته في جهات الاتصال والسجلات المرتبطة"),
			default: new_name,
			description: __(
				"تمت التعبئة مسبقاً من اسم {0} الجديد — يمكنك تعديله قبل الحفظ إذا لزم الأمر.",
				[doctype_ar]
			),
			reqd: 1,
		},
	];

	if (source_address) {
		fields.push({
			fieldtype: "Check",
			fieldname: "sync_address",
			label: __("مزامنة العنوان مع السجلات المحددة التي لا تملك عنواناً ({0})", [esc(source_address_title)]),
			default: 1,
		});
	}

	if (!is_individual) {
		fields.push({
			fieldtype: "HTML",
			fieldname: "company_note",
			options: `<div class="alert" style="background:var(--yellow-highlight);
				          border-right:3px solid var(--yellow-500);
				          padding:8px 12px;margin:0 0 12px;border-radius:var(--border-radius);
				          font-size:var(--text-sm);direction:rtl;text-align:right;">
					<b>سجل شركة / شراكة</b> &mdash;
					المزامنة التلقائية للأسماء لا تنطبق على سجلات الشركات. أي تحديد هنا يُعتبر تعديلاً يدوياً مقصوداً.
				</div>`,
		});
	}

	fields.push({
		fieldtype: "HTML",
		fieldname: "contacts_list",
	});

	// حقل منفصل لقسم العناوين المرتبطة (يُملأ بعد عرض النافذة)
	fields.push({
		fieldtype: "HTML",
		fieldname: "addresses_list",
	});

	const dialog = new frappe.ui.Dialog({
		title: __("تحديث جهات الاتصال والسجلات المرتبطة؟"),
		fields: fields,
		primary_action_label: __("تحديث وحفظ"),
		secondary_action_label: __("حفظ {0} فقط", [doctype_ar]),
		secondary_action() {
			is_settled = true;
			frm.__ce_original_party_name = frm.doc[config.name_field];
			if (config.doctype === "Contact") {
				frm.doc.__ce_name_synced = 1;
			}
			dialog.hide();
			resolve();
		},
		primary_action(values) {
			_do_update(dialog, values, contacts, frm, config, () => {
				is_settled = true;
				frm.__ce_original_party_name = (values.new_name || "").trim() || frm.doc[config.name_field];
				resolve();
			});
		},
	});

	// زر إلغاء لإلغاء عملية الحفظ كلياً
	if (dialog.add_custom_action) {
		dialog.add_custom_action(__("إلغاء"), () => {
			dialog.hide();
		});
	}

	dialog.onhide = function () {
		if (!is_settled) {
			// إيقاف عملية الحفظ
			frappe.validated = false;
			reject();
		}
	};

	_render_contacts(dialog, contacts, is_individual);
	_render_addresses(dialog, linked_addresses, old_name, new_name);
	dialog.show();
};

// ---------------------------------------------------------------------------
// عرض جهات الاتصال والسجلات المرتبطة
// ---------------------------------------------------------------------------

function _render_contacts(dialog, contacts, is_individual) {
	const $wrapper = dialog.fields_dict.contacts_list.$wrapper;
	const real_contacts = (contacts || []).filter((c) => c && c.contact);

	if (!real_contacts.length) {
		$wrapper.empty();
		return;
	}

	const rows = real_contacts.map((c) => _render_contact_row(c, is_individual)).join("");

	$wrapper.html(`
		<div style="margin-top:4px;direction:rtl;text-align:right;">
			<label style="font-size:var(--text-sm);color:var(--text-muted);
			              font-weight:600;text-transform:uppercase;letter-spacing:0.04em;">
				جهات الاتصال والسجلات المرتبطة بهذا السجل
			</label>
			<div class="name-sync-contacts"
			     style="margin-top:6px;border:1px solid var(--border-color);
			            border-radius:var(--border-radius);overflow:hidden;">
				${rows}
			</div>
		</div>
	`);

	// عند تغيير تحديد جهة الاتصال الرئيسية يتم تفعيل أو تعطيل السجلات التابعة لها
	$wrapper.on("change", ".name-sync-contact-checkbox", function () {
		const contact = $(this).data("contact");
		const is_checked = $(this).is(":checked");
		$wrapper.find(`.name-sync-link-checkbox[data-parent-contact="${contact}"]`).each(function () {
			if (!$(this).data("no-perm")) {
				$(this).prop("disabled", !is_checked);
				$(this).prop("checked", is_checked);
				$(this).css("cursor", is_checked ? "pointer" : "not-allowed");
			}
		});
	});
}

function _render_contact_row(c, is_individual) {
	const esc = frappe.utils.escape_html;

	const checked = is_individual && c.is_primary && c.can_edit ? "checked" : "";
	const disabled = !c.can_edit ? "disabled" : "";
	const row_style = !c.can_edit ? "opacity:0.55;" : "";
	const cursor = !c.can_edit ? "not-allowed" : "pointer";

	const primary_badge = c.is_primary
		? `<span style="display:inline-block;margin-right:6px;padding:1px 6px;
		              font-size:10px;font-weight:600;border-radius:8px;
		              background:var(--blue-100);color:var(--blue-700);"
		         >جهة اتصال أساسية</span>`
		: "";

	const links_html = _render_links(c.links, c.contact, !checked);

	const no_perm_note = !c.can_edit
		? `<div style="margin-top:4px;font-size:var(--text-xs);color:var(--red-500);">
				ليس لديك صلاحية لتعديل جهة الاتصال هذه.
		   </div>`
		: "";

	return `
		<div class="name-sync-contact-row"
		     style="padding:10px 14px;border-bottom:1px solid var(--border-color);${row_style}">
			<div style="display:flex;align-items:center;">
				<input type="checkbox"
				       class="name-sync-contact-checkbox"
				       data-contact="${esc(c.contact)}"
				       ${checked} ${disabled}
				       style="width:14px;height:14px;cursor:${cursor};margin-left:10px;">
				<div style="font-weight:600;">
					${esc(c.full_name || c.contact)}${primary_badge}
				</div>
			</div>
			${links_html}
			${no_perm_note}
		</div>`;
}

function _render_links(links, parent_contact, parent_unchecked) {
	const esc = frappe.utils.escape_html;

	if (!links || !links.length) {
		return `<div style="margin-right:24px;margin-top:4px;font-size:var(--text-xs);color:var(--text-muted);">
					لا توجد سجلات مرتبطة أخرى.
				</div>`;
	}

	const items = links
		.map((link) => {
			const dt_slug = link.doctype.toLowerCase().replace(/ /g, "-");
			const can_edit = link.can_edit !== false;
			const is_ind = link.is_individual;
			// التحديد الافتراضي يكون نشطاً إذا كان السجل فرداً ويمتلك صلاحية وتحديد جهة الاتصال مفعل
			const link_checked = is_ind && can_edit && !parent_unchecked ? "checked" : "";
			const link_disabled = !can_edit || parent_unchecked ? "disabled" : "";
			const cursor = !can_edit || parent_unchecked ? "not-allowed" : "pointer";

			const dt_label = LINK_DOCTYPE_AR[link.doctype] || link.doctype;
			const type_label = link.doctype === "Customer" || link.doctype === "Supplier"
				? (is_ind ? " (فرد)" : " (شركة)")
				: "";

			const no_perm = !can_edit
				? ` <span class="text-muted" style="font-size:10px;color:var(--red-500);">(لا توجد صلاحية)</span>`
				: "";

			return `
				<div class="name-sync-link-item" style="display:flex;align-items:center;margin:4px 0;">
					<input type="checkbox"
					       class="name-sync-link-checkbox"
					       data-parent-contact="${esc(parent_contact)}"
					       data-doctype="${esc(link.doctype)}"
					       data-name="${esc(link.name)}"
					       data-no-perm="${!can_edit ? '1' : '0'}"
					       ${link_checked} ${link_disabled}
					       style="width:13px;height:13px;cursor:${cursor};margin-left:8px;">
					<span style="font-size:var(--text-xs);">
						<span class="text-muted">${esc(dt_label)}:</span>
						<a href="/app/${esc(dt_slug)}/${encodeURIComponent(link.name)}"
						   target="_blank" rel="noopener"
						   style="font-weight:500;text-decoration:none;margin-right:2px;">${esc(link.title || link.name)}</a>
						<span class="text-muted" style="font-size:11px;">${type_label}</span>
						${no_perm}
					</span>
				</div>`;
		})
		.join("");

	return `
		<div class="name-sync-links-section" style="margin-right:24px;margin-top:6px;">
			<div style="font-size:11px;font-weight:600;color:var(--text-muted);margin-bottom:2px;">
				تحديث السجلات المرتبطة أيضاً:
			</div>
			${items}
		</div>`;
}

// ---------------------------------------------------------------------------
// عرض العناوين المرتبطة
// ---------------------------------------------------------------------------

function _render_addresses(dialog, linked_addresses, old_name, new_name) {
	const $wrapper = dialog.fields_dict.addresses_list.$wrapper;

	if (!linked_addresses || !linked_addresses.length) {
		$wrapper.empty();
		return;
	}

	const esc = frappe.utils.escape_html;
	const rows = linked_addresses
		.map((addr) => {
			const title = addr.address_title || addr.name;
			const details = [addr.address_line1, addr.city].filter(Boolean).join(" - ");
			const type_label = ADDRESS_TYPE_AR[addr.address_type] || addr.address_type || "";
			const type_badge = type_label
				? `<span style="display:inline-block;margin-right:6px;padding:1px 6px;
				              font-size:10px;font-weight:600;border-radius:8px;
				              background:var(--gray-100);color:var(--gray-700);"
				         >${esc(type_label)}</span>`
				: "";

			return `
				<div class="name-sync-address-row"
				     style="padding:10px 14px;border-bottom:1px solid var(--border-color);display:flex;align-items:center;">
					<input type="checkbox"
					       class="name-sync-address-checkbox"
					       data-address="${esc(addr.name)}"
					       checked
					       style="width:14px;height:14px;cursor:pointer;margin-left:10px;">
					<div style="flex:1;">
						<div style="font-weight:600;font-size:var(--text-sm);">
							<span class="text-muted" style="font-size:var(--text-xs);margin-left:4px;">${esc(__("العنوان"))}:</span>
							<a href="/app/address/${encodeURIComponent(addr.name)}"
							   target="_blank" rel="noopener"
							   style="text-decoration:none;font-weight:600;">${esc(title)}</a>
							${type_badge}
							<span style="color:var(--text-muted);margin:0 6px;">&#8594;</span>
							<span class="name-sync-address-new-name" style="font-weight:600;color:var(--primary-color);">${esc(new_name)}</span>
						</div>
						${details ? `<div style="font-size:var(--text-xs);color:var(--text-muted);margin-top:2px;">${esc(details)}</div>` : ""}
					</div>
				</div>`;
		})
		.join("");

	$wrapper.html(`
		<div style="margin-top:12px;direction:rtl;text-align:right;">
			<label style="font-size:var(--text-sm);color:var(--text-muted);
			              font-weight:600;text-transform:uppercase;letter-spacing:0.04em;">
				${esc(__("العناوين المرتبطة بهذا السجل (تحديث عنوان العنوان / Address Title)"))}
			</label>
			<div class="name-sync-addresses"
			     style="margin-top:6px;border:1px solid var(--border-color);
			            border-radius:var(--border-radius);overflow:hidden;">
				${rows}
			</div>
		</div>
	`);

	// تحديث الاسم الجديد المعروض بجانب العناوين في حال عدّل المستخدم حقل new_name
	dialog.fields_dict.new_name.$input.on("input", function () {
		const val = ($(this).val() || "").trim();
		$wrapper.find(".name-sync-address-new-name").text(val);
	});
}

// ---------------------------------------------------------------------------
// إجراء التحديث
// ---------------------------------------------------------------------------

function _do_update(dialog, values, contacts, frm, config, on_success) {
	const new_name = (values.new_name || "").trim();
	const doctype_ar = DOCTYPE_AR[config.doctype] || config.doctype;

	if (!new_name) {
		frappe.msgprint(__("يرجى إدخال اسم قبل المتابعة."));
		return;
	}

	const selected_contacts = [];
	dialog.fields_dict.contacts_list.$wrapper
		.find(".name-sync-contact-checkbox:checked:not([disabled])")
		.each(function () {
			selected_contacts.push($(this).data("contact"));
		});

	const selected_links = [];
	dialog.fields_dict.contacts_list.$wrapper
		.find(".name-sync-link-checkbox:checked:not([disabled])")
		.each(function () {
			selected_links.push({
				doctype: $(this).data("doctype"),
				name: $(this).data("name"),
			});
		});

	const selected_addresses = [];
	dialog.fields_dict.addresses_list.$wrapper
		.find(".name-sync-address-checkbox:checked")
		.each(function () {
			selected_addresses.push($(this).data("address"));
		});

	if (!selected_contacts.length && !selected_links.length && !selected_addresses.length) {
		frappe.msgprint(
			__("يرجى تحديد جهة اتصال أو سجل مرتبط أو عنوان واحد على الأقل، أو النقر على «حفظ {0} فقط».", [doctype_ar])
		);
		return;
	}

	dialog.disable_primary_action();

	frappe.call({
		method: "contact_enhancements.api.name_sync.update_contact_names",
		args: {
			contacts: selected_contacts,
			new_name: new_name,
			linked_records: selected_links,
			linked_addresses: selected_addresses,
			sync_address: values.sync_address !== undefined ? values.sync_address : 1,
			source_address: (contacts[0] && contacts[0].source_address) || "",
			source_doctype: config.doctype,
			source_name: frm.doc.name,
		},
		freeze: true,
		freeze_message: __("جاري تحديث جهات الاتصال والسجلات المرتبطة…"),
		callback(r) {
			// تحديث حقل الاسم في النموذج الأصلي بالاسم الجديد إذا تم تعديله داخل النافذة
			frm.set_value(config.name_field, new_name);

			if (config.doctype === "Contact") {
				frm.doc.__ce_name_synced = 1;
			}

			// استدعاء on_success أولاً لتعيين is_settled = true قبل إغلاق النافذة
			on_success();

			dialog.hide();
			_report_results(r.message || [], new_name);
		},
		error() {
			dialog.enable_primary_action();
			frappe.msgprint({
				title: __("حدث خطأ"),
				message: __("تعذر تحديث جهات الاتصال والسجلات المرتبطة الآن. يرجى المحاولة مرة أخرى."),
				indicator: "red",
			});
		},
	});
}

function _report_results(results, new_name) {
	if (!results.length) return;

	const updated = results.filter((r) => r.status === "updated");
	const failed = results.filter((r) => r.status === "failed");
	const no_perm = results.filter((r) => r.status === "no_permission");

	if (updated.length && !failed.length && !no_perm.length) {
		frappe.show_alert({
			message: __("تم تحديث {0} سجل إلى «{1}».", [updated.length, new_name]),
			indicator: "green",
		});
		return;
	}

	let msg = "";
	if (updated.length) {
		const names = updated.map((r) => `${DOCTYPE_AR[r.doctype] || r.doctype}: ${r.name}`);
		msg += `<p><b>تم تحديث ${updated.length} سجل:</b> ${names.join(", ")}</p>`;
	}
	if (no_perm.length) {
		const names = no_perm.map((r) => `${DOCTYPE_AR[r.doctype] || r.doctype}: ${r.name}`);
		msg += `<p><b>تم تخطي ${no_perm.length} سجل (لا توجد صلاحية):</b> ${names.join(", ")}</p>`;
	}
	if (failed.length) {
		const names = failed.map((r) => `${DOCTYPE_AR[r.doctype] || r.doctype}: ${r.name}`);
		msg += `<p><b>فشل تحديث ${failed.length} سجل:</b> ${names.join(", ")}</p>`;
	}

	frappe.msgprint({
		title: __("نتائج التحديث"),
		message: msg,
		indicator: failed.length ? "red" : "orange",
	});
}
