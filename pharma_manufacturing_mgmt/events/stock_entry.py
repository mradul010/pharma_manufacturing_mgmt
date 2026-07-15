import frappe
from frappe import _

from pharma_manufacturing_mgmt.utils.gates import validate_stock_entry
from pharma_manufacturing_mgmt.utils.batch_tools import (
	QC_STATUS_QUARANTINE,
	get_row_batches,
	set_batch_qc_status,
)
from pharma_manufacturing_mgmt.utils.quality_inspection_tools import create_draft_quality_inspection
from pharma_manufacturing_mgmt.utils.settings import (
	get_fg_quarantine_warehouse,
	get_pharma_settings,
	has_configured_quarantine_warehouses,
	is_item_in_scope,
	is_workflow_enabled,
	should_auto_create_qi,
)


LOGGER = frappe.logger("pharma_qc")


def validate(doc, method=None):
	validate_stock_entry(doc, method=method)


def on_submit(doc, method=None):
	LOGGER.info("Stock Entry on_submit hook started for %s", doc.name)
	settings = get_pharma_settings()
	if not is_workflow_enabled(settings) or not _is_manufacture_entry(doc):
		return

	if not has_configured_quarantine_warehouses(settings):
		LOGGER.info("Stock Entry %s submit skipped because quarantine warehouses are not configured", doc.name)
		return

	fg_quarantine_warehouse = get_fg_quarantine_warehouse(settings)
	finished_item = _get_finished_item(doc)

	for row in doc.get("items") or []:
		if not _is_finished_goods_output_row(row, fg_quarantine_warehouse, finished_item):
			continue

		if not is_item_in_scope(row.item_code):
			continue

		batches = get_row_batches(row)
		if not batches:
			frappe.throw(
				_("Row {0}: No finished goods batch could be resolved for item {1}.").format(
					row.idx, row.item_code
				)
			)

		for batch in batches:
			set_batch_qc_status(
				batch.batch_no,
				QC_STATUS_QUARANTINE,
				_(
					"Finished Goods received via Manufacture Stock Entry {0}; status set to Quarantine."
				).format(doc.name),
			)

			if should_auto_create_qi(settings):
				# Stock Entry manufacturing QC is part of the production process, so ERPNext's
				# valid "In Process" inspection type is used for finished goods quarantine.
				create_draft_quality_inspection(
					reference_type="Stock Entry",
					reference_name=doc.name,
					item_code=row.item_code,
					batch_no=batch.batch_no,
					inspection_type="In Process",
					company=doc.company,
					sample_size=1,
					child_row_reference=row.name,
					comment_reference=doc,
				)


def _is_manufacture_entry(doc) -> bool:
	return doc.purpose == "Manufacture" or doc.stock_entry_type == "Manufacture"


def _is_finished_goods_output_row(row, fg_quarantine_warehouse: str, finished_item: str | None = None) -> bool:
	if row.t_warehouse != fg_quarantine_warehouse:
		return False

	if row.s_warehouse:
		return False

	if row.get("is_finished_item"):
		return True

	return bool(finished_item and row.item_code == finished_item)


def _get_finished_item(doc) -> str:
	if hasattr(doc, "get_finished_item"):
		return doc.get_finished_item() or ""

	if doc.work_order:
		return frappe.db.get_value("Work Order", doc.work_order, "production_item") or ""

	if doc.bom_no:
		return frappe.db.get_value("BOM", doc.bom_no, "item") or ""

	return ""
