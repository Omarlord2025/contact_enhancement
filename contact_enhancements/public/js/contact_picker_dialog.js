// Copyright (c) 2026, Omar Sabry and contributors
// For license information, please see license.txt

// Shared search-as-you-type contact-picker dialog (Phase 1), extracted
// from public/js/customer.js's own show_mandatory_contact_dialog once
// Supplier needed the exact same UI. One global entry point,
// contact_enhancements.show_contact_picker_dialog(frm, config), loaded via
// a real app_include_js entry (not doctype_js) since more than one
// doctype's own script needs it - doctype_js only binds one file to one
// doctype.
//
// config:
//   primary_contact_fieldname: the caller's own Link-to-Contact field to
//     set once a Contact is picked/created (e.g. "customer_primary_contact",
//     "supplier_primary_contact").
//   title: dialog title.
//   no_cancel: true for a forced-onload, non-dismissable dialog (Customer/
//     Supplier's own posture); false for a proactive-but-dismissible
//     dialog (Employee/User).
//   extra_dialog_fields: caller-supplied dialog fields (e.g. Customer's
//     Customer Group / Individual / Company fields), inserted after the
//     "Create a New Contact" section break, before the primary action.
//   on_dialog_ready(dialog): optional, called once the dialog is
//     constructed (before .show()) - lets the caller wire up behavior
//     specific to its own extra_dialog_fields (e.g. Customer's Individual/
//     Company mutual exclusivity), which this shared module has no reason
//     to know about.
//   validate_extra_fields(values): optional, called on EITHER path -
//     right before create_minimal_contact on the "Create New Contact"
//     path (after this dialog's own built-in field checks), or right
//     before a search result's own "Select" is accepted - return a
//     user-facing error message string to block submission (e.g.
//     Customer's "Enter a company name." when new_is_company is checked,
//     or User's own "Enter this User's own Email."), or a falsy value to
//     let it proceed.
//   on_before_finish(dialog_values, contact_name): optional, called on
//     EITHER path - creating a new Contact or picking an existing one via
//     search (finish() itself calls it, not primary_action, so both
//     converge on the same call) - before the primary contact field is
//     actually set and the dialog closes. Lets the caller apply its own
//     doctype-specific side effects (e.g. Customer's customer_type/
//     customer_group/customer_name, or User's own email/role_profile)
//     using the dialog's own field values, regardless of which path the
//     user actually took to pick a Contact.

// Frappe doesn't auto-create a per-app JS namespace just because an app is
// installed - confirmed by checking how erpnext's own equivalent global
// gets set up (erpnext/public/js/conf.js's own explicit
// frappe.provide("erpnext")). frappe.provide is idempotent (a no-op if the
// object already exists), safe regardless of load order relative to any
// other file that also calls it.
frappe.provide("contact_enhancements");

/**
 * Prefill fields on a parent document from the Contact its primary-contact
 * field points at - the "I picked a Contact, fill in the name" behaviour,
 * in one place instead of re-implemented per doctype.
 *
 * Bound to the primary-contact field's own change event by each caller,
 * NOT to the dialog, so it fires for every way that field can end up set:
 * the picker dialog's search, the picker's create-new path, and the native
 * Link dropdown directly. Getting this wrong is a mistake this app has
 * already made twice - supplier.js originally only filled supplier_name on
 * the dialog's create-new path, and user.js only ever filled first_name
 * inside the dialog, so picking a Contact from the native dropdown synced
 * nothing at all.
 *
 * Only ever fills a field that is currently blank: this is a convenience
 * for a fresh record, never a rename of something already entered. The
 * server-side equivalent for later renames
 * (contact_hooks.propagate_contact_changes_to_linked_doctypes) is
 * deliberately stricter still - it refuses to touch a Company's or a
 * Partnership's trading name at all.
 *
 * @param {object} frm - the parent form.
 * @param {string} contact_fieldname - field holding the Contact's name.
 * @param {object} field_map - {target fieldname: Contact fieldname}, e.g.
 *   {supplier_name: "full_name"} or {first_name: "full_name", email: "email_id"}.
 * @returns {Promise} resolves once any prefill has been applied.
 */
