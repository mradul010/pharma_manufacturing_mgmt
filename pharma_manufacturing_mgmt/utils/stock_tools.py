import frappe
from frappe import _
from frappe.utils import flt

from pharma_manufacturing_mgmt.utils.batch_tools import (
	get_batch_balance_in_warehouse as get_batch_warehouse_balance,
)
from pharma_manufacturing_mgmt.utils.settings import (
	get_fg_approved_warehouse,
	get_fg_quarantine_warehouse,
	get_pharma_settings,
	get_rejected_warehouse,
	get_rm_approved_warehouse,
	get_rm_quarantine_warehouse,
	should_auto_submit_release_transfer,
)


LOGGER = frappe.logger("pharma_qc")


def get_batch_balance_in_warehouse(
	item_code: str,
	batch_no: str,
	warehouse: str,
	company: str | None = None,
) -> float:
	return get_batch_warehouse_balance(
		batch_no,
		warehouse,
		item_code=item_code,
		company=company,
	)


def get_quarantine_source_for_batch(
	item_code: str,
	batch_no: str,
	settings=None,
	company: str | None = None,
	rejected: bool = False,
):
	settings = settings or get_pharma_settings()
	candidates = [
		{
			"stage": "RM",
			"source_warehouse": get_rm_quarantine_warehouse(settings),
			"approved_warehouse": get_rm_approved_warehouse(settings),
		},
		{
			"stage": "FG",
			"source_warehouse": get_fg_quarantine_warehouse(settings),
			"approved_warehouse": get_fg_approved_warehouse(settings),
		},
	]

	for candidate in candidates:
		balance_qty = get_batch_balance_in_warehouse(
			item_code,
			batch_no,
			candidate["source_warehouse"],
			company=company,
		)
		if flt(balance_qty) <= 0:
			continue

		target_warehouse = get_rejected_warehouse(settings) if rejected else candidate["approved_warehouse"]
		return frappe._dict(
			{
				"stage": candidate["stage"],
				"source_warehouse": candidate["source_warehouse"],
				"target_warehouse": target_warehouse,
				"qty": balance_qty,
			}
		)

	return frappe._dict()


def create_material_transfer_for_batch(
	reference_qi: str,
	item_code: str,
	batch_no: str,
	source_warehouse: str,
	target_warehouse: str,
	qty: float,
	company: str | None = None,
	auto_submit: bool | None = None,
) -> str:
	existing = get_linked_release_stock_entry(reference_qi)
	if existing:
		LOGGER.info("Existing Stock Entry %s found for Quality Inspection %s", existing, reference_qi)
		return existing

	if not company:
		company = _get_company_for_transfer(source_warehouse, target_warehouse)

	if not (source_warehouse and target_warehouse):
		frappe.throw(_("Source and target warehouses are required for QC release transfer."))

	if flt(qty) <= 0:
		frappe.throw(
			_("No quarantine balance is available for batch {0} of item {1}.").format(batch_no, item_code)
		)

	stock_entry = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"stock_entry_type": "Material Transfer",
			"purpose": "Material Transfer",
			"company": company,
			"custom_pharma_release_ref": reference_qi,
			"items": [
				{
					"item_code": item_code,
					"qty": qty,
					"s_warehouse": source_warehouse,
					"t_warehouse": target_warehouse,
					# ERPNext v16 can create the Serial and Batch Bundle from legacy batch fields
					# when use_serial_batch_fields is enabled on a batch-tracked item.
					"use_serial_batch_fields": 1,
					"batch_no": batch_no,
				}
			],
		}
	)
	stock_entry.insert(ignore_permissions=True)

	should_submit = should_auto_submit_release_transfer() if auto_submit is None else auto_submit
	if should_submit:
		stock_entry.submit()

	LOGGER.info(
		"Stock Entry %s created for QI %s batch %s %s -> %s",
		stock_entry.name,
		reference_qi,
		batch_no,
		source_warehouse,
		target_warehouse,
	)
	return stock_entry.name


def get_linked_release_stock_entry(quality_inspection: str, include_submitted: bool = True) -> str:
	docstatus_filter = ("<", 2) if include_submitted else 0
	return (
		frappe.db.get_value(
			"Stock Entry",
			{
				"custom_pharma_release_ref": quality_inspection,
				"docstatus": docstatus_filter,
			},
			"name",
			order_by="creation desc",
		)
		or ""
	)


def get_linked_release_stock_entries(quality_inspection: str):
	return frappe.get_all(
		"Stock Entry",
		filters={"custom_pharma_release_ref": quality_inspection, "docstatus": ("<", 2)},
		fields=["name", "docstatus"],
		order_by="creation desc",
	)


def _get_company_for_transfer(source_warehouse: str, target_warehouse: str) -> str:
	return (
		frappe.db.get_value("Warehouse", source_warehouse, "company")
		or frappe.db.get_value("Warehouse", target_warehouse, "company")
		or frappe.defaults.get_user_default("Company")
		or frappe.defaults.get_global_default("company")
	)
