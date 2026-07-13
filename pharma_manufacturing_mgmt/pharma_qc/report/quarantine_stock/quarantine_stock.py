import frappe
from frappe import _
from frappe.utils import date_diff, flt, getdate, today

from pharma_manufacturing_mgmt.utils.batch_tools import (
	get_first_quarantine_receipt,
	get_quarantine_batch_balances,
)
from pharma_manufacturing_mgmt.utils.settings import (
	get_pharma_settings,
	get_quarantine_warehouses,
	get_rejected_warehouse,
	is_quarantine_workflow_enabled,
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.company:
		frappe.throw(_("Company is required."))

	settings = get_pharma_settings()
	if not is_quarantine_workflow_enabled(settings):
		return get_columns(), [], None, None, []

	warehouses = get_quarantine_warehouses(settings)
	if filters.warehouse:
		if filters.warehouse not in warehouses:
			frappe.throw(_("{0} is not a configured Quarantine warehouse.").format(filters.warehouse))
		warehouses = [filters.warehouse]

	balances = get_quarantine_batch_balances(
		warehouses,
		company=filters.company,
		item_code=filters.item_code,
	)

	release_qty_map = get_release_qty_map(settings)
	data = []
	for balance in balances:
		row = build_row(balance, release_qty_map)
		if filters.qc_status and row["qc_status"] != filters.qc_status:
			continue
		if filters.ageing_bucket and row["ageing_bucket"] != filters.ageing_bucket:
			continue
		data.append(row)

	report_summary = get_report_summary(data, filters.company)
	return get_columns(), data, None, None, report_summary


def get_columns():
	return [
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 140},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 160},
		{"label": _("Batch No"), "fieldname": "batch_no", "fieldtype": "Link", "options": "Batch", "width": 130},
		{"label": _("QC Status"), "fieldname": "qc_status", "fieldtype": "Data", "width": 110},
		{"label": _("Warehouse"), "fieldname": "warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 180},
		{"label": _("Balance Qty"), "fieldname": "balance_qty", "fieldtype": "Float", "width": 120},
		{"label": _("Stock Value"), "fieldname": "stock_value", "fieldtype": "Currency", "width": 120},
		{"label": _("Received Via"), "fieldname": "received_via", "fieldtype": "Dynamic Link", "options": "received_via_type", "width": 150},
		{"label": _("Received Via Type"), "fieldname": "received_via_type", "fieldtype": "Data", "hidden": 1},
		{"label": _("Receipt Date"), "fieldname": "receipt_date", "fieldtype": "Date", "width": 110},
		{"label": _("Supplier"), "fieldname": "supplier", "fieldtype": "Link", "options": "Supplier", "width": 160},
		{"label": _("Quality Inspection"), "fieldname": "quality_inspection", "fieldtype": "Link", "options": "Quality Inspection", "width": 160},
		{"label": _("QI Status"), "fieldname": "quality_inspection_status", "fieldtype": "Data", "width": 100},
		{"label": _("Pending Days"), "fieldname": "pending_days", "fieldtype": "Int", "width": 110},
		{"label": _("Ageing Bucket"), "fieldname": "ageing_bucket", "fieldtype": "Data", "width": 110},
		{"label": _("Released Qty"), "fieldname": "released_qty", "fieldtype": "Float", "width": 110},
		{"label": _("Rejected Qty"), "fieldname": "rejected_qty", "fieldtype": "Float", "width": 110},
	]


def build_row(balance, release_qty_map):
	batch = frappe.db.get_value(
		"Batch",
		balance.batch_no,
		["item", "custom_qc_status", "expiry_date"],
		as_dict=True,
	)
	item = frappe.db.get_value("Item", balance.item_code, ["item_name"], as_dict=True) or {}
	receipt = get_first_quarantine_receipt(balance.batch_no, balance.warehouse)
	qi = get_latest_quality_inspection(balance.item_code, balance.batch_no)

	receipt_date = receipt.get("receipt_date") or receipt.get("posting_date")
	pending_days = date_diff(today(), getdate(receipt_date)) if receipt_date else None
	key = (balance.item_code, balance.batch_no)
	release_qty = release_qty_map.get(key, frappe._dict({"released_qty": 0, "rejected_qty": 0}))

	return {
		"item_code": balance.item_code,
		"item_name": item.get("item_name"),
		"batch_no": balance.batch_no,
		"qc_status": (batch or {}).get("custom_qc_status"),
		"warehouse": balance.warehouse,
		"balance_qty": flt(balance.balance_qty),
		"stock_value": flt(balance.stock_value),
		"received_via": receipt.get("voucher_no"),
		"received_via_type": receipt.get("voucher_type"),
		"receipt_date": receipt_date,
		"supplier": receipt.get("supplier"),
		"quality_inspection": qi.get("name"),
		"quality_inspection_status": qi.get("status"),
		"pending_days": pending_days,
		"ageing_bucket": get_ageing_bucket(pending_days),
		"released_qty": flt(release_qty.released_qty),
		"rejected_qty": flt(release_qty.rejected_qty),
	}


