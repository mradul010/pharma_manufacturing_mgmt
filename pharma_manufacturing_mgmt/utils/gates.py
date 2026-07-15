import frappe
from frappe import _
from frappe.utils import cint, date_diff, getdate

from pharma_manufacturing_mgmt.utils.batch_tools import (
	QC_STATUS_APPROVED,
	QC_STATUS_QUARANTINE,
	get_latest_submitted_quality_inspection,
	get_batch_status,
	get_row_batches,
	set_batch_qc_status,
)
from pharma_manufacturing_mgmt.utils.settings import (
	SHELF_LIFE_ACTION_STOP,
	get_min_shelf_life_days_for_dispatch,
	get_pharma_settings,
	get_quarantine_warehouses,
	get_release_role,
	get_rejected_warehouse,
	get_shelf_life_action,
	has_configured_quarantine_warehouses,
	is_item_in_scope,
	is_quarantine_warehouse,
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
	validate_qc_release(doc, settings=settings)
	validate_quarantine_escape(doc, settings=settings)


def validate_outbound(doc, method=None):
	settings = get_pharma_settings()
	if not is_workflow_enabled(settings):
		return

	if doc.doctype == "Sales Invoice" and not cint(doc.get("update_stock")):
		return

	if not has_configured_quarantine_warehouses(settings):
		LOGGER.info("%s %s outbound validation skipped because warehouses are not configured", doc.doctype, doc.name)
		return

	if cint(doc.get("is_return")):
		_validate_outbound_return(doc, settings=settings)
		return

	shelf_life_warnings = []
	for row in doc.get("items") or []:
		if not is_item_in_scope(row.item_code):
			continue

		for batch in get_row_batches(row):
			_validate_outbound_batch(doc, row, batch.batch_no, settings=settings)
			_validate_dispatch_shelf_life(doc, row, batch.batch_no, settings, shelf_life_warnings)

	if shelf_life_warnings:
		frappe.msgprint(
			_("The following batches are below the minimum shelf life for dispatch:<br>{0}").format(
				"<br>".join(shelf_life_warnings)
			),
			title=_("Shelf Life Warning"),
			indicator="orange",
		)


def on_outbound_submit(doc, method=None):
	settings = get_pharma_settings()
	if (
		not is_workflow_enabled(settings)
		or (doc.doctype == "Sales Invoice" and not cint(doc.get("update_stock")))
		or not cint(doc.get("is_return"))
	):
		return

	for row in doc.get("items") or []:
		if not is_item_in_scope(row.item_code):
			continue

		for batch in get_row_batches(row):
			set_batch_qc_status(
				batch.batch_no,
				QC_STATUS_QUARANTINE,
				_("Returned via {0} {1} - re-quarantined.").format(doc.doctype, doc.name),
			)


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


def validate_qc_release(doc, method=None, settings=None):
	settings = settings or get_pharma_settings()
	if not doc.get("custom_pharma_release_ref"):
		return

	for row in doc.get("items") or []:
		if not _is_qc_release_row(row, settings):
			continue

		if not is_item_in_scope(row.item_code):
			continue

		batches = get_row_batches(row)
		if not batches:
			frappe.throw(
				_("Row {0}: No batch could be resolved for QC Release item {1}.").format(
					row.idx, row.item_code
				)
			)

		for batch in batches:
			_validate_qc_release_batch(row, batch.batch_no)


def _is_consumed_row(doc, row) -> bool:
	if not row.s_warehouse:
		return False

	if doc.purpose == "Manufacture" and row.get("is_finished_item"):
		return False

	return True


def _is_qc_release_row(row, settings) -> bool:
	if not row.s_warehouse or not is_quarantine_warehouse(row.s_warehouse, settings):
		return False

	if row.t_warehouse == get_rejected_warehouse(settings):
		return False

	return True


def _validate_qc_release_batch(row, batch_no: str):
	status = get_batch_status(batch_no)
	if status != QC_STATUS_APPROVED:
		frappe.throw(
			_(
				"Row {0}: Batch {1} for item {2} cannot be released. Current QC status: {3}."
			).format(row.idx, batch_no, row.item_code, status or _("Not Set"))
		)

	quality_inspection = get_latest_submitted_quality_inspection(
		batch_no,
		"Accepted",
		item_code=row.item_code,
	)
	if quality_inspection:
		return

	frappe.throw(
		_(
			"Row {0}: Batch {1} for item {2} cannot be released. No submitted Accepted Quality Inspection was found for this item and batch. Current QC status: {3}."
		).format(row.idx, batch_no, row.item_code, status or _("Not Set"))
	)


def _validate_outbound_batch(doc, row, batch_no: str, settings):
	status = get_batch_status(batch_no)
	if status != QC_STATUS_APPROVED:
		frappe.throw(
			_(
				"Row {0}: item {1}, batch {2} is not QC-approved. Current status: {3}."
			).format(row.idx, row.item_code, batch_no, status or _("Not Set"))
		)

	source_warehouse = row.get("warehouse")
	if is_quarantine_warehouse(source_warehouse, settings) or source_warehouse == get_rejected_warehouse(settings):
		frappe.throw(
			_(
				"Row {0}: item {1}, batch {2} is in warehouse {3}. Stock must exit via QC Release before delivery."
			).format(row.idx, row.item_code, batch_no, source_warehouse)
		)

	quality_inspection = get_latest_submitted_quality_inspection(
		batch_no,
		"Accepted",
		item_code=row.item_code,
	)
	if not quality_inspection:
		frappe.throw(
			_(
				"Row {0}: item {1}, batch {2} has no submitted Accepted Quality Inspection."
			).format(row.idx, row.item_code, batch_no)
		)

	row.custom_quality_inspection = quality_inspection


def _validate_dispatch_shelf_life(doc, row, batch_no: str, settings, warnings: list[str]):
	min_days = get_min_shelf_life_days_for_dispatch(settings)
	if min_days <= 0:
		return

	expiry_date = frappe.db.get_value("Batch", batch_no, "expiry_date")
	if not expiry_date:
		return

	posting_date = getdate(doc.get("posting_date") or frappe.utils.today())
	remaining_days = date_diff(getdate(expiry_date), posting_date)
	if remaining_days >= min_days:
		return

	message = _(
		"Batch {0} expires on {1}; remaining shelf life is {2} days, required minimum is {3} days."
	).format(batch_no, expiry_date, remaining_days, min_days)

	if get_shelf_life_action(settings) == SHELF_LIFE_ACTION_STOP:
		frappe.throw(message)

	warnings.append(message)


def _validate_outbound_return(doc, settings):
	for row in doc.get("items") or []:
		if not is_item_in_scope(row.item_code):
			continue

		target_warehouse = row.get("warehouse")
		if not is_quarantine_warehouse(target_warehouse, settings):
			frappe.throw(
				_(
					"Row {0}: return item {1} must be received into a quarantine warehouse."
				).format(row.idx, row.item_code)
			)
