import frappe
from frappe.utils import flt, formatdate

from pharma_manufacturing_mgmt.utils.batch_tools import get_row_batches


EMPTY_VALUE = "—"


@frappe.whitelist()
def get_dispensing_sheet_context(stock_entry_name: str) -> dict:
	doc = frappe.get_doc("Stock Entry", stock_entry_name)
	work_order = frappe.get_doc("Work Order", doc.work_order) if doc.work_order else None
	bom = frappe.get_doc("BOM", work_order.bom_no) if work_order and work_order.bom_no else None
	bom_rows = _get_bom_rows_by_item_code(bom)

	return {
		"work_order": doc.work_order or EMPTY_VALUE,
		"product": _get_product_display(doc, work_order),
		"batch_size": _get_batch_size(work_order),
		"party": work_order.get("custom_party") if work_order and work_order.get("custom_party") else "",
		"fg_batch_no": _get_fg_batch_no(doc),
		"posting_date": formatdate(doc.posting_date, "dd-mm-yyyy") if doc.posting_date else EMPTY_VALUE,
		"stock_entry": doc.name or EMPTY_VALUE,
		"ingredients": [_get_ingredient_context(row, work_order, bom, bom_rows) for row in doc.items if row.s_warehouse],
	}


def _get_bom_rows_by_item_code(bom) -> dict:
	if not bom:
		return {}

	return {row.item_code: row for row in bom.items if row.item_code}


def _get_product_display(doc, work_order) -> str:
	if work_order and work_order.production_item:
		item_name = frappe.db.get_value("Item", work_order.production_item, "item_name")
		return _join_code_name(work_order.production_item, item_name)

	for row in doc.items:
		if row.item_code:
			return _join_code_name(row.item_code, row.item_name)

	return EMPTY_VALUE


def _get_batch_size(work_order) -> str:
	if not work_order:
		return EMPTY_VALUE

	qty = flt(work_order.qty)
	if not qty:
		return EMPTY_VALUE

	uom = work_order.stock_uom or ""
	return "{0:.3f} {1}".format(qty, uom).strip()


def _get_fg_batch_no(doc) -> str:
	for row in doc.items:
		if not (row.t_warehouse and row.get("is_finished_item")):
			continue

		batches = get_row_batches(row)
		if batches:
			return ", ".join(batch.batch_no for batch in batches if batch.batch_no) or EMPTY_VALUE

	return EMPTY_VALUE


def _get_ingredient_context(row, work_order, bom, bom_rows: dict) -> dict:
	bom_row = bom_rows.get(row.item_code)
	batches = [_get_batch_context(row.item_code, batch.batch_no) for batch in get_row_batches(row)]
	return {
		"item_code": row.item_code or EMPTY_VALUE,
		"item_name": row.item_name or frappe.db.get_value("Item", row.item_code, "item_name") or EMPTY_VALUE,
		"standard_qty": _get_standard_qty(work_order, bom, bom_row, row),
		"issued_qty": "{0:.3f} {1}".format(flt(row.qty), row.uom or "").strip(),
		"batches": batches,
	}


def _get_standard_qty(work_order, bom, bom_row, stock_entry_row) -> str:
	if not (work_order and bom and bom_row):
		return EMPTY_VALUE

	bom_qty = flt(bom.quantity)
	work_order_qty = flt(work_order.qty)
	if not (bom_qty and work_order_qty):
		return EMPTY_VALUE

	standard_qty = flt(bom_row.qty) / bom_qty * work_order_qty
	uom = bom_row.uom or stock_entry_row.uom or ""
	return "{0:.3f} {1}".format(standard_qty, uom).strip()


def _get_batch_context(item_code: str, batch_no: str) -> dict:
	quality_inspection = _get_latest_accepted_quality_inspection(item_code, batch_no)
	ar_number = EMPTY_VALUE
	if quality_inspection:
		ar_number = quality_inspection.custom_ar_number or quality_inspection.name

	return {
		"batch_no": batch_no or EMPTY_VALUE,
		"ar_number": ar_number,
	}


def _get_latest_accepted_quality_inspection(item_code: str, batch_no: str):
	if not (item_code and batch_no):
		return None

	result = frappe.db.sql(
		"""
		SELECT name, custom_ar_number
		FROM `tabQuality Inspection`
		WHERE batch_no = %(batch)s
			AND item_code = %(item)s
			AND docstatus = 1
			AND status = 'Accepted'
		ORDER BY report_date DESC, creation DESC
		LIMIT 1
		""",
		{"batch": batch_no, "item": item_code},
		as_dict=True,
	)
	return result[0] if result else None


def _join_code_name(item_code: str, item_name: str | None) -> str:
	if item_name:
		return "{0} - {1}".format(item_code, item_name)

	return item_code or EMPTY_VALUE
