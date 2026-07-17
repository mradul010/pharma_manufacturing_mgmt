from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt, get_datetime
from pypika import functions as fn

from pharma_manufacturing_mgmt.utils.settings import (
	get_pharma_settings,
	get_quarantine_warehouses,
)


QC_STATUS_QUARANTINE = "Quarantine"
QC_STATUS_UNDER_TEST = "Under Test"
QC_STATUS_APPROVED = "Approved"
QC_STATUS_REJECTED = "Rejected"

QC_STATUSES = {
	QC_STATUS_QUARANTINE,
	QC_STATUS_UNDER_TEST,
	QC_STATUS_APPROVED,
	QC_STATUS_REJECTED,
}

LOGGER = frappe.logger("pharma_qc")


def get_row_batches(row) -> list[frappe._dict]:
	batches = defaultdict(float)
	bundle = row.get("serial_and_batch_bundle")

	if bundle:
		LOGGER.info("Resolving batches from Serial and Batch Bundle %s", bundle)
		for batch in frappe.db.sql(
			"""
			SELECT sbe.batch_no, SUM(sbe.qty) AS qty
			FROM `tabSerial and Batch Entry` sbe
			WHERE sbe.parent = %s
				AND IFNULL(sbe.batch_no, '') != ''
			GROUP BY sbe.batch_no
			""",
			(bundle,),
			as_dict=True,
		):
			batches[batch.batch_no] += abs(flt(batch.qty))

	if not batches and row.get("batch_no"):
		LOGGER.info("Resolving batch from legacy batch_no field %s", row.get("batch_no"))
		batches[row.get("batch_no")] += abs(
			flt(
				row.get("transfer_qty")
				or row.get("stock_qty")
				or row.get("received_stock_qty")
				or row.get("qty")
				or 1
			)
		)

	resolved_batches = [
		frappe._dict({"batch_no": batch_no, "qty": qty}) for batch_no, qty in batches.items()
	]
	LOGGER.info("Resolved batches for row %s: %s", row.get("name") or row.get("idx"), resolved_batches)
	return resolved_batches


def set_batch_qc_status(
	batch_no: str,
	status: str,
	comment_reference=None,
	comment_text: str | None = None,
):
	if isinstance(comment_reference, str) and comment_text is None:
		comment_text = comment_reference
		comment_reference = None

	if status not in QC_STATUSES:
		frappe.throw(_("Invalid QC status {0}.").format(status))

	current_status = get_batch_qc_status(batch_no)
	values = {"custom_qc_status": status}
	frappe.db.set_value("Batch", batch_no, values)
	LOGGER.info("Batch %s QC status changed to %s", batch_no, status)

	if comment_text and current_status != status:
		add_batch_comment(batch_no, comment_text)


def add_batch_comment(batch_no: str, content: str):
	if frappe.db.exists(
		"Comment",
		{
			"comment_type": "Comment",
			"reference_doctype": "Batch",
			"reference_name": batch_no,
			"content": content,
		},
	):
		return

	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Comment",
			"reference_doctype": "Batch",
			"reference_name": batch_no,
			"content": content,
		}
	).insert(ignore_permissions=True)


def get_batch_qc_status(batch_no: str) -> str:
	return frappe.db.get_value("Batch", batch_no, "custom_qc_status") or ""


def get_batch_status(batch_no: str) -> str:
	return get_batch_qc_status(batch_no)


def get_batch_quarantine_balance(
	item_code: str,
	batch_no: str,
	quarantine_warehouse: str,
	company: str | None = None,
) -> float:
	return get_batch_balance_in_warehouse(
		batch_no,
		quarantine_warehouse,
		item_code=item_code,
		company=company,
	)


def get_batch_balance_in_warehouse(
	batch_no: str,
	warehouse: str,
	item_code: str | None = None,
	company: str | None = None,
) -> float:
	if not (batch_no and warehouse):
		return 0

	balance = sum(
		flt(row.balance_qty)
		for row in get_quarantine_batch_balances(
			[warehouse],
			company=company,
			item_code=item_code,
			batch_no=batch_no,
		)
	)
	LOGGER.info("Batch %s balance in warehouse %s resolved as %s", batch_no, warehouse, balance)
	return balance if flt(balance) > 0 else 0


def find_batch_quarantine_warehouse(
	item_code: str,
	batch_no: str,
	settings=None,
	company: str | None = None,
) -> str:
	settings = settings or get_pharma_settings()
	for warehouse in get_quarantine_warehouses(settings):
		if get_batch_balance_in_warehouse(
			batch_no,
			warehouse,
			item_code=item_code,
			company=company,
		):
			return warehouse

	return ""


def get_batch_quarantine_balance_for_any_stage(
	item_code: str,
	batch_no: str,
	settings=None,
	company: str | None = None,
) -> frappe._dict:
	settings = settings or get_pharma_settings()
	for warehouse in get_quarantine_warehouses(settings):
		balance = get_batch_balance_in_warehouse(
			batch_no,
			warehouse,
			item_code=item_code,
			company=company,
		)
		if balance:
			return frappe._dict({"warehouse": warehouse, "qty": balance})

	return frappe._dict({"warehouse": "", "qty": 0})


