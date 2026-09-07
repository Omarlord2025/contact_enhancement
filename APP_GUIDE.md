# contact_enhancements — Complete App Guide

A Frappe/ERPNext app that makes **Contact** the single source of truth for every person
in the system, and keeps Customer, Supplier, Employee, Lead and User connected to it.

This document describes everything the app does. It is written for someone who has never
seen it before.

---

## 1. The problem it solves

In stock ERPNext, the same human being routinely ends up as several disconnected records:
a Contact, a Customer, a Lead, and a User — none of which know about each other. Phone
numbers are typed in whatever format the person felt like. Nothing stops the same mobile
number being entered on five different Contacts.

This app enforces one rule everywhere: **a person is a Contact, and every other record that
represents that person points at it.**

It does this in three layers:

| Layer | What it does |
|---|---|
| **Linking** | Every Customer/Supplier/Employee/User has a primary Contact, linked in both directions |
| **Data quality** | Names and phone numbers are normalised and validated on every save, in every language |
| **Propagation** | Change a Contact's name, email or phone once, and every linked record follows |

---

## 2. Doctypes it touches

| Doctype | What the app adds |
|---|---|
| **Contact** | Name/phone rules, duplicate detection, "Linked Addresses" panel |
| **Contact Phone** | Country, channel flags (WhatsApp/Telegram/Landline), E.164 storage, uniqueness |
| **Customer** | Required primary contact on new records (address optional), contact picker dialog |
| **Supplier** | Required primary contact on new records (address optional), contact picker dialog |
| **Employee** | Optional primary contact and address, on-demand picker |
| **User** | Optional primary contact, "Linked Addresses" panel |
| **Lead** | Address inherited from the Contact's other links |
| **Address** | Reused via its native Dynamic Link table — no new fields |

---

## 3. Custom fields

Created by `setup/custom_fields.py`, applied on install and every migrate.

**Contact**
| Field | Type | Purpose |
|---|---|---|
| `linked_addresses_section` | Section Break | Header for the panel below |
| `linked_addresses_html` | HTML | Live list of every address reachable from this Contact |

**Contact Phone** (child table of Contact)
| Field | Type | Purpose |
|---|---|---|
| `country` | Link → Country | Which numbering plan validates this row. Defaults to Egypt |
| `custom_phone_national` | Data | Bare local digits (`01012345678`), search-only |
| `custom_whatsapp` | Check | This number is on WhatsApp |
| `custom_telegram` | Check | This number is on Telegram |
| `custom_landline` | Check | This is a landline, not a mobile |
| `custom_country_resolution` | Select (hidden) | How this row's country was decided — `Detected`, `Pattern`, `Manual`, or `Unresolved`. Blank for rows never touched by the historical backfill. Powers the Phone Country Resolution page, §8.5 |

**Employee**
| Field | Type | Purpose |
|---|---|---|
| `primary_address_and_contact_section` | Section Break | Section header |
| `employee_primary_address` | Link → Address | This employee's address |
| `employee_primary_contact` | Link → Contact | This employee as a person |

**User**
| Field | Type | Purpose |
|---|---|---|
| `user_contact_section` | Section Break | Section header |
| `user_primary_contact` | Link → Contact | This account's person |
| `linked_addresses_section` / `linked_addresses_html` | Section Break / HTML | Live address panel |

---

## 4. Customisations to standard doctypes

Applied via Property Setters in `setup/property_setters.py`.

**Renamed fields** — "First Name" becomes **"Full Name"** on Contact, Employee and User.
The app treats a person's name as one field, not first/middle/last.

**Hidden fields** — `Contact.middle_name`, `Contact.last_name`,
`Contact Phone.is_primary_phone`, `Employee.employee_name`.

**Quick Entry disabled** on Customer, Supplier and User. Frappe's Quick Entry dialog does not
include the primary-contact field, so leaving it on would let someone create a record that
skips the contact requirement entirely.

