// Copyright (c) 2026, Omar Sabry and contributors
// For license information, please see license.txt

// Same hand-rolled-table shape as the duplicate-mobile-contacts page (a
// plain, non-virtualized <table> - fully appropriate for this data volume,
// a handful of unresolved rows on a real site). Country options are fetched
// once per page load, not once per row - see loadCountriesThenRender.
frappe.pages["phone-country-resolution"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Phone Country Resolution"),
		single_column: true,
	});

	page.add_inner_button(__("Refresh"), () => render());

	const $summary = $('<div style="margin-bottom: 15px;"></div>').appendTo(page.body);
	const $body = $("<div></div>").appendTo(page.body);

	let countryOptions = null;

	function loadCountriesThenRender() {
		if (countryOptions) {
			render();
			return;
		}
		frappe.call({
			method: "contact_enhancements.api.phone_country_resolution.list_countries",
			callback(r) {
				countryOptions = r.message || [];
				render();
			},
		});
	}

	function render() {
		$body.html(`<div class="text-muted">${__("Loading...")}</div>`);
		frappe.call({
			method: "contact_enhancements.api.phone_country_resolution.get_unresolved_phone_country_report",
			callback(r) {
				renderData(r.message || []);
			},
		});
	}

	function countrySelectHtml() {
		const options = countryOptions
			.map((name) => `<option value="${frappe.utils.escape_html(name)}">${frappe.utils.escape_html(name)}</option>`)
			.join("");
		return `<select class="form-control country-select"><option value=""></option>${options}</select>`;
	}

	function renderData(rows) {
		$summary.html(`<b>${__("Unresolved Phone Numbers")}:</b> ${rows.length}`);

		if (!rows.length) {
			$body.html(`<div class="text-muted">${__("No unresolved phone numbers - everything is either automatically resolved or already handled.")}</div>`);
			return;
		}

		const trs = rows.map((row) => {
			return `<tr data-contact="${frappe.utils.escape_html(row.contact)}" data-row="${frappe.utils.escape_html(
				row.contact_phone_row
			)}">
				<td><a href="/app/contact/${encodeURIComponent(row.contact)}">${frappe.utils.escape_html(
					row.full_name || row.contact
				)}</a></td>
				<td>${frappe.utils.escape_html(row.phone || "")}</td>
				<td>${countrySelectHtml()}</td>
				<td class="text-center"><input type="checkbox" class="landline-checkbox"></td>
				<td><button class="btn btn-xs btn-primary submit-btn">${__("Submit")}</button></td>
			</tr>`;
		});

		$body.html(`
			<table class="table table-bordered" style="background: var(--card-bg, #fff);">
				<thead>
					<tr>
						<th>${__("Contact")}</th>
						<th>${__("Phone")}</th>
						<th>${__("Country")}</th>
						<th>${__("Landline")}</th>
						<th>${__("Action")}</th>
					</tr>
				</thead>
				<tbody>${trs.join("")}</tbody>
			</table>
		`);

		$body.find(".submit-btn").on("click", function () {
			const $tr = $(this).closest("tr");
			const contact = $tr.attr("data-contact");
			const contact_phone_row = $tr.attr("data-row");
			const country = $tr.find(".country-select").val();
			const landline = $tr.find(".landline-checkbox").is(":checked") ? 1 : 0;

			if (!country) {
				frappe.show_alert({ message: __("Select a Country first."), indicator: "orange" });
				return;
			}

			frappe.call({
				method: "contact_enhancements.api.phone_country_resolution.resolve_phone_country_row",
				args: { contact, contact_phone_row, country, landline },
				freeze: true,
				freeze_message: __("Saving..."),
				callback() {
					frappe.show_alert({ message: __("Resolved"), indicator: "green" });
					render();
				},
				error(r) {
					frappe.msgprint({
						title: __("Could not resolve this row"),
						indicator: "red",
						message: (r && r.exc) || __("Something went wrong."),
					});
				},
			});
		});
	}

	loadCountriesThenRender();
};
