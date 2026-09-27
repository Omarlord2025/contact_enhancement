# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

"""Whitelisted API for bidirectional name synchronisation — Customer/Supplier → Contact.

Two endpoints:

  get_linked_contacts_for_name_sync
      Discovers every Contact Dynamic-Linked to a given Customer/Supplier and
      returns the data the name-sync dialog needs (current full_name, all other
      Dynamic Links, and whether the caller may edit each Contact).

  update_contact_names
      Applies a new first_name/full_name to a caller-supplied list of Contacts,
      one by one, with per-Contact permission checks and Arabic normalization.

Both endpoints use frappe.db.set_value(), NOT doc.save(), deliberately.
doc.save() would fire Contact.on_update → propagate_contact_changes_to_linked_doctypes,
which would push the name back to every linked Customer/Supplier — exactly the
P0 loop the implementation plan identifies.  frappe.db.set_value() bypasses all
doc_events; the existing propagation chain fires naturally on the Contact's next
unrelated save instead.  At that point _doc_before_save already reflects the new
full_name so has_value_changed("full_name") returns False — no double-propagation.

Assumption: in this app, Contact.first_name IS the full name (Property Setter
relabels the field as "Full Name").  Middle and last name fields are not used,
so full_name == first_name for every Contact in this system.  Setting both
first_name and full_name to the same value is therefore safe.
"""

import frappe
from frappe import _

from contact_enhancements.contact_hooks import normalize_arabic_first_name

# Doctypes surfaced in the dialog's "Also linked to:" list.
_LINK_DOCTYPES_FOR_DISPLAY = frozenset({"Customer", "Supplier", "Employee", "User", "Lead"})

# Which type value marks a party as an individual person (the only case where
# the existing propagate_contact_changes_to_linked_doctypes will auto-sync the
# Contact's name back on its next save).
_INDIVIDUAL_TYPE_BY_DOCTYPE = {
    "Customer": ("customer_type", "Individual"),
    "Supplier": ("supplier_type", "Individual"),
}

# Human-readable title field per doctype, used to show e.g. the supplier_name
# instead of the raw document name in the dialog.
_TITLE_FIELD = {
    "Customer": "customer_name",
    "Supplier": "supplier_name",
    "Employee": "employee_name",
    "User": "full_name",
    "Lead": "lead_name",
}

# Primary-contact field per doctype — used to mark which Contact is "primary"
# on this party.
_PRIMARY_CONTACT_FIELD = {
    "Customer": "customer_primary_contact",
    "Supplier": "supplier_primary_contact",
    "Employee": "employee_primary_contact",
    "User": "user_primary_contact",
    "Lead": "lead_primary_contact",
}

# Primary-address field per doctype — used for address discovery and syncing
_PRIMARY_ADDRESS_FIELD = {
    "Customer": "customer_primary_address",
    "Supplier": "supplier_primary_address",
    "Employee": "employee_primary_address",
    "Contact": "address",
}


def _get_primary_address_for_record(doctype, docname):
    """Find the primary Address document name associated with a record."""
    if not doctype or not docname:
        return None

    field = _PRIMARY_ADDRESS_FIELD.get(doctype)
    if field and frappe.db.has_column(doctype, field):
        addr = frappe.db.get_value(doctype, docname, field)
        if addr and frappe.db.exists("Address", addr):
            return addr

    # Fallback to Dynamic Link on Address
    links = frappe.get_all(
        "Dynamic Link",
        filters={
            "parenttype": "Address",
            "link_doctype": doctype,
            "link_name": docname,
        },
        fields=["parent"],
        order_by="creation asc",
        limit=1,
    )
    if links and frappe.db.exists("Address", links[0].parent):
        return links[0].parent

    return None


