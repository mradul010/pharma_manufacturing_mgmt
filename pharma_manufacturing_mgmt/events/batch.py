import frappe
from frappe import _

from pharma_manufacturing_mgmt.utils.batch_tools import (
	QC_STATUS_QUARANTINE,
	set_batch_qc_status,
)
from pharma_manufacturing_mgmt.utils.settings import (
	is_item_in_scope,
	quarantine_workflow_active_for_doc,
)

LOGGER = frappe.logger("pharma_qc")


def set_default_qc_status(doc, method=None):
	LOGGER.info("Batch hook started for %s", doc.name)
	if not quarantine_workflow_active_for_doc():
		LOGGER.info(
			"Batch %s skipped because pharma quarantine workflow is disabled or warehouses are not configured",
			doc.name,
		)
		return

	if not doc.item or not is_item_in_scope(doc.item) or doc.get("custom_qc_status"):
		LOGGER.info("Batch %s skipped as item %s is out of scope or status already set", doc.name, doc.item)
		return

	set_batch_qc_status(
		doc.name,
		QC_STATUS_QUARANTINE,
		_("QC status set to Quarantine automatically on batch creation."),
	)
	LOGGER.info("Batch %s status changed to Quarantine on creation", doc.name)
