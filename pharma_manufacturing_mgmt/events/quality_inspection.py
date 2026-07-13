import frappe
from frappe import _

from pharma_manufacturing_mgmt.utils.batch_tools import (
	QC_STATUS_APPROVED,
	QC_STATUS_QUARANTINE,
	QC_STATUS_REJECTED,
	QC_STATUS_UNDER_TEST,
	get_batch_status,
	set_batch_qc_status,
)
from pharma_manufacturing_mgmt.utils.settings import (
	get_fg_quarantine_warehouse,
	get_pharma_settings,
	get_rm_quarantine_warehouse,
	has_configured_quarantine_warehouses,
	is_item_in_scope,
	is_workflow_enabled,
	should_auto_submit_release_transfer,
)
from pharma_manufacturing_mgmt.utils.stock_tools import (
	create_material_transfer_for_batch,
	get_linked_release_stock_entries,
	get_quarantine_source_for_batch,
)


LOGGER = frappe.logger("pharma_qc")


def set_under_test(doc, method=None):
	LOGGER.info("Quality Inspection on_update hook started for %s", doc.name)
	settings = get_pharma_settings()
	if (
		not is_workflow_enabled(settings)
		or not has_configured_quarantine_warehouses(settings)
		or doc.docstatus != 0
		or not _is_relevant_quality_inspection(doc)
	):
		return

	if get_batch_status(doc.batch_no) != QC_STATUS_QUARANTINE:
		return

	if not _has_reading_value(doc):
		return

	set_batch_qc_status(
		doc.batch_no,
		QC_STATUS_UNDER_TEST,
		_("QC testing started via {0}.").format(doc.name),
	)
	LOGGER.info("Batch %s status changed to Under Test from QI %s", doc.batch_no, doc.name)


def on_submit(doc, method=None):
	LOGGER.info("Quality Inspection on_submit hook started for %s", doc.name)
	settings = get_pharma_settings()
	if not is_workflow_enabled(settings) or not _is_relevant_quality_inspection(doc):
		return

	if not has_configured_quarantine_warehouses(settings):
		LOGGER.info("Quality Inspection %s skipped because quarantine warehouses are not configured", doc.name)
		return

	frappe.db.get_value("Batch", doc.batch_no, "name", for_update=True)

	if doc.status == "Accepted":
		set_batch_qc_status(
			doc.batch_no,
			QC_STATUS_APPROVED,
			_("QC approved via {0}.").format(doc.name),
		)
		_create_qc_transfer(doc, settings=settings, rejected=False)
	elif doc.status == "Rejected":
		set_batch_qc_status(
			doc.batch_no,
			QC_STATUS_REJECTED,
			_("QC rejected via {0}.").format(doc.name),
		)
		_create_qc_transfer(doc, settings=settings, rejected=True)
	else:
		LOGGER.info("Quality Inspection %s skipped because status is %s", doc.name, doc.status)


def record_verdict(doc, method=None):
	on_submit(doc, method=method)


def on_cancel(doc, method=None):
	LOGGER.info("Quality Inspection on_cancel hook started for %s", doc.name)
	settings = get_pharma_settings()
	if not is_workflow_enabled(settings) or not _is_relevant_quality_inspection(doc):
		return

	for stock_entry in get_linked_release_stock_entries(doc.name):
		if stock_entry.docstatus == 1:
			frappe.throw(_("Cancel the linked release/reject Stock Entry first."))

		frappe.delete_doc("Stock Entry", stock_entry.name, ignore_permissions=True)
		LOGGER.info("Draft Stock Entry %s deleted for cancelled QI %s", stock_entry.name, doc.name)

	set_batch_qc_status(
		doc.batch_no,
		QC_STATUS_QUARANTINE,
		_("QI {0} cancelled; status reverted to Quarantine.").format(doc.name),
	)


def _create_qc_transfer(doc, settings, rejected: bool):
	source = get_quarantine_source_for_batch(
		doc.item_code,
		doc.batch_no,
		settings=settings,
		company=doc.company,
		rejected=rejected,
	)
	if not source:
		frappe.throw(
			_(
				"No quarantine stock balance was found for batch {0} of item {1} in RM quarantine {2} or FG quarantine {3}; release/reject transfer was not created."
			).format(
				doc.batch_no,
				doc.item_code,
				get_rm_quarantine_warehouse(settings),
				get_fg_quarantine_warehouse(settings),
			)
		)

	if not source.target_warehouse:
		frappe.throw(
			_("No target warehouse is configured for batch {0} of item {1}.").format(
				doc.batch_no, doc.item_code
			)
		)

	create_material_transfer_for_batch(
		reference_qi=doc.name,
		item_code=doc.item_code,
		batch_no=doc.batch_no,
		source_warehouse=source.source_warehouse,
		target_warehouse=source.target_warehouse,
		qty=source.qty,
		company=doc.company,
		auto_submit=should_auto_submit_release_transfer(settings),
	)


def _is_relevant_quality_inspection(doc) -> bool:
	return bool(doc.item_code and doc.batch_no and is_item_in_scope(doc.item_code))


def _has_reading_value(doc) -> bool:
	fields = ["reading_value"] + ["reading_{0}".format(i) for i in range(1, 11)]
	for reading in doc.get("readings") or []:
		for fieldname in fields:
			value = reading.get(fieldname)
			if value not in (None, ""):
				return True

	return False
