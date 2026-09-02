// Copyright (c) 2026, Omar Sabry and contributors
// For license information, please see license.txt

// Client-side mirror of the 5 Arabic first-name rules documented in full
// (with the exact Unicode ranges and rationale for each) at the top of
// contact_enhancements/contact_hooks.py - that docstring is the single
// source of truth; this file intentionally doesn't re-document the rules,
// only mirrors their behavior for instant feedback before a round-trip to
// the server. The server-side contact_hooks.normalize_contact_first_name
// hook is the real, unbypassable normalization (REST API, Data Import) -
// this is purely a UX convenience layer that shows the corrected value
// before that round-trip. This app has no JS test infrastructure, so
// keeping these two implementations in sync is a code-review discipline,
// not something automated tests catch here.
//
// Deliberately auto-normalizes, not rejects - matching the Python side,
// this never blocks a save with frappe.validated = false; it corrects
// the value in place.
function normalize_arabic_first_name(name) {
	if (!name) return name;

	// 1. Delete tashkeel/tatweel.
	const tashkeelPattern = /[ً-ٰٟـ]/g;
	name = name.replace(tashkeelPattern, "");

	// 2. Replace disallowed characters (digits/symbols) with a space,
	// not delete them outright - "Ahmed1Ali" -> "Ahmed Ali" (two
	// words), not "AhmedAli" (silently merged).
	const invalidCharsPattern = /[^a-zA-Z؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿\s]/g;
	name = name.replace(invalidCharsPattern, " ");

	// 3. Collapse any whitespace run to a single space, trim the ends.
	name = name.replace(/\s+/g, " ").trim();

	// 4. A word-initial "أ" (U+0623) becomes plain "ا" - "آ"/"إ" are untouched.
	name = name.replace(/(^|\s)أ/g, "$1ا");

	// 5. A word-final "ة" (U+0629) becomes "ه"; a word-final "ي" (U+064A,
	// not "ى") becomes "ى".
	name = name.replace(/(ة|ي)(?=\s|$)/g, (match) => (match === "ة" ? "ه" : "ى"));

	return name;
}

frappe.ui.form.on("Contact", {
	first_name(frm) {
		const normalized = normalize_arabic_first_name(frm.doc.first_name);
		if (normalized !== frm.doc.first_name) {
			frm.set_value("first_name", normalized);
		}
	},
	validate(frm) {
		// Safety net for anything that sets first_name without going
		// through the field event above (e.g. a paste immediately
		// followed by Ctrl+S before the change event settles).
		frm.doc.first_name = normalize_arabic_first_name(frm.doc.first_name);
	},
	refresh(frm) {
		// Only makes sense once this Contact actually exists - a fresh,
		// unsaved Contact can't be Dynamic-Linked to anything yet, so
		// there's nothing for the live lookup to find regardless.
		if (frm.is_new()) return;
		const field = frm.get_field("linked_addresses_html");
		if (!field) return;

		// Reuse the existing component and just re-fetch in place.
		// refresh() fires on load, on every save and on every route back
		// to the form; rebuilding each time emptied the section and
		// repainted "Loading..." over rows that were already correct.
		const existing = frm.__ce_linked_addresses;
		if (existing && $.contains(document, existing.wrapper)) {
			existing.handle.refresh();
			return;
		}

		const handle = contact_enhancements.render_linked_addresses(field, {
			fetch: () =>
				frappe
					.call({
						method: "contact_enhancements.api.contact_lookup.get_addresses_for_contact",
						args: { contact: frm.doc.name },
					})
					.then((r) => {
						// frappe.call resolves with exc set instead of
						// rejecting, so let the component's own error
						// state actually trigger.
						if (r.exc) throw new Error(r.exc);
						return r.message;
					}),
			empty_message: __("No addresses linked to this Contact yet - via any Customer, Supplier, Employee, Lead, or User."),
		});
		frm.__ce_linked_addresses = { handle, wrapper: field.$wrapper[0] };
	},
});

const LANDLINE_FLAG = "custom_landline";
const MOBILE_ORIENTED_PHONE_FLAGS = ["is_primary_mobile_no", "custom_whatsapp", "custom_telegram"];

// Mirrors contact_hooks.py's own module docstring above
// enforce_contact_phone_channel_exclusivity - that's the single source of
// truth for the reasoning (a landline can't be WhatsApp/Telegram-capable
// or a primary mobile number); this only mirrors the behavior live in the
// grid, the instant a checkbox is clicked, instead of waiting for save.
// Contact Phone is only ever embedded by Contact itself (confirmed by
// grepping every doctype definition in this bench for "options": "Contact
// Phone" - see contact_enhancements/CLAUDE.md), so this one handler is
// already the complete picture.
//
// Deliberately mutates locals[cdt][cdn] directly and does one explicit
// grid.refresh() at the end, instead of chaining frappe.model.set_value
// calls for the sibling fields - confirmed by testing that even a single
// set_value call for a sibling field, triggered from inside this same
// field's own change handler (however it's deferred - inline, awaited,
// or setTimeout'd), reliably clears the *other* field back to unchecked
// via its own further refresh, including the field that was just clicked
// to get here in the first place. Setting the row's own data directly and
// refreshing once, after this handler has fully returned, avoids that
// cascade entirely.
function apply_landline_exclusivity_and_refresh(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	let changed = false;

	if (row[LANDLINE_FLAG]) {
		MOBILE_ORIENTED_PHONE_FLAGS.forEach((fieldname) => {
			if (row[fieldname]) {
				row[fieldname] = 0;
				changed = true;
			}
		});
	} else if (MOBILE_ORIENTED_PHONE_FLAGS.some((fieldname) => row[fieldname])) {
		row[LANDLINE_FLAG] = 0;
		changed = true;
	}

	if (changed) {
		// refresh_row(cdn), not the whole grid's own refresh() - a full
		// grid refresh rebuilds every row's DOM from scratch, which left
		// the *other* checkbox in this same row unresponsive to a
		// follow-up click for a while after (confirmed by testing).
		// refresh_row only rebuilds this one row.
		frm.fields_dict.phone_nos.grid.refresh_row(cdn);
	}
}

frappe.ui.form.on("Contact Phone", {
	custom_landline(frm, cdt, cdn) {
		apply_landline_exclusivity_and_refresh(frm, cdt, cdn);
	},
	is_primary_mobile_no(frm, cdt, cdn) {
		apply_landline_exclusivity_and_refresh(frm, cdt, cdn);
	},
	custom_whatsapp(frm, cdt, cdn) {
		apply_landline_exclusivity_and_refresh(frm, cdt, cdn);
	},
	custom_telegram(frm, cdt, cdn) {
		apply_landline_exclusivity_and_refresh(frm, cdt, cdn);
	},
});