def create_quality_inspection_for_batch(
	reference_type: str,
	reference_name: str,
	item_code: str,
	batch_no: str,
	inspection_type: str,
	company: str | None = None,
	sample_size: float = 1,
	child_row_reference: str | None = None,
	comment_reference=None,
) -> str:
	from pharma_manufacturing_mgmt.utils.quality_inspection_tools import (
		create_draft_quality_inspection,
	)

	return create_draft_quality_inspection(
		reference_type=reference_type,
		reference_name=reference_name,
		item_code=item_code,
		batch_no=batch_no,
		inspection_type=inspection_type,
		company=company,
		sample_size=sample_size,
		child_row_reference=child_row_reference,
		comment_reference=comment_reference,
	)


def get_latest_submitted_quality_inspection(
	batch_no: str,
	status: str | None = None,
	item_code: str | None = None,
	reference_type: str | None = None,
	reference_name: str | None = None,
) -> str:
	filters = {"batch_no": batch_no, "docstatus": 1}
	if status:
		filters["status"] = status
	if item_code:
		filters["item_code"] = item_code
	if reference_type:
		filters["reference_type"] = reference_type
	if reference_name:
		filters["reference_name"] = reference_name

	return (
		frappe.db.get_value(
			"Quality Inspection",
			filters,
			"name",
			order_by="report_date desc, creation desc",
		)
		or ""
	)


def get_accepted_quality_inspection(item_code: str, batch_no: str) -> str:
	if not (item_code and batch_no):
		return ""

	return get_latest_submitted_quality_inspection(
		batch_no,
		"Accepted",
		item_code=item_code,
	)


def has_submitted_accepted_quality_inspection(batch_no: str, item_code: str | None = None) -> bool:
	if item_code:
		return bool(get_accepted_quality_inspection(item_code, batch_no))

	return bool(get_latest_submitted_quality_inspection(batch_no, "Accepted"))


def has_submitted_qc_release(batch_no: str) -> bool:
	if not batch_no:
		return False

	if frappe.db.sql(
		"""
		SELECT se.name
		FROM `tabStock Entry` se
		INNER JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
		WHERE se.docstatus = 1
			AND IFNULL(se.custom_pharma_release_ref, '') != ''
			AND sed.batch_no = %s
		LIMIT 1
		""",
		(batch_no,),
	):
		return True

	return bool(
		frappe.db.sql(
			"""
			SELECT se.name
			FROM `tabStock Entry` se
			INNER JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
			INNER JOIN `tabSerial and Batch Entry` sbe ON sbe.parent = sed.serial_and_batch_bundle
			WHERE se.docstatus = 1
				AND IFNULL(se.custom_pharma_release_ref, '') != ''
				AND sbe.batch_no = %s
			LIMIT 1
			""",
			(batch_no,),
		)
	)


def get_existing_quality_inspection(
	reference_type: str,
	reference_name: str,
	item_code: str,
	batch_no: str,
) -> str:
	return (
		frappe.db.exists(
			"Quality Inspection",
			{
				"reference_type": reference_type,
				"reference_name": reference_name,
				"item_code": item_code,
				"batch_no": batch_no,
				"docstatus": ("<", 2),
			},
		)
		or ""
	)


def get_quarantine_batch_balances(
	warehouses: list[str],
	company: str | None = None,
	item_code: str | None = None,
	batch_no: str | None = None,
) -> list[frappe._dict]:
	if not warehouses:
		return []

	rows = []
	rows.extend(_get_legacy_batch_balances(warehouses, company, item_code, batch_no))
	rows.extend(_get_bundle_batch_balances(warehouses, company, item_code, batch_no))

	balances = {}
	for row in rows:
		key = (row.item_code, row.batch_no, row.warehouse)
		if key not in balances:
			balances[key] = frappe._dict(
				{
					"item_code": row.item_code,
					"batch_no": row.batch_no,
					"warehouse": row.warehouse,
					"balance_qty": 0,
					"stock_value": 0,
				}
			)

		balances[key].balance_qty += flt(row.balance_qty)
		balances[key].stock_value += flt(row.stock_value)

	return [row for row in balances.values() if flt(row.balance_qty) > 0]


