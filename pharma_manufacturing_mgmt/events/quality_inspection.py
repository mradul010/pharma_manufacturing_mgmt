import frappe
from frappe import _
from frappe.model.naming import make_autoname

from pharma_manufacturing_mgmt.pharma_manufacturing_mgmt.doctype.batch_manufacturing_record.batch_manufacturing_record import (
	clear_bmr_ipc_for_qi,
	sync_bmr_ipc_from_qi,
)
from pharma_manufacturing_mgmt.utils.batch_tools import (
	QC_STATUS_APPROVED,
	QC_STATUS_QUARANTINE,
	QC_STATUS_REJECTED,
	QC_STATUS_UNDER_TEST,
	get_batch_status,
	set_batch_qc_status,
)
from pharma_manufacturing_mgmt.utils.settings import (
	RELEASE_MODE_AUTO_DRAFT,
	RELEASE_MODE_AUTO_SUBMIT,
	get_fg_approved_warehouse,
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
	get_batch_balance_in_warehouse,
	get_linked_release_stock_entries,
	get_quarantine_source_for_batch,
)


LOGGER = frappe.logger("pharma_qc")


def set_ar_number(doc, method=None):
	if not doc.get("custom_ar_number"):
		doc.custom_ar_number = make_autoname("AR-.YY.-.#####")


def set_under_test(doc, method=None):
	LOGGER.info("Quality Inspection on_update hook started for %s", doc.name)
	settings = get_pharma_settings()
	if (
		not is_workflow_enabled(settings)
		or not has_configured_quarantine_warehouses(settings)
		or doc.docstatus != 0
	):
		return

	qi_batch = _get_quality_inspection_item_batch(doc, settings=settings)
	if not qi_batch:
		return

	if get_batch_status(qi_batch.batch_no) != QC_STATUS_QUARANTINE:
		return

	if not _has_reading_value(doc):
		return

	set_batch_qc_status(
		qi_batch.batch_no,
		QC_STATUS_UNDER_TEST,
		_("QC testing started via {0}.").format(doc.name),
	)
	LOGGER.info("Batch %s status changed to Under Test from QI %s", qi_batch.batch_no, doc.name)


def on_submit(doc, method=None):
	LOGGER.info("Quality Inspection on_submit hook started for %s", doc.name)
	sync_bmr_ipc_from_qi(doc)

	settings = get_pharma_settings()
	if not is_workflow_enabled(settings):
		return

	if not has_configured_quarantine_warehouses(settings):
		LOGGER.info("Quality Inspection %s skipped because quarantine warehouses are not configured", doc.name)
		return

	qi_batch = _get_quality_inspection_item_batch(doc, settings=settings)
	if not qi_batch:
		return

	frappe.db.get_value("Batch", qi_batch.batch_no, "name", for_update=True)

	if doc.status == "Accepted":
		set_batch_qc_status(
			qi_batch.batch_no,
			QC_STATUS_APPROVED,
			_("QC approved via {0}.").format(doc.name),
		)
		_create_qc_transfer_if_configured(doc, settings=settings, rejected=False, qi_batch=qi_batch)
	elif doc.status == "Rejected":
		set_batch_qc_status(
			qi_batch.batch_no,
			QC_STATUS_REJECTED,
			_("QC rejected via {0}.").format(doc.name),
		)
		if not _is_manufacture_quality_inspection(doc):
			_create_qc_transfer_if_configured(doc, settings=settings, rejected=True, qi_batch=qi_batch)
	else:
		LOGGER.info("Quality Inspection %s skipped because status is %s", doc.name, doc.status)


def record_verdict(doc, method=None):
	on_submit(doc, method=method)


def on_cancel(doc, method=None):
	LOGGER.info("Quality Inspection on_cancel hook started for %s", doc.name)
	clear_bmr_ipc_for_qi(doc)

	settings = get_pharma_settings()
	if not is_workflow_enabled(settings):
		return

	for stock_entry in get_linked_release_stock_entries(doc.name):
		if stock_entry.docstatus == 1:
			frappe.throw(_("Cancel the linked release/reject Stock Entry first."))

		frappe.delete_doc("Stock Entry", stock_entry.name, ignore_permissions=True)
		LOGGER.info("Draft Stock Entry %s deleted for cancelled QI %s", stock_entry.name, doc.name)

	qi_batch = _get_quality_inspection_item_batch(doc, settings=settings)
	if qi_batch:
		set_batch_qc_status(
			qi_batch.batch_no,
			QC_STATUS_QUARANTINE,
			_("QI {0} cancelled; status reverted to Quarantine.").format(doc.name),
		)