**Indexes added** (performance):
`Customer.customer_primary_contact`, `Customer.customer_primary_address`,
`Supplier.supplier_primary_contact`, `Employee.employee_primary_contact`,
`User.user_primary_contact`, `Contact Phone.phone`, `Contact.email_id`
(duplicate-detection lookups), `Contact.full_name` (anchored-prefix name-duplicate
lookups), and `contact_person` on Quotation, Sales Order, Sales Invoice, Purchase
Order, Purchase Invoice and Opportunity.

> **Note:** the app does **not** make any field statically mandatory (`reqd`). See §6.

---

## 5. Data entry rules

### 5.1 Name rules

Applied to `Contact.first_name` on every save, and mirrored in JavaScript
(`public/js/contact.js`) for instant feedback. The canonical rule table lives in the
`contact_hooks.py` module docstring.

Rules are applied **in order**, and **correct the value in place — they never reject a save**:

1. A word may not start with `أ` (U+0623) → replaced with plain `ا`
2. Arabic diacritics (tashkeel) and tatweel are deleted
3. Runs of whitespace collapse to a single space; ends trimmed
4. Only **letters (any script)**, **combining marks**, whitespace, `'` and `-` are kept.
   Anything else (digits, symbols) becomes a space. A token left with no letter is dropped
5. A word may not end in `ة` → `ه`, or `ي` → `ى`

**Every writing system is supported.** Arabic, Latin, Cyrillic, Greek, Chinese, Japanese,
Hebrew, Devanagari, Tamil, Bengali and Thai all store correctly, including accented Latin
(`José Müller`) and names with punctuation (`O'Brien`, `Anne-Marie`, `Al-Sayed`).

### 5.2 No minimum on name length

This app used to require a name to have at least three components (first, father's,
family) on every custom data-entry window it built — the shared contact-picker dialog's
"create a new Contact" path (Customer/Supplier/Employee/User all funnel through it) and
`Employee.first_name` directly. **That requirement has been removed.** A name of any
length — one word, two words, or a two-character CJK name like `李伟` — is now accepted
everywhere in this app.

The live "someone with this name is already on file" lookup in the contact-picker dialog
(`api.contact_lookup.search_contacts_by_name_prefix`) still matches on up to three leading
name components, but that is a search heuristic for "is this the same person," independent
of whether any particular length is required of the name itself.

### 5.3 Phone rules

- Stored in **E.164** (`+201012345678`), always
- Validated against the row's own `country` using the `phonenumbers` library — every
  country, not just Egypt
- Formatting noise (spaces, dashes, parens, dots) is stripped before parsing
- If a number carries its own unambiguous country code (a `+`/`00` prefix) that doesn't match
  the row's currently selected `country`, the row's `country` is **auto-corrected to match the
  number** before validation runs — it's never just accepted against the wrong country. A
  plain local-format number (no country code of its own) can't trigger this and is still
  checked against whatever country is selected
- `custom_phone_national` is auto-populated with the bare local digits so staff can still
  search the way they type
- **A landline cannot also be WhatsApp/Telegram** — the flags are mutually exclusive and
  auto-corrected, not rejected
- **A mobile number must be unique across all Contacts.** Enforced by a validate hook *and*
  a database UNIQUE INDEX. Raises `frappe.UniqueValidationError` so other apps can catch
  this case specifically. Landlines are exempt (a shared office line is legitimate)

---

## 6. The contact requirement

New **Customers** and new **Suppliers** need a primary **Contact**.

**No primary Address is ever required** — not on Customer, Supplier or
Employee. The address is still resolved and filled in automatically wherever
one can be found (the cross-doctype lookup, the Lead snapshot, and the
client-side prefill all still run); it is a strong default, not a gate.
Requiring it used to make the app's own creation flow a dead end — the
contact-picker dialog creates a brand-new Contact, which by definition has no
Address, so the save was blocked on a field the dialog gave no way to fill.

This is enforced by validate hooks gated on `doc.is_new()` — **not** by a mandatory field.
That is deliberate and important:

