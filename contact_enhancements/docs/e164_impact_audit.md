# Phase 0b bench-wide E.164 impact audit

Required deliverable before `normalize_and_validate_contact_phone` changes its
return value from local-digit form (`01012345678`) to E.164
(`+201012345678`). Every app in this bench was checked for code that reads,
writes, or compares `Contact Phone.phone`, `phone_nos`, or
`custom_phone_e164`. Disposition legend:

- **Unaffected** — no phone-format-sensitive code found.
- **Safe / benefits** — already format-agnostic (normalizes before
  comparing), or the format change is purely cosmetic (display only).
- **Needs its own fix** — a genuine correctness risk once `phone` becomes
  E.164. Per this plan's own scope boundary, these are documented here, not
  fixed here — `custom_webshop` is a separate app/maintainer.

## Apps with zero references

Confirmed via bench-wide grep for `Contact Phone`, `phone_nos`,
`custom_phone_e164`: **payments**, **webshop**, **custom_shipping_rule**,
**currency_audit**, **wheel_tracker**, **auto_direction**. No impact.

## custom_webshop

The only other app in the bench that touches Contact's phone data. File by
file:

### `signup/linking.py` — Safe / benefits

- `_add_verified_details()` (~line 468): `any(try_to_e164(row.phone) ==
  doc.phone_e164 for row in contact.get("phone_nos", []))` — already routes
  every `row.phone` through `try_to_e164()` before comparing. Format-agnostic
  today, stays correct after the conversion.
- The canonicalize-on-verify loop (~line 289-305): `if row.phone ==
  doc.phone_e164: continue`, then `if try_to_e164(row.phone, region) !=
  doc.phone_e164: continue`. The first check is a fast-path that currently
  only matches on the rare row that already happens to be E.164; once
  `phone` itself is always E.164, this fast path becomes the *normal* case
  and the fallback `try_to_e164()` re-derivation (confirmed elsewhere this
  session: an explicit `+` prefix is always honored over any region hint) is
  simply never reached for legitimate matches. No behavior change, code gets
  more effective, not less.
- The trailing `Customer.mobile_no` mirror check (~line 310-314) compares
  against `Customer.mobile_no`, a native ERPNext field this plan does not
  touch at all — unaffected regardless.

### `signup/audit.py` — Safe

Uses `try_to_e164()`-normalized comparisons throughout. Format-agnostic.

### `www/checkout/index.py` — Safe (cosmetic only)

Reads `Contact Phone.phone` (filtered `is_primary_mobile_no=1`) as a
read-only fallback into a `mobile_no` display/prefill value, no equality
comparison against another format anywhere in this file. Once `phone` is
E.164, the checkout page will display/prefill `+201012345678` instead of
`01012345678` — a UX/display difference only, not a correctness bug.

### `shopping_cart/cart_override.py` — **Needs its own fix (not fixed here)**

`update_customer_info()`'s write paths (new-Contact creation, and the
"replace all numbers" branch) are safe: they feed raw, unnormalized
`contact_data.get("mobile_no")` straight into `phone.phone`, but
`contact_enhancements`'s own `normalize_and_validate_contact_phones`
validate hook renormalizes it to E.164 on every `contact.save()` regardless
of what format was fed in.

The **existing-Contact-update branch** (~lines 233-262) is a genuine risk.
It does direct `==` string comparisons between raw, un-normalized values —
`new_mobile = contact_data.get("mobile_no")` (raw webshop signup-form input)
and `user_mobile = frappe.db.get_value("User", ..., "mobile_no")` (cleaned of
formatting noise by this app's own `user_hooks.py`, but never converted to
E.164) — against already-persisted `phone.phone` rows:

```python
for phone in contact.get("phone_nos", []):
    if phone.phone == new_mobile:        # line 242
        ...
for phone in contact.get("phone_nos", []):
    if phone.phone == user_mobile:       # line 254
        ...
if not any(p.phone == new_mobile for p in contact.get("phone_nos", [])):  # line 258
```

Once `phone.phone` is stored as E.164, none of these three comparisons can
ever match an existing row again, even for the literal same phone number.
Concrete consequences at webshop checkout, once 0b ships:

1. The "already present" checks (242, 258) always report false negatives →
   a redundant duplicate `phone_nos` row gets appended for a number the
   Contact already has (the new row is itself later renormalized to E.164 by
   this app's own hook, so it can end up byte-identical to the row already
   there — two rows, same value, on one Contact).
2. The "which row is the known mobile" lookup (254) never finds its target,
   so the correct existing row's `is_primary_mobile_no` flag never gets
   re-asserted, leaving stale primary-flag state.
3. Once 0d's mobile-uniqueness enforcement ships, a duplicate append driven
   by this bug risks turning what used to be a silent redundancy into a hard
   `contact.save()` failure at checkout, if uniqueness is ever checked
   within a single Contact's own rows as well as across Contacts.

Disposition: flagged for whoever owns `custom_webshop`. Not modified by this
plan — the fix belongs in `cart_override.py` itself, e.g. routing both sides
of each comparison through the same normalization (`try_to_e164()`, already
used correctly elsewhere in this same app) before comparing, matching the
pattern `signup/linking.py` already uses correctly.

### `public/js/store.js` — Unaffected

No references to phone/mobile fields at all.

### `patches/canonicalize_webshop_owned_phones.py` — Unaffected, informational

A one-time patch already shipped by `custom_webshop` itself, converting
`User.mobile_no` and `Customer.mobile_no` (fields it owns) to E.164 when
they match a verified webshop-signup identity. Does not touch `Contact
Phone.phone` at all. Confirms `custom_webshop`'s own prior direction is
already E.164-aligned — no new risk, and no action needed here.

### `Contact Phone.custom_phone_e164` — Redundant post-conversion, not broken

A custom field `custom_webshop` added to `Contact Phone` itself, because its
own code previously needed an E.164 value while `phone` stayed local-form.
Once `phone` is always E.164, this column duplicates it. Not broken —
nothing reads it in a way the conversion invalidates — but no longer
necessary. Not modified here; flagged for whoever owns `custom_webshop` to
retire on their own schedule.

## Conclusion

One genuine cross-app risk found (`cart_override.py`'s three direct
string-equality comparisons), fully cataloged above with concrete failure
scenarios, disposition, and a suggested fix shape — left for
`custom_webshop`'s own maintainer per this plan's explicit scope boundary
("this plan does not modify custom_webshop's code or schema"). Every other
hit in the bench is either unaffected or already safe/benefits from the
conversion. Phase 0b's code changes (`normalize_and_validate_contact_phone`
+ `renormalize_contact_phones_to_e164`) are clear to proceed.
