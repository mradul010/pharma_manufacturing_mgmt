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
	is_workflow_enabled,
	should_auto_create_quality_inspection,
)


LOGGER = frappe.logger("pharma_qc")


def validate(doc, method=None):
	validate_stock_entry(doc, method=method)


def on_submit(doc, method=None):
	LOGGER.info("Stock Entry on_submit hook started for %s", doc.name)
	settings = get_pharma_settings()
	if doc.docstatus != 1 or doc.purpose != "Manufacture" or not is_workflow_enabled(settings):
		return

	fg_quarantine_warehouse = get_fg_quarantine_warehouse(settings)
	if not fg_quarantine_warehouse:
		LOGGER.info("Stock Entry %s submit skipped because quarantine warehouses are not configured", doc.name)
		return

	if not should_auto_create_quality_inspection(settings):
		LOGGER.info(
			"Manufacture Stock Entry %s skipped automatic FG Quality Inspection creation because the setting is disabled",
			doc.name,
		)
		return

	for row in doc.get("items") or []:
		if not _is_finished_goods_output_row(row, fg_quarantine_warehouse):
			continue

		batches = get_row_batches(row)
		if not batches:
			_log_fg_operation_failure(
				doc,
				row.item_code,
				"",
				fg_quarantine_warehouse,
				_("Row {0}: No finished goods batch could be resolved for item {1}.").format(row.idx, row.item_code),
			)
			continue

		for batch in batches:
			if not _batch_matches_item(
				batch.batch_no,
				row.item_code,
				doc=doc,
				row=row,
				warehouse=fg_quarantine_warehouse,
			):
				LOGGER.warning(
					"Manufacture Stock Entry %s row %s skipped mismatched batch %s for item %s",
					doc.name,
					row.idx,
					batch.batch_no,
					row.item_code,
				)
				continue

			try:
				# Stock Entry manufacturing QC is part of the production process, so ERPNext's
				# valid "In Process" inspection type is used for finished goods quarantine.
				create_draft_quality_inspection(
					reference_type="Stock Entry",
					reference_name=doc.name,
					item_code=row.item_code,
					batch_no=batch.batch_no,
					inspection_type="In Process",
					company=doc.company,
					sample_size=batch.qty or row.get("transfer_qty") or row.get("qty") or 1,
					child_row_reference=row.name,
					comment_reference=doc,
				)
			except Exception as exc:
				_log_fg_operation_failure(doc, row.item_code, batch.batch_no, fg_quarantine_warehouse, exc)
				continue

			set_batch_qc_status(
				batch.batch_no,
				QC_STATUS_QUARANTINE,
				_(
					"Finished Goods received via Manufacture Stock Entry {0}; status set to Quarantine."
				).format(doc.name),
			)


def _is_finished_goods_output_row(row, fg_quarantine_warehouse: str) -> bool:
	if row.t_warehouse != fg_quarantine_warehouse:
		return False

	if row.s_warehouse:
		return False

	if row.get("type") or row.get("is_legacy_scrap_item"):
		return False

	return bool(row.get("is_finished_item"))


def _batch_matches_item(batch_no: str, item_code: str, doc, row, warehouse: str) -> bool:
	batch_item = frappe.db.get_value("Batch", batch_no, "item")
	if batch_item == item_code:
		return True

	if not batch_item:
		_log_fg_operation_failure(
			doc,
			item_code,
			batch_no,
			warehouse,
			_("Row {0}: Batch {1} does not exist.").format(row.idx, batch_no),
		)
		return False

	LOGGER.warning(
		"Manufacture Stock Entry %s row %s skipped batch %s because it belongs to item %s, not %s",
		doc.name,
		row.idx,
		batch_no,
		batch_item,
		item_code,
	)
	return False


def _log_fg_operation_failure(doc, item_code: str, batch_no: str, warehouse: str, error):
	message = _(
		"Automatic Quality Inspection creation failed for FG item {0}, batch {1}, warehouse {2}. Error: {3}"
	).format(
		item_code,
		batch_no or _("Not Resolved"),
		warehouse or _("Not Set"),
		error,
	)
	log_message = frappe.get_traceback() if isinstance(error, Exception) else message
	frappe.log_error(title=_("FG Quarantine Workflow Creation Failed"), message=log_message)
	_add_stock_entry_comment(doc, message)
	if isinstance(error, Exception):
		LOGGER.exception(message)
	else:
		LOGGER.error(message)


def _add_stock_entry_comment(doc, message: str):
	if frappe.db.exists(
		"Comment",
		{
			"comment_type": "Comment",
			"reference_doctype": doc.doctype,
			"reference_name": doc.name,
			"content": message,
		},
	):
		return

	doc.add_comment("Comment", message)