> A static `reqd` is evaluated on *every* save, not just inserts. Making these fields
> mandatory retroactively froze every Customer and Supplier created before the app existed —
> a no-op re-save raised `MandatoryError`, blocking ERPNext's own flows, Data Import,
> background jobs and other apps. Existing records are **grandfathered**: they stay editable.

Both hooks honour `doc.flags.ignore_mandatory`, so Data Import and programmatic callers
behave exactly as they would with a real mandatory field.

---

## 7. How linking works

### 7.1 Two directions

A "primary contact" field is a plain **Link** field. Frappe does *not* automatically create
the reverse relationship, so the app does: on every save, the Contact/Address gets a
**Dynamic Link** row pointing back at the parent record.

That row is inserted **directly**, not by loading and saving the Contact. Loading and saving
the parent held wide locks inside another document's save and caused database deadlocks; it
also bumped the Contact's `modified` timestamp, invalidating copies other code was holding.
Permission is checked explicitly before the insert, exactly as the old save did.

### 7.2 Address inheritance

If a Contact is already linked to a Customer with an address, and the same person is added
as a Supplier, the Supplier's address is filled in automatically.

Resolution order: **Customer → Supplier → Lead → User**. Only ever fills a blank field —
it never overwrites an address someone chose.

### 7.3 Continuous propagation

When a Contact's **name**, **email** or **phone** changes, every linked record is updated:

| Target | Name | Email | Phone |
|---|---|---|---|
| Customer | `customer_name` ¹ | `email_id` | `mobile_no` |
| Supplier | `supplier_name` ¹ | `email_id` | `mobile_no` |
| Employee | `first_name` | `personal_email` | `cell_number` |
| User | `first_name` | *(never)* ² | `mobile_no` |

¹ **Only when the party type is `Individual`.** A Company's or Partnership's trading name is
its own and is never overwritten by its contact person's name.

² `User.email` is that document's naming field — writing it would rename the User, with
consequences for login and audit history.

Each record's fields are written in **one statement inside one savepoint**. If a write fails
(for example, two Users share a Contact and `User.mobile_no` is unique), it retries field by
field so one collision doesn't lose the record's other updates — and it can **never** break
the Contact save that triggered it.

---

## 8. User interface

### 8.1 The contact picker dialog

The core interaction. Search for an existing Contact by name, email or phone — or create a
new one — without leaving the form.

| Doctype | When it appears | Cancellable |
|---|---|---|
| Customer | On opening a new Customer | **No** |
| Supplier | On opening a new Supplier | **No** |
| Employee | "Link / Find Contact" button | Yes |
| User | On opening a new User | Yes |

Search is debounced (300 ms, minimum 3 characters), matches name / email / phone in either
local or international format, and shows enough of each match — company, designation, email,
phone numbers with channel tags — to confirm the right person.

Each dialog collects the extra fields its own doctype needs: Customer gets
individual/company + customer group; Supplier gets supplier type; **Employee gets gender,
date of birth and date of joining**; User gets email and role profile.

### 8.2 Automatic name fill

Picking a Contact fills the parent's name field — `customer_name`, `supplier_name`, or
`first_name` — from the Contact's full name, if it's still blank. This works through **every**
path: the dialog's search, the dialog's create-new, and the native Link dropdown. A
server-side backstop covers API and Data Import, where no browser runs.

### 8.3 Linked Addresses panel

On **Contact** and **User** forms. Shows every address reachable from that person, across
every doctype they're linked to, each labelled with where it came from
(*"via Customer: Acme Trading"*).

Computed live on every view — never a cached copy — so an address added or removed anywhere
appears immediately. On the User form you can also link, create and unlink addresses directly.

### 8.4 Duplicate Mobile Contacts page

An admin page listing every mobile number shared by more than one Contact, worst offenders
first, with the business weight of each (linked parties, transaction counts) so you can tell
which to keep. Merging is one click.

### 8.5 Phone Country Resolution page

