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
	QC_STAGE_FG,
	RELEASE_MODE_AUTO_DRAFT,
	RELEASE_MODE_AUTO_SUBMIT,
	get_fg_quarantine_warehouse,
	get_pharma_settings,
	get_release_mode,
	get_rm_quarantine_warehouse,
	has_configured_quarantine_warehouses,
	is_item_in_scope,
	is_workflow_enabled,
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
		_create_qc_transfer_if_configured(doc, settings=settings, rejected=False)
	elif doc.status == "Rejected":
		set_batch_qc_status(
			doc.batch_no,
			QC_STATUS_REJECTED,
			_("QC rejected via {0}.").format(doc.name),
		)
		_create_qc_transfer_if_configured(doc, settings=settings, rejected=True)
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
	is_manufacture_qi = _is_manufacture_quality_inspection(doc)
	source = get_quarantine_source_for_batch(
		doc.item_code,
		doc.batch_no,
		settings=settings,
		company=doc.company,
		rejected=rejected,
		preferred_stage=QC_STAGE_FG if is_manufacture_qi else None,
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

	if is_manufacture_qi and source.stage != QC_STAGE_FG:
		frappe.throw(
			_(
				"Manufacture Quality Inspection {0} is for item {1}, batch {2}, but no matching FG quarantine balance was found. QC movement was not created."
			).format(doc.name, doc.item_code, doc.batch_no)
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
		auto_submit=(get_release_mode(settings) == RELEASE_MODE_AUTO_SUBMIT and not rejected),
	)


def _create_qc_transfer_if_configured(doc, settings, rejected: bool):
	release_mode = get_release_mode(settings)
	if release_mode not in (RELEASE_MODE_AUTO_DRAFT, RELEASE_MODE_AUTO_SUBMIT):
		LOGGER.info("Quality Inspection %s release transfer skipped in Manual mode", doc.name)
		return

	try:
		_create_qc_transfer(doc, settings=settings, rejected=rejected)
	except Exception:
		LOGGER.exception("Auto QC transfer creation failed for Quality Inspection %s", doc.name)
		_add_qi_comment(
			doc.name,
			_(
				"Automatic QC {0} transfer was not created. Please create the Stock Entry manually. Error: {1}"
			).format(_("rejection") if rejected else _("release"), frappe.get_traceback()),
		)


def _add_qi_comment(quality_inspection: str, content: str):
	if frappe.db.exists(
		"Comment",
		{
			"comment_type": "Comment",
			"reference_doctype": "Quality Inspection",
			"reference_name": quality_inspection,
			"content": content,
		},
	):
		return

	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Comment",
			"reference_doctype": "Quality Inspection",
			"reference_name": quality_inspection,
			"content": content,
		}
	).insert(ignore_permissions=True)


def _is_relevant_quality_inspection(doc) -> bool:
	return bool(doc.item_code and doc.batch_no and is_item_in_scope(doc.item_code))


def _is_manufacture_quality_inspection(doc) -> bool:
	if doc.reference_type != "Stock Entry" or not doc.reference_name:
		return False

	return frappe.db.get_value("Stock Entry", doc.reference_name, "purpose") == "Manufacture"


def _has_reading_value(doc) -> bool:
	fields = ["reading_value"] + ["reading_{0}".format(i) for i in range(1, 11)]
	for reading in doc.get("readings") or []:
		for fieldname in fields:
			value = reading.get(fieldname)
			if value not in (None, ""):
				return True

	return False
