// Copyright (c) 2026, Omar Sabry and contributors
// For license information, please see license.txt

// Shared "Linked Addresses" card rendering - one component, reused by
// both Contact's own read-only table (public/js/contact.js) and User's
// own polished section (public/js/user.js), loaded via app_include_js
// the same way contact_picker_dialog.js is (more than one doctype's own
// script needs it - doctype_js only binds one file to one doctype).
//
// Deliberately a plain re-render-on-demand component, not a persisted/
// cached list: every call to .refresh() below re-fetches from the
// server, so the section only ever shows what's actually true right now
// - never a stale copy left over from an earlier render. Callers are
// responsible for calling .refresh() again whenever something that could
// change the result changes (e.g. the primary contact field itself), not
// just once on page load - that's what makes this "live".

frappe.provide("contact_enhancements");

contact_enhancements.render_linked_addresses = function (field, opts) {
	// opts:
	//   fetch: async () => [address rows] - each row carries
	//     address_display_fields() plus source_label, and (only for
	//     User's own view) removable.
	//   on_remove(address): async fn, called when a removable row's own
	//     Remove button is clicked - omit to render every row read-only.
	//   on_add(): fn, called when the "Add Address" button is clicked -
	//     omit to hide that button entirely (Contact's own table has no
	//     add flow - addresses are only ever added via whichever other
	//     doctype actually owns them).
	//   empty_message: shown when fetch() returns nothing.
	const $wrapper = field.$wrapper;
	$wrapper.empty();

	const $root = $('<div class="linked-addresses-v2"></div>').appendTo($wrapper);
	const $list = $('<div class="la-list"></div>').appendTo($root);

	if (opts.on_add) {
		$(`<button class="btn btn-xs btn-default la-add-btn" style="margin-top: 10px;">${__("Add Address")}</button>`)
			.appendTo($root)
			.on("click", opts.on_add);
	}

	// Guards against an older, slower fetch painting over a newer one -
	// refresh() is called on every form refresh and on every
	// primary-contact change, so two can easily be in flight at once.
	let latest_fetch = 0;
	let has_rendered = false;

	async function refresh() {
		// Only show the loading placeholder on the very first paint.
		// Blanking already-correct rows on every refresh (form load, every
		// save, every route back to the form) is what made this section
		// flash and jump height for no reason - keep what's on screen
		// until the new rows are actually in hand.
		if (!has_rendered) {
			$list.html(`<div class="la-loading text-muted">${__("Loading...")}</div>`);
		}

		const fetch_id = ++latest_fetch;
		let rows;
		try {
			rows = (await opts.fetch()) || [];
		} catch (e) {
			if (fetch_id === latest_fetch) {
				$list.html(`<div class="text-muted">${__("Couldn't load addresses right now.")}</div>`);
				has_rendered = false;
			}
			return;
		}
		if (fetch_id !== latest_fetch) return; // superseded by a newer fetch
		render_rows(rows);
		has_rendered = true;
	}

	function render_rows(rows) {
		$list.empty();
		if (!rows.length) {
			$list.append(`<div class="la-empty text-muted">${opts.empty_message || __("No addresses linked yet.")}</div>`);
			return;
		}
		// One insert rather than one per row - each render_card() call is
		// already an HTML parse, and appending them individually makes the
		// attached flex container re-layout N times.
		$list.append(rows.map((addr) => render_card(addr)));
	}

	function render_card(addr) {
		const esc = frappe.utils.escape_html;
		const line_parts = [addr.address_line1, addr.address_line2, addr.city, addr.country].filter(Boolean);
		const line = esc(line_parts.join(", "));
		const title = esc(addr.address_title || addr.name);
		const type = addr.address_type ? esc(addr.address_type) : "";
		const source = addr.source_label ? esc(addr.source_label) : "";
		const badges = [];
		if (type) badges.push(`<span class="la-badge la-badge-type">${type}</span>`);
		if (addr.is_primary_address) badges.push(`<span class="la-badge la-badge-primary">${__("Primary")}</span>`);
		if (addr.is_shipping_address) badges.push(`<span class="la-badge la-badge-shipping">${__("Shipping")}</span>`);

		const $card = $(`
			<div class="la-card">
				<div class="la-card-main">
					<div class="la-card-title">
						<a href="/app/address/${encodeURIComponent(addr.name)}" target="_blank">${title}</a>
						${badges.join("")}
					</div>
					<div class="la-card-line">${line}</div>
					${source ? `<div class="la-card-source">${source}</div>` : ""}
				</div>
			</div>
		`);

		if (opts.on_remove && addr.removable) {
			const $remove = $(`<button class="btn btn-xs btn-link text-danger la-remove-btn">${__("Remove")}</button>`);
			$remove.on("click", () => {
				frappe.confirm(__("Remove this address?"), async () => {
					await opts.on_remove(addr.name);
					refresh();
				});
			});
			$card.append($remove);
		}
		return $card;
	}

	// Scoped styling, injected once per page load (idempotent check) -
	// this app has no separate CSS bundle, and a handful of rules for a
	// custom card list doesn't warrant adding one.
	if (!document.getElementById("contact-enhancements-la-style")) {
		const style = document.createElement("style");
		style.id = "contact-enhancements-la-style";
		style.textContent = `
			.linked-addresses-v2 .la-list { display: flex; flex-direction: column; gap: 8px; margin-top: 6px; }
			.linked-addresses-v2 .la-card { display: flex; justify-content: space-between; align-items: flex-start;
				border: 1px solid var(--border-color, #d1d8dd); border-radius: 8px; padding: 10px 12px; background: var(--fg-color, #fff); }
			.linked-addresses-v2 .la-card-title { font-weight: 600; margin-bottom: 2px; }
			.linked-addresses-v2 .la-card-title a { color: inherit; }
			.linked-addresses-v2 .la-card-line { font-size: 12px; color: var(--text-muted, #8d99a6); }
			.linked-addresses-v2 .la-card-source { font-size: 11px; color: var(--blue-500, #2490ef); margin-top: 4px; }
			.linked-addresses-v2 .la-badge { display: inline-block; font-size: 10px; font-weight: 600; border-radius: 10px;
				padding: 1px 8px; margin-left: 6px; background: var(--gray-100, #f4f5f6); color: var(--text-muted, #8d99a6); }
			.linked-addresses-v2 .la-badge-primary { background: var(--green-100, #dff5e3); color: var(--green-600, #29a94e); }
			.linked-addresses-v2 .la-badge-shipping { background: var(--yellow-100, #fdf3d7); color: var(--yellow-600, #cca306); }
			.linked-addresses-v2 .la-remove-btn { white-space: nowrap; }
		`;
		document.head.appendChild(style);
	}

	refresh();
	return { refresh };
};
