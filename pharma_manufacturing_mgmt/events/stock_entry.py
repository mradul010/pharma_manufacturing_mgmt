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
	if not is_workflow_enabled(settings) or doc.purpose != "Manufacture":
		return

	if not has_configured_quarantine_warehouses(settings):
		LOGGER.info("Stock Entry %s submit skipped because quarantine warehouses are not configured", doc.name)
		return

	fg_quarantine_warehouse = get_fg_quarantine_warehouse(settings)

	for row in doc.get("items") or []:
		if row.t_warehouse != fg_quarantine_warehouse:
			continue

		if not row.get("is_finished_item") and row.s_warehouse:
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
