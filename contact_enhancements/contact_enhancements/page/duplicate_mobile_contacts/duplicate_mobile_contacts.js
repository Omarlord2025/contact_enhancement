// Copyright (c) 2026, Omar Sabry and contributors
// For license information, please see license.txt

// A hand-rolled table, not frappe.ui.Page's usual report/list machinery -
// deliberate: Frappe's own report datatable (frappe-datatable) has no
// rowSpan/cell-merging support at all (confirmed by reading its bundled
// source directly), and a real merged box per duplicate group - the
// group's shared mobile number spanning every member Contact's own row
// beneath it - is exactly what this page needs. A plain, non-virtualized
// <table> is fully appropriate for this data volume (a handful of
// duplicate groups on a real site, an occasional admin lookup, not a
// large dataset needing virtualization).
frappe.pages["duplicate-mobile-contacts"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Duplicate Mobile Contacts"),
		single_column: true,
	});

	page.add_inner_button(__("Refresh"), () => render());

	const $summary = $('<div style="margin-bottom: 15px;"></div>').appendTo(page.body);
	const $body = $('<div></div>').appendTo(page.body);

	function render() {
		$body.html(`<div class="text-muted">${__("Loading...")}</div>`);
		frappe.call({
			method: "contact_enhancements.api.duplicate_mobile_contacts.get_report_data",
			callback(r) {
				renderData(r.message || { groups: [], summary: { group_count: 0, contact_count: 0 } });
			},
		});
	}

	function renderData(data) {
		$summary.html(
			`<b>${__("Duplicate Groups")}:</b> ${data.summary.group_count}` +
				`&nbsp;&nbsp;&nbsp;<b>${__("Contacts Involved")}:</b> ${data.summary.contact_count}`
		);

		if (!data.groups.length) {
			$body.html(`<div class="text-muted">${__("No duplicate mobile numbers found.")}</div>`);
			return;
		}

		const rows = [];
		data.groups.forEach((group) => {
			group.members.forEach((member, i) => {
				const cells = [];
				if (i === 0) {
					cells.push(
						`<td rowspan="${group.members.length}" style="vertical-align: middle; font-weight: bold; white-space: nowrap;">` +
							`${frappe.utils.escape_html(group.phone)}</td>`
					);
				}
				cells.push(
					`<td><a href="/app/contact/${encodeURIComponent(member.contact)}">` +
						`${frappe.utils.escape_html(member.contact)}</a></td>`
				);
				cells.push(
					`<td>${
						member.suggested
							? `<span class="indicator-pill green no-indicator-dot">${__("Suggested")}</span>`
							: ""
					}</td>`
				);
				cells.push(`<td>${frappe.utils.escape_html(member.full_name || "")}</td>`);
				cells.push(`<td>${frappe.utils.escape_html(member.email_id || "")}</td>`);
				cells.push(`<td>${member.business_records_html}</td>`);
				cells.push(
					`<td><button class="btn btn-xs ${
						member.suggested ? "btn-primary" : "btn-default"
					}" data-phone="${frappe.utils.escape_html(group.phone)}" ` +
						`data-contact="${frappe.utils.escape_html(member.contact)}">` +
						`${__("Keep this Contact")}</button></td>`
				);
				rows.push(`<tr>${cells.join("")}</tr>`);
			});
		});

		$body.html(`
			<table class="table table-bordered" style="background: var(--card-bg, #fff);">
				<thead>
					<tr>
						<th>${__("Mobile Number")}</th>
						<th>${__("Contact")}</th>
						<th>${__("Suggested")}</th>
						<th>${__("Full Name")}</th>
						<th>${__("Email")}</th>
						<th>${__("Business Records")}</th>
						<th>${__("Action")}</th>
					</tr>
				</thead>
				<tbody>${rows.join("")}</tbody>
			</table>
		`);

		$body.find("button[data-contact]").on("click", function () {
			const phone = $(this).attr("data-phone");
			const survivor = $(this).attr("data-contact");
			frappe.confirm(
				__("Merge every other Contact sharing {0} into {1}? This can't be undone.", [
					phone,
					survivor,
				]),
				() => {
					frappe.call({
						method: "contact_enhancements.api.contact_dedupe.merge_duplicate_mobile_contacts",
						args: { phone: phone, survivor: survivor },
						freeze: true,
						freeze_message: __("Merging..."),
						callback(r) {
							const result = r.message || {};
							if ((result.failed || []).length) {
								frappe.msgprint({
									title: __("Some merges failed"),
									indicator: "orange",
									message: result.failed
										.map(
											(f) =>
												`${frappe.utils.escape_html(f.name)}: ${frappe.utils.escape_html(f.error)}`
										)
										.join("<br>"),
								});
							} else {
								frappe.show_alert({ message: __("Merged successfully"), indicator: "green" });
							}
							render();
						},
					});
				}
			);
		});
	}

	render();
};
