import frappe
from frappe import _
from frappe.model.document import Document

from pharma_manufacturing_mgmt.utils.settings import REQUIRED_WAREHOUSE_FIELDS


class PharmaSettings(Document):
	def validate(self):
		self.validate_no_duplicate_quarantine_warehouses()
		self.warn_if_workflow_enabled_without_warehouses()

	def validate_no_duplicate_quarantine_warehouses(self):
		if (
			self.rm_quarantine_warehouse
			and self.rm_quarantine_warehouse == self.fg_quarantine_warehouse
		):
			frappe.throw(
				_("Quarantine Warehouse {0} is mapped more than once.").format(self.rm_quarantine_warehouse)
			)

	def warn_if_workflow_enabled_without_warehouses(self):
		if not self.enable_quarantine_workflow:
			return

		if not all(self.get(fieldname) for fieldname in REQUIRED_WAREHOUSE_FIELDS):
			frappe.msgprint(
				_("Quarantine workflow is enabled but warehouses are not configured"),
				indicator="orange",
				alert=True,
			)
