import frappe
from frappe import _

from pharma_manufacturing_mgmt.utils.batch_tools import (
	QC_STATUS_APPROVED,
	get_batch_status,
	get_row_batches,
)
from pharma_manufacturing_mgmt.utils.settings import (
	get_pharma_settings,
	get_quarantine_warehouses,
	get_release_role,
	has_configured_quarantine_warehouses,
	is_item_in_scope,
	is_workflow_enabled,
	should_restrict_quarantine_transfers,
)


CONSUMPTION_PURPOSES = {"Material Transfer for Manufacture", "Manufacture", "Material Issue"}
LOGGER = frappe.logger("pharma_qc")


def validate_stock_entry(doc, method=None):
	LOGGER.info("Stock Entry validate hook started for %s", doc.name)
	settings = get_pharma_settings()
	if not is_workflow_enabled(settings):
		LOGGER.info("Stock Entry %s validation skipped because workflow is disabled", doc.name)
		return

	if not has_configured_quarantine_warehouses(settings):
		LOGGER.info("Stock Entry %s validation skipped because quarantine warehouses are not configured", doc.name)
		return

	validate_batch_consumption(doc, settings=settings)
	validate_quarantine_escape(doc, settings=settings)


def validate_batch_consumption(doc, method=None, settings=None):
	settings = settings or get_pharma_settings()
	if (
		not is_workflow_enabled(settings)
		or not has_configured_quarantine_warehouses(settings)
		or doc.purpose not in CONSUMPTION_PURPOSES
	):
		return

	for row in doc.get("items") or []:
		if not _is_consumed_row(doc, row) or not is_item_in_scope(row.item_code):
			continue

		for batch in get_row_batches(row):
			status = get_batch_status(batch.batch_no)
			if status == QC_STATUS_APPROVED:
				continue

			frappe.throw(
				_(
					"Batch {0} of item {1} is not QC-approved. Current status: {2}. It cannot be consumed."
				).format(batch.batch_no, row.item_code, status or _("Not Set"))
			)


def validate_quarantine_escape(doc, method=None, settings=None):
	settings = settings or get_pharma_settings()
	if (
		not is_workflow_enabled(settings)
		or not has_configured_quarantine_warehouses(settings)
		or not should_restrict_quarantine_transfers(settings)
	):
		return

	if doc.get("custom_pharma_release_ref"):
		LOGGER.info("Stock Entry %s quarantine escape allowed by release reference", doc.name)
		return

	role = get_release_role(settings)
	if role and role in frappe.get_roles(frappe.session.user):
		LOGGER.info("Stock Entry %s quarantine escape allowed by role %s", doc.name, role)
		return

	quarantine_warehouses = set(get_quarantine_warehouses(settings))
	for row in doc.get("items") or []:
		if not row.s_warehouse or row.s_warehouse not in quarantine_warehouses:
			continue

		if not is_item_in_scope(row.item_code):
			continue

		batches = get_row_batches(row) or [frappe._dict({"batch_no": _("Unspecified")})]
		for batch in batches:
			frappe.throw(
				_(
					"Manual transfer out of quarantine warehouse {0} for item {1}, batch {2} is blocked because QC release/rejection must be system-generated or approved by the QA release role."
				).format(row.s_warehouse, row.item_code, batch.batch_no)
			)


def _is_consumed_row(doc, row) -> bool:
	if not row.s_warehouse:
		return False

	if doc.purpose == "Manufacture" and row.get("is_finished_item"):
		return False

	return True