def _get_legacy_batch_balances(warehouses, company=None, item_code=None, batch_no=None):
	sle = frappe.qb.DocType("Stock Ledger Entry")
	query = (
		frappe.qb.from_(sle)
		.select(
			sle.item_code,
			sle.batch_no,
			sle.warehouse,
			fn.Sum(sle.actual_qty).as_("balance_qty"),
			fn.Sum(sle.stock_value_difference).as_("stock_value"),
		)
		.where(
			(sle.warehouse.isin(warehouses))
			& (sle.is_cancelled == 0)
			& (sle.docstatus == 1)
			& (sle.batch_no.isnotnull())
			& (sle.batch_no != "")
			& ((sle.serial_and_batch_bundle.isnull()) | (sle.serial_and_batch_bundle == ""))
		)
		.groupby(sle.item_code, sle.batch_no, sle.warehouse)
	)

	if company:
		query = query.where(sle.company == company)
	if item_code:
		query = query.where(sle.item_code == item_code)
	if batch_no:
		query = query.where(sle.batch_no == batch_no)

	return query.run(as_dict=True)


def _get_bundle_batch_balances(warehouses, company=None, item_code=None, batch_no=None):
	sle = frappe.qb.DocType("Stock Ledger Entry")
	sbe = frappe.qb.DocType("Serial and Batch Entry")
	sabb = frappe.qb.DocType("Serial and Batch Bundle")

	query = (
		frappe.qb.from_(sle)
		.inner_join(sabb)
		.on(sabb.name == sle.serial_and_batch_bundle)
		.inner_join(sbe)
		.on(sbe.parent == sle.serial_and_batch_bundle)
		.select(
			sle.item_code,
			sbe.batch_no,
			sle.warehouse,
			fn.Sum(sbe.qty).as_("balance_qty"),
			fn.Sum(sbe.stock_value_difference).as_("stock_value"),
		)
		.where(
			(sle.warehouse.isin(warehouses))
			& (sle.is_cancelled == 0)
			& (sle.docstatus == 1)
			& (sabb.is_cancelled == 0)
			& (sbe.batch_no.isnotnull())
			& (sbe.batch_no != "")
		)
		.groupby(sle.item_code, sbe.batch_no, sle.warehouse)
	)

	if company:
		query = query.where(sle.company == company)
	if item_code:
		query = query.where(sle.item_code == item_code)
	if batch_no:
		query = query.where(sbe.batch_no == batch_no)

	return query.run(as_dict=True)


def get_first_quarantine_receipt(batch_no: str, warehouse: str):
	rows = []
	rows.extend(_get_legacy_receipt_rows(batch_no, warehouse))
	rows.extend(_get_bundle_receipt_rows(batch_no, warehouse))

	rows = [row for row in rows if row.posting_datetime]
	if not rows:
		return frappe._dict()

	rows.sort(key=lambda row: (get_datetime(row.posting_datetime), row.creation))
	first = rows[0]
	if first.voucher_type != "Purchase Receipt":
		return first

	pr = frappe.db.get_value(
		"Purchase Receipt",
		first.voucher_no,
		["supplier", "posting_date"],
		as_dict=True,
	)
	if pr:
		first.supplier = pr.supplier
		first.receipt_date = pr.posting_date

	return first


def _get_legacy_receipt_rows(batch_no, warehouse):
	return frappe.db.sql(
		"""
		SELECT voucher_type, voucher_no, posting_datetime, posting_date, creation
		FROM `tabStock Ledger Entry`
		WHERE batch_no = %s
			AND warehouse = %s
			AND actual_qty > 0
			AND is_cancelled = 0
			AND docstatus = 1
		ORDER BY posting_datetime, creation
		LIMIT 1
		""",
		(batch_no, warehouse),
		as_dict=True,
	)


def _get_bundle_receipt_rows(batch_no, warehouse):
	return frappe.db.sql(
		"""
		SELECT sle.voucher_type, sle.voucher_no, sle.posting_datetime, sle.posting_date, sle.creation
		FROM `tabStock Ledger Entry` sle
		INNER JOIN `tabSerial and Batch Entry` sbe
			ON sbe.parent = sle.serial_and_batch_bundle
		WHERE sbe.batch_no = %s
			AND sle.warehouse = %s
			AND sbe.qty > 0
			AND sle.is_cancelled = 0
			AND sle.docstatus = 1
		ORDER BY sle.posting_datetime, sle.creation
		LIMIT 1
		""",
		(batch_no, warehouse),
		as_dict=True,
	)


def _get_reference_company(reference_type: str, reference_name: str) -> str:
	return (
		frappe.db.get_value(reference_type, reference_name, "company")
		or frappe.defaults.get_user_default("Company")
		or frappe.defaults.get_global_default("company")
	)


def _add_reference_comment(comment_reference, reference_type: str, reference_name: str, content: str):
	comment_doctype = reference_type
	comment_name = reference_name
	if getattr(comment_reference, "doctype", None) and getattr(comment_reference, "name", None):
		comment_doctype = comment_reference.doctype
		comment_name = comment_reference.name
	elif isinstance(comment_reference, (tuple, list)) and len(comment_reference) == 2:
		comment_doctype, comment_name = comment_reference

	if not (comment_doctype and comment_name):
		return

	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Comment",
			"reference_doctype": comment_doctype,
			"reference_name": comment_name,
			"content": content,
		}
	).insert(ignore_permissions=True)