def _get_all_linked_addresses(doctype, docname):
    """Return every Address document linked to a record, as a list of dicts
    with name, address_title, address_line1, and city — enough for the
    name-sync dialog to show them as selectable items the user can rename.

    Collects via two routes so no address is missed:
    1. The doctype's own primary-address field (e.g. customer_primary_address)
       so the "main" address always appears first.
    2. All Dynamic Links where this record is mentioned on an Address parent
       (a record can be linked to multiple addresses).

    Deduplicates by address name so the primary never appears twice.

    Args:
        doctype: the doctype of the record (e.g. "Customer").
        docname: the document name.

    Returns:
        List of dicts: [{"name", "address_title", "address_line1", "city"}, ...]
    """
    if not doctype or not docname:
        return []

    seen = set()
    result = []

    def _add(addr_name):
        if not addr_name or addr_name in seen:
            return
        row = frappe.db.get_value(
            "Address",
            addr_name,
            ["name", "address_title", "address_type", "address_line1", "city"],
            as_dict=True,
        )
        if row:
            seen.add(addr_name)
            result.append(row)

    # Route 1: primary-address field
    primary_field = _PRIMARY_ADDRESS_FIELD.get(doctype)
    if primary_field and frappe.db.has_column(doctype, primary_field):
        _add(frappe.db.get_value(doctype, docname, primary_field))

    # Route 2: all Dynamic Links on Address parents pointing to this record
    dyn_links = frappe.get_all(
        "Dynamic Link",
        filters={
            "parenttype": "Address",
            "link_doctype": doctype,
            "link_name": docname,
        },
        fields=["parent"],
        order_by="creation asc",
    )
    for lnk in dyn_links:
        _add(lnk.parent)

    return result


