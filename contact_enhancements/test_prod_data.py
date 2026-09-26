import frappe
import csv
import traceback

def run_test():
    csv_path = "/home/erpnext/.gemini/antigravity-ide/brain/fcedbc10-81d2-49e5-96b5-028cd95a20eb/scratch/prod_contacts.csv"
    
    contacts = {}
    current_id = None
    
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            row_id = row.get('ID')
            if row_id:
                current_id = row_id
                if current_id not in contacts:
                    contacts[current_id] = {
                        'first_name': row.get('First Name'),
                        'phone_nos': [],
                        'links': []
                    }
            
            if not current_id:
                continue
                
            # Add phone if present
            phone = row.get('Number (Contact Numbers)')
            if phone:
                is_primary = row.get('Is Primary Mobile (Contact Numbers)')
                is_primary = int(is_primary) if is_primary and is_primary.isdigit() else 0
                
                if not any(p['phone'] == phone for p in contacts[current_id]['phone_nos']):
                    contacts[current_id]['phone_nos'].append({
                        'phone': phone,
                        'is_primary_mobile_no': is_primary
                    })
                    
            # Add link if present
            link_dt = row.get('Link Document Type (Links)')
            link_name = row.get('Link Name (Links)')
            if link_dt and link_name:
                if not any(l['link_doctype'] == link_dt and l['link_name'] == link_name for l in contacts[current_id]['links']):
                    contacts[current_id]['links'].append({
                        'link_doctype': link_dt,
                        'link_name': link_name
                    })

    total_contacts = len(contacts)
    success = 0
    duplicate_errors = []
    validation_errors = []
    
    print(f"Starting test for {total_contacts} Contacts from production data...")
    print("-" * 60)
    
    # We will test each contact in its own savepoint, then rollback
    # Actually, it's safer to rollback the whole transaction after testing each one, 
    # so we don't hit duplicate keys against other test contacts!
    # Wait, if we rollback after EACH contact, we won't detect if the production data 
    # has duplicates *within* itself (which is exactly what we want to test!).
    # So we should insert them all in one transaction, catch errors, and rollback at the very end.
    
    frappe.db.begin()
    
    for cid, data in contacts.items():
        try:
            sp = "savepoint_contact_insert"
            frappe.db.savepoint(sp)
            
            doc = frappe.new_doc('Contact')
            doc.first_name = data['first_name'] or "Unknown"
            
            for p in data['phone_nos']:
                doc.append('phone_nos', {
                    'phone': p['phone'],
                    'is_primary_mobile_no': p['is_primary_mobile_no'],
                    'country': 'Egypt'
                })
                
            for l in data['links']:
                doc.append('links', {
                    'link_doctype': l['link_doctype'],
                    'link_name': l['link_name']
                })
            
            doc.insert(ignore_permissions=True)
            success += 1
            
        except frappe.UniqueValidationError as e:
            frappe.db.rollback(save_point=sp)
            duplicate_errors.append((cid, str(e)))
        except frappe.ValidationError as e:
            frappe.db.rollback(save_point=sp)
            validation_errors.append((cid, str(e)))
        except Exception as e:
            frappe.db.rollback(save_point=sp)
            validation_errors.append((cid, traceback.format_exc()))
            
    # Always rollback the entire test transaction so we don't save test data
    frappe.db.rollback()
    
    print(f"\nRESULTS SUMMARY")
    print(f"Total Processed : {total_contacts}")
    print(f"Successful      : {success}")
    print(f"Duplicates found: {len(duplicate_errors)}")
    print(f"Other Errors    : {len(validation_errors)}")
    
    if duplicate_errors:
        print("\n--- DUPLICATE MOBILE NUMBERS ---")
        for cid, err in duplicate_errors[:15]:
            print(f"Contact {cid}: {err}")
        if len(duplicate_errors) > 15:
            print(f"... and {len(duplicate_errors) - 15} more.")
            
    if validation_errors:
        print("\n--- VALIDATION ERRORS ---")
        for cid, err in validation_errors[:15]:
            print(f"Contact {cid}: {err.strip()}")
        if len(validation_errors) > 15:
            print(f"... and {len(validation_errors) - 15} more.")
