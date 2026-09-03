app_name = "contact_enhancements"
app_title = "Contact Enhancements"
app_publisher = "Omar Sabry"
app_description = "WhatsApp/Telegram/Landline channel flags on Contact phone numbers, and a Lead-match lookup when creating a new Contact"
app_email = "omarelgewily2011@gmail.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "contact_enhancements",
# 		"logo": "/assets/contact_enhancements/logo.png",
# 		"title": "Contact Enhancements",
# 		"route": "/contact_enhancements",
# 		"has_permission": "contact_enhancements.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/contact_enhancements/css/contact_enhancements.css"
# contact_picker_dialog.js / linked_addresses.js are shared components
# more than one doctype's own doctype_js needs (Customer, Supplier,
# Contact, User, ...) - doctype_js only binds one file to one doctype, so
# these have to be genuine app_include_js entries instead, loaded on
# every Desk page.
app_include_js = [
	"/assets/contact_enhancements/js/contact_picker_dialog.js",
	"/assets/contact_enhancements/js/linked_addresses.js",
]

# include js, css files in header of web template
# web_include_css = "/assets/contact_enhancements/css/contact_enhancements.css"
# web_include_js = "/assets/contact_enhancements/js/contact_enhancements.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "contact_enhancements/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
doctype_js = {
	"Customer": "public/js/customer.js",
	"Contact": "public/js/contact.js",
	"Supplier": "public/js/supplier.js",
	"Employee": "public/js/employee.js",
	"User": "public/js/user.js",
}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "contact_enhancements/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "contact_enhancements.utils.jinja_methods",
# 	"filters": "contact_enhancements.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "contact_enhancements.install.before_install"
after_install = "contact_enhancements.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "contact_enhancements.uninstall.before_uninstall"
# after_uninstall = "contact_enhancements.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "contact_enhancements.utils.before_app_install"
# after_app_install = "contact_enhancements.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "contact_enhancements.utils.before_app_uninstall"
# after_app_uninstall = "contact_enhancements.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "contact_enhancements.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"Customer": {
		# before_naming, not validate: ERPNext's own Customer.autoname()
		# reads customer_name, and naming runs before validate - so a
		# validate-time backfill is too late on an insert.
		"before_naming": "contact_enhancements.customer_hooks.apply_contact_identity_before_naming",
		# enforce_* runs last: sync_* may still resolve a contact/address
		# from a Lead conversion, and that must happen before anything
		# reports them missing.
		"validate": [
			"contact_enhancements.customer_hooks.sync_customer_from_primary_contact",
			"contact_enhancements.customer_hooks.enforce_primary_contact_and_address_on_new_customer",
		],
		"on_update": "contact_enhancements.customer_hooks.link_primary_contact",
	},
	"Supplier": {
		"validate": [
			"contact_enhancements.supplier_hooks.backfill_supplier_primary_contact_from_dynamic_link",
			"contact_enhancements.supplier_hooks.backfill_supplier_name_from_primary_contact",
			"contact_enhancements.supplier_hooks.sync_supplier_address_from_contact_links",
			# Last, after the backfill above has had its chance.
			"contact_enhancements.supplier_hooks.enforce_primary_contact_on_new_supplier",
		],
		"on_update": "contact_enhancements.supplier_hooks.link_primary_contact",
	},
	"Employee": {
		"validate": [
			"contact_enhancements.employee_hooks.sync_employee_contact_from_user",
			"contact_enhancements.employee_hooks.backfill_employee_name_from_primary_contact",
			"contact_enhancements.employee_hooks.sync_employee_address_from_contact_links",
			"contact_enhancements.employee_hooks.enforce_full_name_has_at_least_three_words",
		],
		"on_update": "contact_enhancements.employee_hooks.link_employee_contact",
	},
	"Lead": {
		"on_update": "contact_enhancements.lead_hooks.sync_lead_address_from_contact_links",
	},
	"Contact": {
		"validate": [
			"contact_enhancements.contact_hooks.normalize_contact_first_name",
			"contact_enhancements.contact_hooks.enforce_contact_phone_channel_exclusivity",
			"contact_enhancements.contact_hooks.normalize_and_validate_contact_phones",
			"contact_enhancements.contact_hooks.enforce_unique_mobile_number",
			"contact_enhancements.api.contact_dedupe.warn_if_duplicate_contact",
		],
		"on_update": "contact_enhancements.contact_hooks.propagate_contact_changes_to_linked_doctypes",
	},
	"User": {
		"validate": [
			"contact_enhancements.user_hooks.validate_user_phone_before_contact_sync",
			"contact_enhancements.user_hooks.backfill_user_name_from_primary_contact",
		],
		"on_update": "contact_enhancements.user_hooks.link_user_contact",
	},
}

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"contact_enhancements.tasks.all"
# 	],
# 	"daily": [
# 		"contact_enhancements.tasks.daily"
# 	],
# 	"hourly": [
# 		"contact_enhancements.tasks.hourly"
# 	],
# 	"weekly": [
# 		"contact_enhancements.tasks.weekly"
# 	],
# 	"monthly": [
# 		"contact_enhancements.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "contact_enhancements.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "contact_enhancements.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "contact_enhancements.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["contact_enhancements.utils.before_request"]
# after_request = ["contact_enhancements.utils.after_request"]

# Job Events
# ----------
# before_job = ["contact_enhancements.utils.before_job"]
# after_job = ["contact_enhancements.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"contact_enhancements.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