@frappe.whitelist()
def get_linked_contacts_for_name_sync(doctype, docname):
    """Return every Contact linked to this party (or parties linked to this Contact).

    For each Contact the response includes:
      - contact        – Contact document name
      - full_name      – current displayed name
      - is_primary     – True when it is the party's primary-contact field value
      - can_edit       – True when the caller has write permission on that Contact
      - links          – list of all other Dynamic Links on this Contact that
                         belong to _LINK_DOCTYPES_FOR_DISPLAY, each with:
                         { doctype, name, title, is_individual }

    Returns an empty list for unsupported doctypes or when no Contacts exist.
    """
    if doctype not in ("Customer", "Supplier", "Employee", "Lead", "Contact"):
        return []

    # Read-access check on the record itself before we query anything.
    frappe.has_permission(doctype, "read", doc=docname, throw=True)

    if doctype == "Contact":
        contact_names = [docname]
        primary_contact = docname
    else:
        primary_contact_field = _PRIMARY_CONTACT_FIELD.get(doctype)
        primary_contact = (
            frappe.db.get_value(doctype, docname, primary_contact_field)
            if primary_contact_field
            else None
        )

        # All Contacts linked to this party via Dynamic Link.
        rows = frappe.get_all(
            "Dynamic Link",
            filters={
                "parenttype": "Contact",
                "link_doctype": doctype,
                "link_name": docname,
            },
            fields=["parent"],
            distinct=True,
        )
        contact_names = list({row.parent for row in rows})
        if primary_contact and primary_contact not in contact_names:
            if frappe.db.exists("Contact", primary_contact):
                contact_names.append(primary_contact)

        if not contact_names:
            source_linked_addresses = _get_all_linked_addresses(doctype, docname)
            if not source_linked_addresses:
                return []
            source_address = _get_primary_address_for_record(doctype, docname)
            source_address_title = None
            if source_address:
                source_address_title = (
                    frappe.db.get_value("Address", source_address, "address_title")
                    or frappe.db.get_value("Address", source_address, "address_line1")
                    or source_address
                )
            return [
                {
                    "contact": None,
                    "full_name": None,
                    "is_primary": False,
                    "can_edit": False,
                    "links": [],
                    "source_address": source_address,
                    "source_address_title": source_address_title,
                    "linked_addresses": source_linked_addresses,
                }
            ]

    # Batch: current full_name for each Contact.
    contact_records = frappe.get_all(
        "Contact",
        filters={"name": ["in", contact_names]},
        fields=["name", "full_name"],
    )
    full_name_by_contact = {r.name: r.full_name for r in contact_records}

    # Batch: all Dynamic Links for these Contacts, restricted to the doctypes
    # we want to display — minus the originating party itself (redundant in
    # context, and always present, which would pollute the "also linked to" list).
    all_links = frappe.get_all(
        "Dynamic Link",
        filters={
            "parenttype": "Contact",
            "parent": ["in", contact_names],
            "link_doctype": ["in", list(_LINK_DOCTYPES_FOR_DISPLAY)],
        },
        fields=["parent", "link_doctype", "link_name"],
    )

    # Filter out the originating party from all rows at once.
    all_links = [
        lnk
        for lnk in all_links
        if not (lnk.link_doctype == doctype and lnk.link_name == docname)
    ]

    # Collect all (doctype, name) pairs needing title/type lookups — one batch
    # query per doctype avoids N+1.
    linked_by_dt: dict[str, set] = {}
    for lnk in all_links:
        linked_by_dt.setdefault(lnk.link_doctype, set()).add(lnk.link_name)

    titles: dict[tuple, str] = {}           # (doctype, name) → display title
    type_values: dict[tuple, str] = {}      # (doctype, name) → type field value

    for dt, names in linked_by_dt.items():
        title_field = _TITLE_FIELD.get(dt)
        type_info = _INDIVIDUAL_TYPE_BY_DOCTYPE.get(dt)

        fields = ["name"]
        if title_field:
            fields.append(title_field)
        if type_info:
            fields.append(type_info[0])

        for rec in frappe.get_all(dt, filters={"name": ["in", list(names)]}, fields=fields):
            key = (dt, rec.name)
            titles[key] = rec.get(title_field) if title_field else rec.name
            if type_info:
                type_values[key] = rec.get(type_info[0])

    # Build: contact → list-of-link-dicts.
    links_by_contact: dict[str, list] = {name: [] for name in contact_names}
    for lnk in all_links:
        key = (lnk.link_doctype, lnk.link_name)
        type_info = _INDIVIDUAL_TYPE_BY_DOCTYPE.get(lnk.link_doctype)
        if type_info:
            # Customer/Supplier: individual only when type field == "Individual"
            is_individual = type_values.get(key) == type_info[1]
        else:
            # Employee/User are always individuals (no type gate in _CONTACT_SYNC_TARGETS)
            is_individual = True

        can_edit_link = frappe.has_permission(lnk.link_doctype, "write", doc=lnk.link_name, throw=False)
        links_by_contact[lnk.parent].append(
            {
                "doctype": lnk.link_doctype,
                "name": lnk.link_name,
                "title": titles.get(key, lnk.link_name),
                "is_individual": is_individual,
                "can_edit": bool(can_edit_link),
            }
        )

    source_address = _get_primary_address_for_record(doctype, docname)
    if not source_address and doctype != "Contact" and primary_contact:
        source_address = _get_primary_address_for_record("Contact", primary_contact)

    # When called from a Contact form (doctype="Contact"), also search the
    # Contact's own linked records (Customer/Supplier/Employee) for an address -
    # the most common production case: a Contact has no dedicated address field
    # value, but its linked Customer does. Without this, source_address was always
    # empty when the dialog was opened from a Contact, the sync checkbox never
    # appeared, and address propagation was impossible from that entry point.
    if not source_address and all_links:
        for lnk in all_links:
            source_address = _get_primary_address_for_record(lnk.link_doctype, lnk.link_name)
            if source_address:
                break

    source_address_title = None
    if source_address:
        source_address_title = (
            frappe.db.get_value("Address", source_address, "address_title")
            or frappe.db.get_value("Address", source_address, "address_line1")
            or source_address
        )

    # Gather all addresses linked to the source record so the dialog can
    # offer the user the option to rename their address_title as well.
    source_linked_addresses = _get_all_linked_addresses(doctype, docname)

    # When called from Contact, also collect addresses from linked records.
    if not source_linked_addresses and all_links:
        for lnk in all_links:
            addrs = _get_all_linked_addresses(lnk.link_doctype, lnk.link_name)
            for a in addrs:
                if not any(x["name"] == a["name"] for x in source_linked_addresses):
                    source_linked_addresses.append(a)

    result = []
    for contact_name in contact_names:
        can_edit = frappe.has_permission("Contact", "write", doc=contact_name, throw=False)
        result.append(
            {
                "contact": contact_name,
                "full_name": full_name_by_contact.get(contact_name, contact_name),
                "is_primary": contact_name == primary_contact,
                "can_edit": bool(can_edit),
                "links": links_by_contact.get(contact_name, []),
                "source_address": source_address,
                "source_address_title": source_address_title,
                "linked_addresses": source_linked_addresses,
            }
        )

    # Primary contact first, then by full_name for a stable, predictable order.
    result.sort(key=lambda r: (0 if r["is_primary"] else 1, (r["full_name"] or "").lower()))
    return result


