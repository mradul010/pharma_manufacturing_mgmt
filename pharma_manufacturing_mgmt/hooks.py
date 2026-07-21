app_name = "pharma_manufacturing_mgmt"
app_title = "Vigisolvo Pharma Mgmt"
app_publisher = "vigisolvo"
app_description = "Pharma mgmt app"
app_email = "admin@gmail.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "pharma_manufacturing_mgmt",
# 		"logo": "/assets/pharma_manufacturing_mgmt/logo.png",
# 		"title": "Vigisolvo Pharma Mgmt",
# 		"route": "/pharma_manufacturing_mgmt",
# 		"has_permission": "pharma_manufacturing_mgmt.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/pharma_manufacturing_mgmt/css/pharma_manufacturing_mgmt.css"
# app_include_js = "/assets/pharma_manufacturing_mgmt/js/pharma_manufacturing_mgmt.js"

# include js, css files in header of web template
# web_include_css = "/assets/pharma_manufacturing_mgmt/css/pharma_manufacturing_mgmt.css"
# web_include_js = "/assets/pharma_manufacturing_mgmt/js/pharma_manufacturing_mgmt.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "pharma_manufacturing_mgmt/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
doctype_js = {
	"Delivery Note": "public/js/delivery_note.js",
}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "pharma_manufacturing_mgmt/public/icons.svg"

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

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "pharma_manufacturing_mgmt.utils.jinja_methods",
# 	"filters": "pharma_manufacturing_mgmt.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "pharma_manufacturing_mgmt.install.before_install"
# after_install = "pharma_manufacturing_mgmt.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "pharma_manufacturing_mgmt.uninstall.before_uninstall"
# after_uninstall = "pharma_manufacturing_mgmt.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "pharma_manufacturing_mgmt.utils.before_app_install"
# after_app_install = "pharma_manufacturing_mgmt.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "pharma_manufacturing_mgmt.utils.before_app_uninstall"
# after_app_uninstall = "pharma_manufacturing_mgmt.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "pharma_manufacturing_mgmt.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "pharma_manufacturing_mgmt.notifications.get_notification_config"

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

# Overriding Doctype Classes
# --------------------------

override_doctype_class = {
	"Quality Inspection": "pharma_manufacturing_mgmt.overrides.quality_inspection.PharmaQualityInspection"
}

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"Batch": {
		"after_insert": "pharma_manufacturing_mgmt.events.batch.set_default_qc_status",
	},
	"Work Order": {
		"on_submit": "pharma_manufacturing_mgmt.pharma_manufacturing_mgmt.doctype.batch_manufacturing_record.batch_manufacturing_record.create_bmr_from_work_order",
	},
	"Purchase Receipt": {
		"validate": "pharma_manufacturing_mgmt.events.purchase_receipt.validate_quarantine_receipt",
		"on_submit": "pharma_manufacturing_mgmt.events.purchase_receipt.on_submit",
		"on_cancel": "pharma_manufacturing_mgmt.events.purchase_receipt.on_cancel",
	},
	"Purchase Invoice": {
		"validate": "pharma_manufacturing_mgmt.events.purchase_invoice.warn_update_stock",
	},
	"Quality Inspection": {
		"before_insert": "pharma_manufacturing_mgmt.events.quality_inspection.set_ar_number",
		"on_update": "pharma_manufacturing_mgmt.events.quality_inspection.set_under_test",
		"on_submit": "pharma_manufacturing_mgmt.events.quality_inspection.on_submit",
		"on_cancel": "pharma_manufacturing_mgmt.events.quality_inspection.on_cancel",
	},
	"Stock Entry": {
		"validate": "pharma_manufacturing_mgmt.events.stock_entry.validate",
		"on_submit": "pharma_manufacturing_mgmt.events.stock_entry.on_submit",
	},
	"Delivery Note": {
		"validate": "pharma_manufacturing_mgmt.utils.gates.validate_outbound",
		"on_submit": "pharma_manufacturing_mgmt.utils.gates.on_outbound_submit",
	},
	"Sales Invoice": {
		"validate": "pharma_manufacturing_mgmt.utils.gates.validate_outbound",
		"on_submit": "pharma_manufacturing_mgmt.utils.gates.on_outbound_submit",
	},
}

fixtures = [
	{"dt": "Custom Field", "filters": [["module", "=", "Pharma QC"]]},
	{"dt": "Role", "filters": [["name", "=", "Pharma QA"]]},
]

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"pharma_manufacturing_mgmt.tasks.all"
# 	],
# 	"daily": [
# 		"pharma_manufacturing_mgmt.tasks.daily"
# 	],
# 	"hourly": [
# 		"pharma_manufacturing_mgmt.tasks.hourly"
# 	],
# 	"weekly": [
# 		"pharma_manufacturing_mgmt.tasks.weekly"
# 	],
# 	"monthly": [
# 		"pharma_manufacturing_mgmt.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "pharma_manufacturing_mgmt.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "pharma_manufacturing_mgmt.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "pharma_manufacturing_mgmt.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "pharma_manufacturing_mgmt.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["pharma_manufacturing_mgmt.utils.before_request"]
# after_request = ["pharma_manufacturing_mgmt.utils.after_request"]

# Job Events
# ----------
# before_job = ["pharma_manufacturing_mgmt.utils.before_job"]
# after_job = ["pharma_manufacturing_mgmt.utils.after_job"]

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
# 	"pharma_manufacturing_mgmt.auth.validate"
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