An admin page listing every `Contact Phone` row the historical backfill (§11) couldn't
resolve automatically (`custom_country_resolution == "Unresolved"`) — no international
prefix to detect, and not shaped like an Egyptian mobile number either. For each row, the
administrator picks the real country (and landline flag) from a dropdown; saving runs the
row through the normal Contact validate hook chain exactly as a manual edit would, and
marks it `Manual`. Rows a brand-new save auto-detects or auto-corrects (§5.3) never land
here — this page only ever queues what neither automatic rule could decide.

---

## 9. Whitelisted API

### `api/contact_lookup.py`
| Method | Purpose |
|---|---|
| `create_minimal_contact(first_name, phone, country=None)` | Create a Contact with just a name and number |
| `search_contact_by_phone(...)` | Link-field query for primary-contact fields |
| `search_contacts_with_details(txt, start=0, page_len=10)` | Rich match list for the picker dialog |
| `search_contacts_by_name_prefix(txt, page_len=10)` | "Someone with this name exists" lookup (anchored prefix match), §5.2 |
| `get_address_from_contact_links(contact, ...)` | Best single address reachable from a Contact |
| `get_addresses_for_contact(contact)` | Every address, with source labels |

### `api/user_addresses.py`
| Method | Purpose |
|---|---|
| `get_all_addresses_for_user(user, contact)` | Live address list for the User panel |
| `search_addresses_for_user(...)` | Address search for the Add Address dialog |
| `link_existing_address_to_user(address, user)` | Link an existing Address |
| `create_and_link_address(user, ...)` | Create and link in one call |
| `unlink_address_from_user(address, user)` | Remove the link, keep the Address |
| `get_user_addresses(user)` | *Deprecated* — use `get_all_addresses_for_user` |

### `api/contact_dedupe.py`
| Method | Purpose |
|---|---|
| `merge_duplicate_mobile_contacts(phone, survivor)` | Merge every Contact sharing a number |
| `dedupe_contact_phone_rows(contact, phone)` | Merge repeated rows on one Contact |

### `api/duplicate_mobile_contacts.py`
| Method | Purpose |
|---|---|
| `get_report_data()` | Backing data for the admin page |

### `api/phone_country_resolution.py`
| Method | Purpose |
|---|---|
| `get_unresolved_phone_country_report()` | Every Contact Phone row still awaiting manual resolution, §8.5 |
| `list_countries()` | Country dropdown for the resolution page |
| `resolve_phone_country_row(contact, contact_phone_row, country, landline=0)` | Apply an administrator's manual country choice to one row |

### `api/lead_lookup.py`
| Method | Purpose |
|---|---|
| `get_lead_snapshot_for_contact(contact_name)` | Lead fields to prefill a Customer |

### Shared helpers (`utils.py`) — import these rather than re-deriving
`dynamic_link_lookup`, `ensure_contact_linked_to_parent`, `ensure_doc_linked_to_parent`,
`addresses_linked_to_many`, `get_all_addresses_for_contact`,
`resolve_address_from_contact_links`, `address_source_label`,
`backfill_name_from_primary_contact`.

---

## 10. Hooks reference

| Doctype | Event | Handler |
|---|---|---|
| **Contact** | validate | `normalize_contact_first_name` |
| | validate | `enforce_contact_phone_channel_exclusivity` |
| | validate | `normalize_and_validate_contact_phones` |
| | validate | `enforce_unique_mobile_number` |
| | validate | `warn_if_duplicate_contact` |
| | on_update | `propagate_contact_changes_to_linked_doctypes` |
| **Customer** | before_naming | `apply_contact_identity_before_naming` ¹ |
| | validate | `sync_customer_from_primary_contact` |
| | validate | `enforce_primary_contact_on_new_customer` |
| | on_update | `link_primary_contact` |
| **Supplier** | before_naming | `apply_contact_identity_before_naming` ¹ |
| | validate | `backfill_supplier_primary_contact_from_dynamic_link` |
| | validate | `backfill_supplier_name_from_primary_contact` |
| | validate | `sync_supplier_address_from_contact_links` |
| | validate | `enforce_primary_contact_on_new_supplier` |
| | on_update | `link_primary_contact` |
| **Employee** | validate | `sync_employee_contact_from_user` |
| | validate | `backfill_employee_name_from_primary_contact` |
| | validate | `sync_employee_address_from_contact_links` |
| | on_update | `link_employee_contact` |
| **User** | validate | `validate_user_phone_before_contact_sync` |
| | validate | `backfill_user_name_from_primary_contact` |
| | on_update | `link_user_contact` |
| **Lead** | on_update | `sync_lead_address_from_contact_links` |

