import frappe
from frappe import _

from pharma_manufacturing_mgmt.utils.settings import (
	get_rm_quarantine_warehouse,
	get_fg_quarantine_warehouse,
	is_item_in_scope,
	workflow_enabled_for_doc,
)

LOGGER = frappe.logger("pharma_qc")


def warn_update_stock(doc, method=None):
	LOGGER.info("Purchase Invoice validate hook started for %s", doc.name)
	if not workflow_enabled_for_doc() or not doc.get("update_stock"):
		LOGGER.info("Purchase Invoice %s skipped because workflow is disabled or update_stock is off", doc.name)
		return

	for row in doc.get("items"):
		if is_item_in_scope(row.item_code):
			LOGGER.info("Purchase Invoice %s warning shown for in-scope item %s", doc.name, row.item_code)
			frappe.msgprint(
				_(
					"Pharma quarantine workflow recommends receiving this item through Purchase Receipt into {0} or {1}."
				).format(get_rm_quarantine_warehouse(), get_fg_quarantine_warehouse()),
				alert=True,
			)
			return

		LOGGER.info("Purchase Invoice %s row %s item %s skipped as out of scope", doc.name, row.idx, row.item_code)