contact_enhancements.prefill_from_contact = function (frm, contact_fieldname, field_map) {
	const contact = frm.doc[contact_fieldname];
	if (!contact) return Promise.resolve();

	// Nothing to do if every target already has a value - skip the
	// round-trip entirely rather than fetching and discarding.
	const wanted = Object.keys(field_map).filter((target) => !frm.doc[target]);
	if (!wanted.length) return Promise.resolve();

	const source_fields = wanted.map((target) => field_map[target]);
	return frappe.db
		.get_value("Contact", contact, source_fields)
		.then(({ message }) => {
			if (!message) return;
			wanted.forEach((target) => {
				const value = message[field_map[target]];
				// Re-check blankness: the await above gives other handlers
				// (a Lead snapshot, say) a chance to fill it first, and
				// theirs is the more specific answer.
				if (value && !frm.doc[target]) frm.set_value(target, value);
			});
		})
		.catch(() => {
			// A convenience prefill into an editable field - never worth
			// interrupting anyone over, but never left as an unhandled
			// rejection either.
		});
};

// A server error inside a non-cancelable dialog (static: true, no_cancel())
// leaves the user with no way to close it and start over, so every
// frappe.call here needs its own explicit error feedback, not just
// Frappe's own default handling - see contact_enhancements/CLAUDE.md's
// performance checklist.
function _contact_picker_dialog_show_call_error(message) {
	frappe.msgprint({
		title: __("Something Went Wrong"),
		message,
		indicator: "red",
	});
}