¹ Registered on `before_naming`, not `validate` — ERPNext's own `Customer.autoname()` /
`Supplier.autoname()` read `customer_name`/`supplier_name` directly, and naming runs before
validate, so a validate-time backfill would be too late on an insert.

**JavaScript** — `contact_picker_dialog.js` and `linked_addresses.js` load on every Desk page
(they're shared components); `customer.js`, `contact.js`, `supplier.js`, `employee.js` and
`user.js` load per doctype.

---

## 11. Installing

```bash
bench get-app contact_enhancements <repo-url>
bench --site <site> install-app contact_enhancements
bench --site <site> migrate
```

### ⚠️ Read before installing on a site with existing data

1. **Every phone number is rewritten to E.164.** `01012345678` becomes `+201012345678`
   everywhere. Any integration reading phone numbers sees the new format.
2. **A UNIQUE INDEX is added on mobile numbers.** If duplicates exist, that patch **aborts
   the migrate** and points you at the Duplicate Mobile Contacts report. Resolve them and
   re-run. It is the last patch, so nothing else is blocked behind it.
3. **Duplicate phone rows within a Contact are deleted** (one kept, flags merged).
4. **`ALTER TABLE` runs on the six transaction doctypes** — Sales Invoice, Sales Order and
   friends. Not instant on a large database. **Use a maintenance window.**
5. **Existing Customers/Suppliers are grandfathered** and stay editable (see §6).

**Recommended:** restore a production backup to a scratch site, migrate there first, and
measure the duplicate count and the `ALTER TABLE` duration before touching production.

---

## 12. Testing

```bash
bench --site <site> run-tests --app contact_enhancements
```

**438 server tests.** There is also a browser test suite (Playwright) covering the creation
flows, name rules across seven writing systems, and the address panels — see the developer
notes in `CLAUDE.md`.

---

## 13. Known limitations

| Limitation | Detail |
|---|---|
| **Customer group default** | The dialog's `customer_group` can default to a group node, which ERPNext rejects. Pick a leaf group |
| **Duplicate warning visibility** | `warn_if_duplicate_contact` uses `msgprint`, which can surface on customer-facing pages if a Contact is created there |
| **JS rule mirror** | The name rules exist in both Python and JavaScript. There is no JS test infrastructure, so keeping them in sync is a review discipline — they have drifted before |

---

## 14. Where to look in the code

```
contact_enhancements/
├── install.py             after_install hook
├── contact_hooks.py       Name + phone rules, uniqueness, propagation  ← the core
├── customer_hooks.py      Customer linking, Lead snapshot
├── supplier_hooks.py      Supplier linking + backfills
├── employee_hooks.py      Employee linking
├── user_hooks.py          User linking, phone pre-clean
├── lead_hooks.py          Lead address inheritance
├── utils.py               Shared helpers — import from here
├── api/                   Whitelisted endpoints (incl. phone_country_resolution.py, §8.5)
├── setup/                 Custom fields + property setters
├── patches/               One-time migrations
├── page/                  Duplicate Mobile Contacts, Phone Country Resolution (§8.4, §8.5)
├── public/js/             Dialogs and panels
└── tests/                 438 tests
```

`CLAUDE.md` in the app root holds the engineering standards, the performance checklist, and a
long list of hard-won gotchas about Frappe internals. **Read it before changing anything.**