def _create_qc_transfer(doc, settings, rejected: bool, qi_batch):
	is_manufacture_qi = _is_manufacture_quality_inspection(doc)
	if not rejected and _has_fg_quarantine_balance(doc, settings=settings, qi_batch=qi_batch):
		_create_fg_transfer(doc, settings=settings, qi_batch=qi_batch)
		return

	if is_manufacture_qi and rejected:
		return

	source = get_quarantine_source_for_batch(
		qi_batch.item_code,
		qi_batch.batch_no,
		settings=settings,
		company=doc.company,
		rejected=rejected,
	)
	if not source:
		frappe.throw(
			_(
				"No quarantine stock balance was found for batch {0} of item {1} in RM quarantine {2} or FG quarantine {3}; release/reject transfer was not created."
			).format(
				qi_batch.batch_no,
				qi_batch.item_code,
				get_rm_quarantine_warehouse(settings),
				get_fg_quarantine_warehouse(settings),
			)
		)

	if not source.target_warehouse:
		frappe.throw(
			_("No target warehouse is configured for batch {0} of item {1}.").format(
				qi_batch.batch_no, qi_batch.item_code
			)
		)

	create_material_transfer_for_batch(
		reference_qi=doc.name,
		item_code=qi_batch.item_code,
		batch_no=qi_batch.batch_no,
		source_warehouse=source.source_warehouse,
		target_warehouse=source.target_warehouse,
		qty=source.qty,
		company=doc.company,
		auto_submit=(get_release_mode(settings) == RELEASE_MODE_AUTO_SUBMIT and not rejected),
	)


def _has_fg_quarantine_balance(doc, settings, qi_batch) -> bool:
	source_warehouse = get_fg_quarantine_warehouse(settings)
	if not source_warehouse:
		return False

	return bool(
		get_batch_balance_in_warehouse(
			qi_batch.item_code,
			qi_batch.batch_no,
			source_warehouse,
			company=doc.company,
		)
	)


def _create_fg_transfer(doc, settings, qi_batch):
	source_warehouse = get_fg_quarantine_warehouse(settings)
	target_warehouse = get_fg_approved_warehouse(settings)
	if not (source_warehouse and target_warehouse):
		frappe.throw(
			_("FG Quarantine and FG Approved warehouses must be configured for FG QC transfer.")
		)

	qty = get_batch_balance_in_warehouse(
		qi_batch.item_code,
		qi_batch.batch_no,
		source_warehouse,
		company=doc.company,
	)
	if not qty:
		frappe.throw(
			_(
				"Quality Inspection {0} is for item {1}, batch {2}, but no matching FG quarantine balance was found. QC movement was not created."
			).format(doc.name, qi_batch.item_code, qi_batch.batch_no)
		)

	create_material_transfer_for_batch(
		reference_qi=doc.name,
		item_code=qi_batch.item_code,
		batch_no=qi_batch.batch_no,
		source_warehouse=source_warehouse,
		target_warehouse=target_warehouse,
		qty=qty,
		company=doc.company,
		auto_submit=False,
		stock_entry_type="Material Transfer",
	)


def _create_qc_transfer_if_configured(doc, settings, rejected: bool, qi_batch):
	release_mode = get_release_mode(settings)
	if (
		rejected
		and not _is_manufacture_quality_inspection(doc)
		and release_mode not in (
			RELEASE_MODE_AUTO_DRAFT,
			RELEASE_MODE_AUTO_SUBMIT,
		)
	):
		LOGGER.info("Quality Inspection %s release transfer skipped in Manual mode", doc.name)
		return

	try:
		_create_qc_transfer(doc, settings=settings, rejected=rejected, qi_batch=qi_batch)
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


def _get_quality_inspection_item_batch(doc, settings=None):
	if not doc.item_code:
		return None

	if not doc.batch_no:
		message = _("Quality Inspection {0} has no batch number; no batch QC status was updated.").format(
			doc.name
		)
		frappe.log_error(title=_("Quality Inspection Missing Batch"), message=message)
		_add_qi_comment(doc.name, message)
		return None

	batch_item = frappe.db.get_value("Batch", doc.batch_no, "item")
	if batch_item != doc.item_code:
		message = _(
			"Quality Inspection {0} item {1} does not match Batch {2} item {3}; no batch QC status or movement was updated."
		).format(doc.name, doc.item_code, doc.batch_no, batch_item or _("Not Set"))
		frappe.log_error(title=_("Quality Inspection Batch Item Mismatch"), message=message)
		_add_qi_comment(doc.name, message)
		return None

	if is_item_in_scope(doc.item_code):
		return frappe._dict({"item_code": doc.item_code, "batch_no": doc.batch_no})

	settings = settings or get_pharma_settings()
	if get_batch_balance_in_warehouse(
		doc.item_code,
		doc.batch_no,
		get_fg_quarantine_warehouse(settings),
		company=doc.company,
	):
		return frappe._dict({"item_code": doc.item_code, "batch_no": doc.batch_no})

	message = _(
		"Quality Inspection {0} item {1} is not in Pharma Settings Applicable Item Groups and has no FG Quarantine balance; no batch QC status or movement was updated."
	).format(doc.name, doc.item_code)
	frappe.log_error(title=_("Quality Inspection Item Out of Scope"), message=message)
	_add_qi_comment(doc.name, message)
	return None


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