def get_latest_quality_inspection(item_code: str, batch_no: str):
	rows = frappe.get_all(
		"Quality Inspection",
		filters={"item_code": item_code, "batch_no": batch_no, "docstatus": ("<", 2)},
		fields=["name", "status"],
		order_by="modified desc",
		limit=1,
	)
	return rows[0] if rows else frappe._dict()


def get_ageing_bucket(pending_days):
	if pending_days is None:
		return ""
	if pending_days <= 3:
		return "0-3 days"
	if pending_days <= 7:
		return "4-7 days"
	return ">7 days"


def get_release_qty_map(settings=None):
	settings = settings or get_pharma_settings()
	rejected_warehouse = get_rejected_warehouse(settings)
	qty_map = {}

	for row in _get_transfer_qty_rows():
		key = (row.item_code, row.batch_no)
		qty_map.setdefault(key, frappe._dict({"released_qty": 0, "rejected_qty": 0}))
		if row.t_warehouse == rejected_warehouse:
			qty_map[key].rejected_qty += flt(row.qty)
		else:
			qty_map[key].released_qty += flt(row.qty)

	return qty_map


def _get_transfer_qty_rows():
	rows = []
	rows.extend(_get_legacy_transfer_qty_rows())
	rows.extend(_get_bundle_transfer_qty_rows())
	return rows


def _get_legacy_transfer_qty_rows():
	return frappe.db.sql(
		"""
		SELECT
			sed.item_code,
			sed.batch_no,
			ABS(SUM(sed.qty)) AS qty,
			sed.t_warehouse
		FROM `tabStock Entry Detail` sed
		INNER JOIN `tabStock Entry` se ON se.name = sed.parent
		WHERE se.docstatus = 1
			AND IFNULL(se.custom_pharma_release_ref, '') != ''
			AND IFNULL(sed.batch_no, '') != ''
			AND (sed.serial_and_batch_bundle IS NULL OR sed.serial_and_batch_bundle = '')
		GROUP BY sed.item_code, sed.batch_no, sed.t_warehouse
		""",
		as_dict=True,
	)


def _get_bundle_transfer_qty_rows():
	return frappe.db.sql(
		"""
		SELECT
			sed.item_code,
			sbe.batch_no,
			ABS(SUM(sbe.qty)) AS qty,
			sed.t_warehouse
		FROM `tabStock Entry Detail` sed
		INNER JOIN `tabStock Entry` se ON se.name = sed.parent
		INNER JOIN `tabSerial and Batch Entry` sbe ON sbe.parent = sed.serial_and_batch_bundle
		WHERE se.docstatus = 1
			AND IFNULL(se.custom_pharma_release_ref, '') != ''
			AND IFNULL(sbe.batch_no, '') != ''
		GROUP BY sed.item_code, sbe.batch_no, sed.t_warehouse
		""",
		as_dict=True,
	)


def get_report_summary(data, company):
	currency = frappe.get_cached_value("Company", company, "default_currency")
	total_value = sum(flt(row.get("stock_value")) for row in data)
	status_batches = {}
	for row in data:
		status = row.get("qc_status") or _("Not Set")
		status_batches.setdefault(status, set()).add(row.get("batch_no"))

	summary = [
		{
			"value": total_value,
			"label": _("Total Quarantine Value"),
			"datatype": "Currency",
			"currency": currency,
			"indicator": "Blue",
		}
	]

	for status in ["Quarantine", "Under Test", "Approved", "Rejected"]:
		summary.append(
			{
				"value": len(status_batches.get(status, set())),
				"label": _("{0} Batches").format(status),
				"datatype": "Int",
				"indicator": "Orange" if status in ("Quarantine", "Under Test") else "Green",
			}
		)

	summary.append(
		{
			"value": len({row.get("batch_no") for row in data if (row.get("pending_days") or 0) > 7}),
			"label": _("Pending > 7 Days"),
			"datatype": "Int",
			"indicator": "Red",
		}
	)
	return summary