@frappe.whitelist()
def update_contact_names(
    contacts=None,
    new_name=None,
    linked_records=None,
    linked_addresses=None,
    sync_address=True,
    source_address=None,
    source_doctype=None,
    source_name=None,
):
    """Write new_name to first_name/full_name on Contacts, name field on linked
    records, and optionally address_title on selected linked addresses.

    Uses frappe.db.set_value() — see this module's own docstring for why
    doc.save() is deliberately avoided.

    Applies normalize_arabic_first_name() before writing, matching the behavior
    of contact_hooks.normalize_contact_first_name (the Contact validate hook),
    which is bypassed here since we're not going through the full save lifecycle.

    Args:
        contacts: list (or JSON string) of Contact document names.
        new_name:  the new full_name / first_name string.
        linked_records: list (or JSON string) of dicts {doctype, name}.
        linked_addresses: list (or JSON string) of Address document names whose
            address_title should be updated to the new normalized name.
        sync_address: bool (default True) to copy primary address to blank targets.
        source_address: Address document name to sync.
        source_doctype: Originating doctype (e.g. "Customer").
        source_name: Originating docname.

    Returns:
        list of { "contact": str, "doctype": str, "name": str, "status": "updated"|"no_permission"|"failed" }
    """
    from frappe.utils import cint
    from contact_enhancements.utils import _insert_dynamic_link

    if not new_name or not new_name.strip():
        frappe.throw(_("A name is required."))

    normalized = normalize_arabic_first_name(new_name.strip())
    if not normalized:
        frappe.throw(_("The name is empty after normalization."))

    if isinstance(contacts, str):
        import json as _json

        contacts = _json.loads(contacts)

    if isinstance(linked_records, str):
        import json as _json

        linked_records = _json.loads(linked_records)

    if isinstance(linked_addresses, str):
        import json as _json

        linked_addresses = _json.loads(linked_addresses)

    contacts = contacts or []
    linked_records = linked_records or []
    linked_addresses = linked_addresses or []

    if not contacts and not linked_records and not linked_addresses:
        return []

    results = []

    # 1. Update Contact documents
    for contact_name in contacts:
        if not frappe.has_permission("Contact", "write", doc=contact_name, throw=False):
            results.append({
                "contact": contact_name,
                "doctype": "Contact",
                "name": contact_name,
                "status": "no_permission",
            })
            continue

        try:
            frappe.db.set_value(
                "Contact",
                contact_name,
                {
                    "first_name": normalized,
                    "full_name": normalized,
                },
                update_modified=True,
            )
            # Invalidate the document cache so any subsequent frappe.get_doc() sees new values.
            frappe.clear_document_cache("Contact", contact_name)
            results.append({
                "contact": contact_name,
                "doctype": "Contact",
                "name": contact_name,
                "status": "updated",
            })
        except Exception:
            frappe.log_error(
                title="contact_enhancements: name_sync update Contact failed",
                message=(
                    f"Tried to update Contact {contact_name!r} "
                    f"(first_name/full_name → {normalized!r}) but got an exception."
                ),
            )
            results.append({
                "contact": contact_name,
                "doctype": "Contact",
                "name": contact_name,
                "status": "failed",
            })

    # 2. Update linked records (Supplier, Employee, User, Customer)
    for rec in linked_records:
        dt = rec.get("doctype")
        dn = rec.get("name")
        if not dt or not dn:
            continue

        if not frappe.has_permission(dt, "write", doc=dn, throw=False):
            results.append({"doctype": dt, "name": dn, "status": "no_permission"})
            continue

        field_updates = {}
        if dt == "Customer":
            field_updates["customer_name"] = normalized
        elif dt == "Supplier":
            field_updates["supplier_name"] = normalized
        elif dt == "Employee":
            field_updates["first_name"] = normalized
            if frappe.db.has_column("Employee", "employee_name"):
                field_updates["employee_name"] = normalized
        elif dt == "User":
            field_updates["first_name"] = normalized
        elif dt == "Lead":
            field_updates["first_name"] = normalized
            if frappe.db.has_column("Lead", "lead_name"):
                field_updates["lead_name"] = normalized

        if not field_updates:
            continue

        try:
            frappe.db.set_value(dt, dn, field_updates, update_modified=True)
            frappe.clear_document_cache(dt, dn)
            results.append({"doctype": dt, "name": dn, "status": "updated"})
        except Exception:
            frappe.log_error(
                title=f"contact_enhancements: name_sync update {dt} failed",
                message=f"Tried to update {dt} {dn!r} with {field_updates!r}",
            )
            results.append({"doctype": dt, "name": dn, "status": "failed"})

    # 3. Update address_title on selected addresses
    for addr_name in linked_addresses:
        if not frappe.db.exists("Address", addr_name):
            results.append({"doctype": "Address", "name": addr_name, "status": "failed"})
            continue

        if not frappe.has_permission("Address", "write", doc=addr_name, throw=False):
            results.append({"doctype": "Address", "name": addr_name, "status": "no_permission"})
            continue

        try:
            frappe.db.set_value("Address", addr_name, "address_title", normalized, update_modified=True)
            frappe.clear_document_cache("Address", addr_name)
            results.append({"doctype": "Address", "name": addr_name, "status": "updated"})
        except Exception:
            frappe.log_error(
                title="contact_enhancements: name_sync update Address title failed",
                message=f"Tried to update Address {addr_name!r} address_title → {normalized!r}",
            )
            results.append({"doctype": "Address", "name": addr_name, "status": "failed"})

    # 4. Propagate Address link if enabled
    do_sync_address = cint(sync_address) if sync_address is not None else 1
    if do_sync_address:
        # Discover source address if not passed
        if not source_address and source_doctype and source_name:
            source_address = _get_primary_address_for_record(source_doctype, source_name)

        if not source_address:
            for c in contacts:
                source_address = _get_primary_address_for_record("Contact", c)
                if source_address:
                    break

        if not source_address:
            for rec in linked_records:
                source_address = _get_primary_address_for_record(rec.get("doctype"), rec.get("name"))
                if source_address:
                    break

        if source_address and frappe.db.exists("Address", source_address):
            addr_display = None
            try:
                from frappe.contacts.doctype.address.address import get_address_display

                addr_display = get_address_display(source_address)
            except Exception:
                pass

            # Sync address to Contacts
            for contact_name in contacts:
                if not frappe.has_permission("Contact", "write", doc=contact_name, throw=False):
                    continue
                if frappe.db.has_column("Contact", "address"):
                    curr_addr = frappe.db.get_value("Contact", contact_name, "address")
                    if not curr_addr:
                        frappe.db.set_value(
                            "Contact", contact_name, "address", source_address, update_modified=False
                        )
                        frappe.clear_document_cache("Contact", contact_name)

                has_link = frappe.db.exists(
                    "Dynamic Link",
                    {
                        "parenttype": "Address",
                        "parent": source_address,
                        "link_doctype": "Contact",
                        "link_name": contact_name,
                    },
                )
                if not has_link:
                    _insert_dynamic_link("Address", source_address, "Contact", contact_name)

            # Sync address to linked records
            for rec in linked_records:
                dt = rec.get("doctype")
                dn = rec.get("name")
                if not dt or not dn or not frappe.has_permission(dt, "write", doc=dn, throw=False):
                    continue

                addr_field = _PRIMARY_ADDRESS_FIELD.get(dt)
                if addr_field and frappe.db.has_column(dt, addr_field):
                    curr_addr = frappe.db.get_value(dt, dn, addr_field)
                    if not curr_addr:
                        updates = {addr_field: source_address}
                        if addr_display and frappe.get_meta(dt).has_field("primary_address"):
                            updates["primary_address"] = addr_display
                        frappe.db.set_value(dt, dn, updates, update_modified=False)
                        frappe.clear_document_cache(dt, dn)

                has_link = frappe.db.exists(
                    "Dynamic Link",
                    {
                        "parenttype": "Address",
                        "parent": source_address,
                        "link_doctype": dt,
                        "link_name": dn,
                    },
                )
                if not has_link:
                    _insert_dynamic_link("Address", source_address, dt, dn)

            # Also ensure source document has address linked if it was blank
            if (
                source_doctype
                and source_name
                and frappe.has_permission(source_doctype, "write", doc=source_name, throw=False)
            ):
                src_addr_field = _PRIMARY_ADDRESS_FIELD.get(source_doctype)
                if src_addr_field and frappe.db.has_column(source_doctype, src_addr_field):
                    curr_addr = frappe.db.get_value(source_doctype, source_name, src_addr_field)
                    if not curr_addr:
                        updates = {src_addr_field: source_address}
                        if addr_display and frappe.get_meta(source_doctype).has_field("primary_address"):
                            updates["primary_address"] = addr_display
                        frappe.db.set_value(source_doctype, source_name, updates, update_modified=False)
                        frappe.clear_document_cache(source_doctype, source_name)

                has_src_link = frappe.db.exists(
                    "Dynamic Link",
                    {
                        "parenttype": "Address",
                        "parent": source_address,
                        "link_doctype": source_doctype,
                        "link_name": source_name,
                    },
                )
                if not has_src_link:
                    _insert_dynamic_link("Address", source_address, source_doctype, source_name)

    return results