contact_enhancements.show_contact_picker_dialog = function (frm, config) {
	if (frm.doc[config.primary_contact_fieldname]) return;

	const dialog = new frappe.ui.Dialog({
		title: config.title,
		static: !!config.no_cancel,
		fields: [
			{
				fieldtype: "Link",
				fieldname: "country",
				label: __("Country"),
				options: "Country",
				default: "Egypt",
				description: __("Searchable across every country - used to check the phone number format below."),
			},
			{
				fieldtype: "Data",
				fieldname: "phone",
				label: __("Phone Number"),
				description: __(
					"Search for an existing Contact by phone number, or enter one below to create a new Contact."
				),
			},
			{ fieldtype: "HTML", fieldname: "results" },
			{ fieldtype: "Section Break", label: __("Create a New Contact") },
			// Full Name, not First Name - matches Contact.first_name's own
			// relabeled Property Setter (Phase 0e). Fieldname stays
			// new_first_name; only the label changes.
			{ fieldtype: "Data", fieldname: "new_first_name", label: __("Full Name") },
			// Live "this person may already exist" list, fed by the name
			// being typed just above. Placed here - before the caller's own
			// extra_dialog_fields spread - so it always renders directly
			// under the name input regardless of which doctype opened the
			// dialog. Same bare-HTML-field pattern as `results` above.
			{ fieldtype: "HTML", fieldname: "name_matches" },
			...(config.extra_dialog_fields || []),
		],
		primary_action_label: __("Create New Contact"),
		primary_action(values) {
			if (!values.country) {
				frappe.msgprint(__("Select a Country."));
				return;
			}
			if (!values.phone) {
				frappe.msgprint(__("Enter a phone number first."));
				return;
			}
			if (!values.new_first_name) {
				frappe.msgprint(__("Enter a first name for the new Contact."));
				return;
			}
			if (!extra_fields_are_valid(values)) return;
			// Per-country phone format validation happens server-side now -
			// a bad number surfaces via this call's own error() callback
			// below instead of an instant client-side check.
			frappe.call({
				method: "contact_enhancements.api.contact_lookup.create_minimal_contact",
				args: {
					first_name: values.new_first_name,
					phone: values.phone,
					country: values.country,
				},
				freeze: true,
				callback(r) {
					if (r.message) {
						finish(r.message);
					}
				},
				error() {
					// Fatal for this attempt (unlike the live-prefill call a
					// caller's own on_before_finish might make): nothing was
					// created, and a non-cancelable dialog leaves the user
					// with no other path forward besides trying again.
					_contact_picker_dialog_show_call_error(
						__("Couldn't create the new Contact right now. Please check the details above and try again.")
					);
				},
			});
		},
	});

	if (config.no_cancel) {
		// Required, not optional: enforcement mechanism for a mandatory
		// primary-contact field - no closing without picking or creating a
		// Contact. `static: true` (passed above) already disables both
		// backdrop-click and Escape-key dismissal.
		dialog.no_cancel();
	}

	function get_values_ignoring_missing() {
		// get_values(true) - ignore_errors=true - reads current field
		// values without Frappe's own built-in reqd check blocking with
		// its generic "Missing Values Required" popup (and returning null,
		// which every caller below would otherwise have to guard against
		// separately). The "create a new Contact" fields (new_first_name,
		// etc.) are legitimately blank when this runs via the "pick an
		// existing Contact" path, and any reqd extra_dialog_fields (e.g.
		// User's own Email) get their own clearer, caller-authored message
		// from validate_extra_fields below instead - never Frappe's
		// generic one.
		return dialog.get_values(true) || {};
	}

	function extra_fields_are_valid(values) {
		// Shared by both paths (create-new, inside primary_action above,
		// and pick-existing, in render_results's own "Select" handler
		// below) - a caller's extra_dialog_fields (e.g. User's own
		// required Email) must hold regardless of which path picks the
		// Contact, not just the create-new one.
		if (!config.validate_extra_fields) return true;
		const error_message = config.validate_extra_fields(values);
		if (error_message) {
			frappe.msgprint(error_message);
			return false;
		}
		return true;
	}

	function finish(contact_name) {
		if (config.on_before_finish) {
			config.on_before_finish(get_values_ignoring_missing(), contact_name);
		}
		frm.set_value(config.primary_contact_fieldname, contact_name);
		dialog.hide();
	}

	function render_results(matches) {
		// Shows enough of each match's own data - not just a name - that
		// whoever's entering data can confirm a match with the person in
		// front of them ("is this your email? your WhatsApp number?")
		// instead of picking a bare name off a list. Each dynamic piece is
		// escaped individually before being assembled into the row's HTML,
		// not escaped as a single pre-built string, to avoid double-escaping.
		const $results = dialog.fields_dict.results.$wrapper;
		if (!matches.length) {
			$results.html(`<div class="text-muted">${__("No matching Contact found.")}</div>`);
			return;
		}
		$results.html(
			matches
				.map((match) => {
					const name = frappe.utils.escape_html(match.name);
					const full_name = frappe.utils.escape_html(match.full_name || match.name);

					const company_line = [match.company_name, match.designation]
						.filter(Boolean)
						.map(frappe.utils.escape_html)
						.join(" — ");

					const email_line = match.email_id ? frappe.utils.escape_html(match.email_id) : "";

					const phone_lines = (match.phones || [])
						.map((row) => {
							const phone = frappe.utils.escape_html(row.phone);
							const channels = row.channels && row.channels.length ? ` (${row.channels.map(frappe.utils.escape_html).join(", ")})` : "";
							return `<div>${phone}${channels}</div>`;
						})
						.join("");

					const detail_lines = [company_line, email_line]
						.filter(Boolean)
						.map((line) => `<div>${line}</div>`)
						.join("");

					return `
						<div class="contact-match-row" style="display: flex; align-items: center; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid var(--border-color);">
							<div>
								<b>${full_name}</b>
								<div class="text-muted">${detail_lines}${phone_lines}</div>
							</div>
							<button class="btn btn-xs btn-primary select-contact-btn" data-name="${name}">${__("Select")}</button>
						</div>`;
				})
				.join("")
		);
	}

	// One delegated handler, bound once, instead of re-binding one per
	// result row on every keystroke-driven search (up to 10 each time).
	// Delegation also survives $results.html(...) replacing the rows.
	dialog.fields_dict.results.$wrapper.on("click", ".select-contact-btn", function () {
		if (!extra_fields_are_valid(get_values_ignoring_missing())) return;
		finish($(this).attr("data-name"));
	});

	// Monotonic token so a slow response for an earlier query can't paint
	// over the results of a later one. Searches are 300ms apart at most,
	// and response times vary with how much of the Contact/Contact Phone
	// join a given query has to scan, so overtaking is realistic.
	let latest_search = 0;

	const search = frappe.utils.debounce((txt) => {
		if (!txt || txt.length < 3) {
			latest_search++; // cancel any in-flight response
			dialog.fields_dict.results.$wrapper.empty();
			return;
		}
		const search_id = ++latest_search;
		frappe.call({
			method: "contact_enhancements.api.contact_lookup.search_contacts_with_details",
			args: { txt, start: 0, page_len: 10 },
			callback(r) {
				if (search_id !== latest_search) return; // superseded
				render_results(r.message || []);
			},
			error() {
				if (search_id !== latest_search) return; // superseded
				// An inline message in the results area, not a msgprint -
				// this fires on every failed keystroke-driven search while
				// e.g. connectivity is flaky, and a modal dialog popping up
				// repeatedly while the user is still typing would be worse
				// than the silent failure this replaces.
				dialog.fields_dict.results.$wrapper.html(
					`<div class="text-muted">${__("Couldn't search right now - check your connection and try again.")}</div>`
				);
			},
		});
	}, 300);

	dialog.fields_dict.phone.$input.on("input", (e) => search(e.target.value));

	// ---- "this person may already exist" - matching on the NAME ----
	//
	// Phone-based duplicate detection cannot catch the case that matters
	// most here: the same person coming back and being entered again with a
	// DIFFERENT number. Nothing about the two records matches on phone, but
	// the name usually does. So as the name is typed, show anyone already
	// on file whose name starts with the same components, with their
	// numbers, so the person entering data can recognise them.
	//
	// Informational, never blocking - it does not gate the create button.
	const $name_matches = dialog.fields_dict.name_matches.$wrapper;

	function render_name_matches(matches, typed_name) {
		if (!matches.length) {
			$name_matches.empty();
			return;
		}
		const esc = frappe.utils.escape_html;
		const rows = matches
			.map((match) => {
				const full_name = esc(match.full_name || match.name);
				const detail = [match.company_name, match.designation]
					.filter(Boolean)
					.map(esc)
					.join(" — ");
				const phones = (match.phones || [])
					.map((row) => {
						const channels =
							row.channels && row.channels.length
								? ` (${row.channels.map(esc).join(", ")})`
								: "";
						return `<div>${esc(row.phone)}${channels}</div>`;
					})
					.join("");
				const no_phone = (match.phones || []).length
					? ""
					: `<div class="text-muted">${__("No number on file")}</div>`;
				return `
					<div class="existing-person-row" style="display: flex; align-items: center; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid var(--border-color);">
						<div>
							<b>${full_name}</b>
							<div class="text-muted">${detail ? `<div>${detail}</div>` : ""}${phones}${no_phone}</div>
						</div>
						<button class="btn btn-xs btn-default open-existing-contact-btn" data-name="${esc(match.name)}">${__("Open & Add Number")}</button>
					</div>`;
			})
			.join("");

		$name_matches.html(`
			<div style="margin-top: 8px;">
				<div class="text-muted" style="margin-bottom: 4px;">
					${__("Someone with this name is already on file — check before creating a new person:")}
				</div>
				${rows}
			</div>`);
	}

	// Its own delegated handler on its own wrapper: the phone results
	// handler above is bound to the `results` wrapper specifically, so it
	// would never see clicks in here even with the same class.
	$name_matches.on("click", ".open-existing-contact-btn", function () {
		const contact_name = $(this).attr("data-name");
		const values = get_values_ignoring_missing() || {};
		// Hand the typed number to the Contact form, which stages it as an
		// unsaved row for the user to check and save. Set BEFORE routing:
		// frappe.set_route is client-side navigation, so this survives.
		contact_enhancements.pending_contact_phone = values.phone
			? { contact: contact_name, phone: values.phone, country: values.country }
			: null;
		dialog.hide();
		frappe.set_route("Form", "Contact", contact_name);
	});

	let latest_name_search = 0;

	const search_by_name = frappe.utils.debounce((txt) => {
		if (!txt || txt.trim().length < 3) {
			latest_name_search++; // cancel any in-flight response
			$name_matches.empty();
			return;
		}
		const search_id = ++latest_name_search;
		frappe.call({
			method: "contact_enhancements.api.contact_lookup.search_contacts_by_name_prefix",
			args: { txt, page_len: 10 },
			callback(r) {
				if (search_id !== latest_name_search) return; // superseded
				render_name_matches(r.message || [], txt);
			},
			error() {
				if (search_id !== latest_name_search) return; // superseded
				// Silent: this is a helpful extra, not something the user
				// asked for, so a failure should never interrupt them
				// mid-name. The phone search above shows an inline message
				// because the user explicitly typed a query there.
				$name_matches.empty();
			},
		});
	}, 300);

	dialog.fields_dict.new_first_name.$input.on("input", (e) => search_by_name(e.target.value));

	if (config.on_dialog_ready) {
		config.on_dialog_ready(dialog);
	}

	dialog.show();
};
