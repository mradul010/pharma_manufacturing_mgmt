import frappe
from frappe import _
from frappe.utils import cint

from pharma_manufacturing_mgmt.utils.batch_tools import (
	QC_STATUS_QUARANTINE,
	create_quality_inspection_for_batch,
	get_row_batches,
	set_batch_qc_status,
)
from pharma_manufacturing_mgmt.utils.settings import (
	get_pharma_settings,
	get_rm_quarantine_warehouse,
	has_configured_quarantine_warehouses,
	is_item_in_scope,
	is_workflow_enabled,
	should_auto_create_qi,
)

LOGGER = frappe.logger("pharma_qc")


def validate_quarantine_receipt(doc, method=None):
	LOGGER.info("Purchase Receipt validate hook started for %s", doc.name)
	settings = get_pharma_settings()
	if not is_workflow_enabled(settings):
		LOGGER.info("Purchase Receipt %s skipped because workflow is disabled", doc.name)
		return

	if not has_configured_quarantine_warehouses(settings):
		LOGGER.info("Purchase Receipt %s skipped because quarantine warehouses are not configured", doc.name)
		return

	rm_quarantine_warehouse = get_rm_quarantine_warehouse(settings)

	for row in doc.get("items"):
		if not is_item_in_scope(row.item_code):
			LOGGER.info("Purchase Receipt %s row %s item %s skipped as out of scope", doc.name, row.idx, row.item_code)
			continue

		if not cint(frappe.get_cached_value("Item", row.item_code, "has_batch_no")):
			LOGGER.info("Purchase Receipt %s row %s blocked because item has no batch tracking", doc.name, row.idx)
			frappe.throw(
				_("Row {0}: Enable batch tracking on item {1} before receiving it.").format(
					row.idx, row.item_code
				)
			)

		if row.warehouse != rm_quarantine_warehouse:
			LOGGER.info(
				"Purchase Receipt %s row %s blocked: warehouse %s is not RM quarantine",
				doc.name,
				row.idx,
				row.warehouse,
			)
			frappe.throw(
				_(
					"Row {0}: Item {1} must be received into RM quarantine warehouse {2}. {3} is not allowed."
				).format(row.idx, row.item_code, rm_quarantine_warehouse, row.warehouse)
			)


def on_submit(doc, method=None):
	LOGGER.info("Purchase Receipt on_submit hook started for %s", doc.name)
	settings = get_pharma_settings()
	if not is_workflow_enabled(settings):
		LOGGER.info("Purchase Receipt %s submit skipped because workflow is disabled", doc.name)
		return

	if not has_configured_quarantine_warehouses(settings):
		LOGGER.info("Purchase Receipt %s submit skipped because quarantine warehouses are not configured", doc.name)
		return

	for row in doc.get("items"):
		if not is_item_in_scope(row.item_code):
			LOGGER.info("Purchase Receipt %s row %s item %s skipped as out of scope", doc.name, row.idx, row.item_code)
			continue

		batches = get_row_batches(row)
		LOGGER.info("Purchase Receipt %s row %s resolved batches: %s", doc.name, row.idx, batches)
		if not batches:
			frappe.throw(
				_("Row {0}: No batch could be resolved for item {1}.").format(row.idx, row.item_code)
			)

		for batch in batches:
			set_batch_qc_status(
				batch.batch_no,
				QC_STATUS_QUARANTINE,
				_("Received via Purchase Receipt {0}; status set to Quarantine.").format(doc.name),
			)
			LOGGER.info("Batch %s status changed to Quarantine from Purchase Receipt %s", batch.batch_no, doc.name)

			if should_auto_create_qi(settings):
				qi_name = create_quality_inspection_for_batch(
					reference_type="Purchase Receipt",
					reference_name=doc.name,
					item_code=row.item_code,
					batch_no=batch.batch_no,
					inspection_type="Incoming",
					company=doc.company,
					sample_size=1,
					child_row_reference=row.name,
					comment_reference=doc,
				)
				if qi_name:
					LOGGER.info("Quality Inspection %s created/resolved for PR %s batch %s", qi_name, doc.name, batch.batch_no)


def on_cancel(doc, method=None):
	LOGGER.info("Purchase Receipt on_cancel hook started for %s", doc.name)
	settings = get_pharma_settings()
	if not is_workflow_enabled(settings):
		LOGGER.info("Purchase Receipt %s cancel skipped because workflow is disabled", doc.name)
		return

	quality_inspections = frappe.get_all(
		"Quality Inspection",
		filters={
			"reference_type": "Purchase Receipt",
			"reference_name": doc.name,
			"docstatus": ("<", 2),
		},
		fields=["name", "docstatus"],
	)
	for qi in quality_inspections:
		if qi.docstatus == 0:
			# Batch status is deliberately left unchanged on PR cancellation; QC state is the audit source.
			frappe.delete_doc("Quality Inspection", qi.name, ignore_permissions=True)
			LOGGER.info("Draft Quality Inspection %s deleted for cancelled Purchase Receipt %s", qi.name, doc.name)
			continue

		message = _(
			"Submitted Quality Inspection {0} was not deleted when Purchase Receipt {1} was cancelled."
		).format(qi.name, doc.name)
		doc.add_comment("Comment", message)
		frappe.msgprint(message, alert=True)
