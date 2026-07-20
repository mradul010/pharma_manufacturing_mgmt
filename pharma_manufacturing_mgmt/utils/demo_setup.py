import frappe
from frappe import _
from frappe.utils import add_days, nowtime, today


DEMO_WAREHOUSES = {
	"rm_quarantine": "RM Quaratine - VPPL",
	"rm_approved": "RM Approved - VPPL",
	"fg_quarantine": "FG Quaration - VPPL",
	"fg_approved": "FG Approved - VPPL",
	"rejected": "Rejected Store - VPPL",
	"packing_material": "Packing Material Store - VPPL",
	"stores": "Stores - VPPL",
	"wip": "Work In Progress - VPPL",
}


def run():
	company = _get_company()
	warehouses = {
		key: _ensure_warehouse(warehouse_name, company)
		for key, warehouse_name in DEMO_WAREHOUSES.items()
	}

	item_groups = [
		_ensure_item_group("RM - API"),
		_ensure_item_group("RM - Excipient"),
		_ensure_item_group("FG - Pharma"),
	]
	template = _ensure_quality_inspection_template()
	items = [
		_ensure_item("PHARMA-RM-API-001", "Demo API Raw Material", item_groups[0], template),
		_ensure_item("PHARMA-RM-EXC-001", "Demo Excipient Raw Material", item_groups[1], template),
		_ensure_item("PHARMA-FG-001", "Demo Finished Good", item_groups[2], template, is_purchase_item=0),
	]
	supplier = _ensure_supplier()

	_configure_settings(item_groups, warehouses)

	_receipt_plan = [
		(items[0], 10, 0),
		(items[1], 12, 3),
		(items[0], 8, 5),
		(items[1], 9, 10),
	]
	for item_code, qty, days_back in _receipt_plan:
		_ensure_purchase_flow(company, supplier, item_code, qty, warehouses["rm_quarantine"], days_back)

	frappe.db.commit()
	frappe.msgprint(_("Pharma QC demo data is ready."))


def _get_company():
	company = frappe.defaults.get_global_default("company") or frappe.defaults.get_user_default("Company")
	if company:
		return company

	company = frappe.db.get_value("Company", {}, "name")
	if not company:
		frappe.throw(_("Please create a Company before running Pharma QC demo setup."))

	return company


def _ensure_warehouse(warehouse_name: str, company: str) -> str:
	if frappe.db.exists("Warehouse", warehouse_name):
		return warehouse_name

	company_abbr = frappe.get_cached_value("Company", company, "abbr")
	doc_warehouse_name = warehouse_name
	if company_abbr:
		suffix = " - " + company_abbr
		if warehouse_name.endswith(suffix):
			doc_warehouse_name = warehouse_name[: -len(suffix)]

	existing = frappe.db.get_value(
		"Warehouse",
		{"warehouse_name": doc_warehouse_name, "company": company},
		"name",
	)
	if existing:
		return existing

	doc = frappe.get_doc(
		{
			"doctype": "Warehouse",
			"warehouse_name": doc_warehouse_name,
			"company": company,
			"is_group": 0,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def _ensure_item_group(item_group_name: str) -> str:
	if frappe.db.exists("Item Group", item_group_name):
		return item_group_name

	parent = frappe.db.get_value("Item Group", {"is_group": 1}, "name", order_by="lft asc")
	doc = frappe.get_doc(
		{
			"doctype": "Item Group",
			"item_group_name": item_group_name,
			"parent_item_group": parent,
			"is_group": 0,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def _ensure_quality_inspection_template() -> str:
	template_name = "Pharma Demo RM Template"
	if frappe.db.exists("Quality Inspection Template", template_name):
		return template_name

	_parameters = [
		("Pharma Demo Assay", 1, 98, 102, None),
		("Pharma Demo Loss on Drying", 1, 0, 1, None),
		("Pharma Demo Description", 0, None, None, "White to off-white powder"),
	]
	for parameter, _numeric, _min_value, _max_value, _value in _parameters:
		if not frappe.db.exists("Quality Inspection Parameter", parameter):
			frappe.get_doc(
				{
					"doctype": "Quality Inspection Parameter",
					"parameter": parameter,
				}
			).insert(ignore_permissions=True)

	template = frappe.get_doc(
		{
			"doctype": "Quality Inspection Template",
			"quality_inspection_template_name": template_name,
		}
	)
	for parameter, numeric, min_value, max_value, value in _parameters:
		template.append(
			"item_quality_inspection_parameter",
			{
				"specification": parameter,
				"numeric": numeric,
				"min_value": min_value,
				"max_value": max_value,
				"value": value,
			},
		)
	template.insert(ignore_permissions=True)
	return template.name


def _ensure_item(
	item_code: str,
	item_name: str,
	item_group: str,
	template: str,
	is_purchase_item: int = 1,
) -> str:
	hsn_code = _get_demo_hsn_code()
	if frappe.db.exists("Item", item_code):
		frappe.db.set_value("Item", item_code, "quality_inspection_template", template)
		frappe.db.set_value("Item", item_code, "is_purchase_item", is_purchase_item)
		if hsn_code:
			frappe.db.set_value("Item", item_code, "gst_hsn_code", hsn_code)
		return item_code

	item_data = {
		"doctype": "Item",
		"item_code": item_code,
		"item_name": item_name,
		"item_group": item_group,
		"stock_uom": "Nos",
		"is_stock_item": 1,
		"is_purchase_item": is_purchase_item,
		"has_batch_no": 1,
		"create_new_batch": 1,
		"batch_number_series": item_code + "-BATCH-.#####",
		"quality_inspection_template": template,
	}
	if hsn_code:
		item_data["gst_hsn_code"] = hsn_code

	doc = frappe.get_doc(item_data)
	doc.insert(ignore_permissions=True)
	return doc.name


def _get_demo_hsn_code():
	if not frappe.get_meta("Item").has_field("gst_hsn_code"):
		return None

	existing = frappe.db.get_value("GST HSN Code", {}, "name")
	if existing:
		return existing

	hsn_code = "999900"
	frappe.get_doc(
		{
			"doctype": "GST HSN Code",
			"hsn_code": hsn_code,
			"description": "Demo pharma material",
		}
	).insert(ignore_permissions=True)
	return hsn_code


def _ensure_supplier() -> str:
	supplier_name = "Pharma Demo Supplier"
	existing = frappe.db.get_value("Supplier", {"supplier_name": supplier_name}, "name")
	if existing:
		return existing

	supplier_group = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
	doc = frappe.get_doc(
		{
			"doctype": "Supplier",
			"supplier_name": supplier_name,
			"supplier_type": "Company",
			"supplier_group": supplier_group,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def _configure_settings(item_groups, warehouses):
	settings = frappe.get_single("Pharma Settings")
	settings.enable_quarantine_workflow = 1
	settings.auto_create_quality_inspection = 1
	settings.auto_submit_release_transfer = 0
	settings.restrict_quarantine_transfers = 1
	settings.quarantine_release_role = "Pharma QA"
	settings.rm_quarantine_warehouse = warehouses["rm_quarantine"]
	settings.rm_approved_warehouse = warehouses["rm_approved"]
	settings.fg_quarantine_warehouse = warehouses["fg_quarantine"]
	settings.fg_approved_warehouse = warehouses["fg_approved"]
	settings.rejected_warehouse = warehouses["rejected"]
	settings.set("applicable_item_groups", [])
	for item_group in item_groups:
		settings.append("applicable_item_groups", {"item_group": item_group})

	settings.save(ignore_permissions=True)
	frappe.clear_cache(doctype="Pharma Settings")
	setup_packing_material()


def _ensure_purchase_flow(company, supplier, item_code, qty, warehouse, days_back):
	posting_date = add_days(today(), -days_back)
	marker = "Pharma QC demo {0} {1}".format(item_code, days_back)
	if frappe.db.exists("Purchase Receipt", {"title": marker, "docstatus": ("<", 2)}):
		return

	po = _ensure_purchase_order(company, supplier, item_code, qty, warehouse, posting_date, marker)
	batch_no = _ensure_batch(item_code, "DEMO-{0}-{1}".format(item_code, days_back))

	pr = frappe.get_doc(
		{
			"doctype": "Purchase Receipt",
			"supplier": supplier,
			"company": company,
			"posting_date": posting_date,
			"posting_time": nowtime(),
			"set_posting_time": 1,
			"title": marker,
			"remarks": marker,
			"items": [
				{
					"item_code": item_code,
					"qty": qty,
					"received_qty": qty,
					"rate": 100,
					"warehouse": warehouse,
					"batch_no": batch_no,
					"use_serial_batch_fields": 1,
					"purchase_order": po.name,
					"purchase_order_item": po.items[0].name,
				}
			],
		}
	)
	pr.insert(ignore_permissions=True)
	pr.submit()


def _ensure_purchase_order(company, supplier, item_code, qty, warehouse, schedule_date, marker):
	existing = frappe.db.get_value("Purchase Order", {"title": marker, "docstatus": ("<", 2)}, "name")
	if existing:
		return frappe.get_doc("Purchase Order", existing)

	po = frappe.get_doc(
		{
			"doctype": "Purchase Order",
			"supplier": supplier,
			"company": company,
			"transaction_date": schedule_date,
			"schedule_date": schedule_date,
			"title": marker,
			"items": [
				{
					"item_code": item_code,
					"qty": qty,
					"rate": 100,
					"warehouse": warehouse,
					"schedule_date": schedule_date,
				}
			],
		}
	)
	po.insert(ignore_permissions=True)
	po.submit()
	return po


def _ensure_batch(item_code, batch_id):
	if frappe.db.exists("Batch", batch_id):
		return batch_id

	batch = frappe.get_doc({"doctype": "Batch", "batch_id": batch_id, "item": item_code})
	batch.insert(ignore_permissions=True)
	return batch.name


def setup_packing_material():
	settings = frappe.get_single("Pharma Settings")
	item_group = _ensure_item_group_with_parent("Packing Material", "All Item Groups")
	_append_applicable_item_group(settings, item_group)

	templates = _ensure_packing_material_templates()
	rm_quarantine_warehouse = settings.rm_quarantine_warehouse
	_ensure_packing_material_item(
		"PM-JAR-120",
		"Transparent Matka Jar 120g",
		item_group,
		templates["PM - Container/Jar"],
		rm_quarantine_warehouse,
	)
	_ensure_packing_material_item(
		"PM-LABEL-TOP",
		"Top Label 47mm",
		item_group,
		templates["PM - Label"],
		rm_quarantine_warehouse,
	)


def _ensure_item_group_with_parent(item_group_name: str, preferred_parent: str) -> str:
	if frappe.db.exists("Item Group", item_group_name):
		return item_group_name

	parent = preferred_parent if frappe.db.exists("Item Group", preferred_parent) else frappe.db.get_value(
		"Item Group", {"is_group": 1}, "name", order_by="lft asc"
	)
	doc = frappe.get_doc(
		{
			"doctype": "Item Group",
			"item_group_name": item_group_name,
			"parent_item_group": parent,
			"is_group": 0,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def _append_applicable_item_group(settings, item_group: str):
	for row in settings.get("applicable_item_groups") or []:
		if row.item_group == item_group:
			return

	settings.append("applicable_item_groups", {"item_group": item_group})
	settings.save(ignore_permissions=True)
	frappe.clear_cache(doctype="Pharma Settings")


def _ensure_packing_material_templates() -> dict[str, str]:
	_parameters = {
		"Empty Weight (g)",
		"Neck Diameter (mm)",
		"Visual / Leakage",
		"Label Dimension (mm)",
		"Avg. Label Weight (g)",
		"Printed Content",
		"Shipper Length (mm)",
		"Shipper Breadth (mm)",
		"Shipper Height (mm)",
		"Ply / Thickness (mm)",
		"Avg. Shipper Weight (kg)",
	}
	for parameter in _parameters:
		if not frappe.db.exists("Quality Inspection Parameter", parameter):
			frappe.get_doc(
				{
					"doctype": "Quality Inspection Parameter",
					"parameter": parameter,
				}
			).insert(ignore_permissions=True)

	template_specs = {
		"PM - Container/Jar": [
			("Empty Weight (g)", 1, 18, 22, None),
			("Neck Diameter (mm)", 1, 52, 56, None),
			("Visual / Leakage", 0, None, None, "No leakage, clear, no deformity"),
		],
		"PM - Label": [
			("Label Dimension (mm)", 0, None, None, "As per specimen"),
			("Avg. Label Weight (g)", 1, 0.30, 0.35, None),
			("Printed Content", 0, None, None, "Matches approved artwork"),
		],
		"PM - Shipper": [
			("Shipper Length (mm)", 1, 455, 461, None),
			("Shipper Breadth (mm)", 1, 383, 388, None),
			("Shipper Height (mm)", 1, 133, 138, None),
			("Ply / Thickness (mm)", 1, 4, 5, None),
			("Avg. Shipper Weight (kg)", 1, 1.10, 1.20, None),
		],
	}
	return {
		template_name: _ensure_pm_template(template_name, parameters)
		for template_name, parameters in template_specs.items()
	}


def _ensure_pm_template(template_name: str, parameters: list[tuple]) -> str:
	if frappe.db.exists("Quality Inspection Template", template_name):
		template = frappe.get_doc("Quality Inspection Template", template_name)
		template.set("item_quality_inspection_parameter", [])
	else:
		template = frappe.get_doc(
			{
				"doctype": "Quality Inspection Template",
				"quality_inspection_template_name": template_name,
			}
		)

	for parameter, numeric, min_value, max_value, value in parameters:
		template.append(
			"item_quality_inspection_parameter",
			{
				"specification": parameter,
				"numeric": numeric,
				"min_value": min_value,
				"max_value": max_value,
				"value": value,
			},
		)

	if template.is_new():
		template.insert(ignore_permissions=True)
	else:
		template.save(ignore_permissions=True)
	return template.name


def _ensure_packing_material_item(
	item_code: str,
	item_name: str,
	item_group: str,
	template: str,
	default_warehouse: str,
) -> str:
	_ensure_uom("Nos")
	item_values = {
		"item_name": item_name,
		"item_group": item_group,
		"stock_uom": "Nos",
		"is_stock_item": 1,
		"is_purchase_item": 1,
		"has_batch_no": 1,
		"create_new_batch": 0,
		"has_expiry_date": 0,
		"quality_inspection_template": template,
	}

	if frappe.db.exists("Item", item_code):
		item = frappe.get_doc("Item", item_code)
		for fieldname, value in item_values.items():
			item.set(fieldname, value)
	else:
		item = frappe.get_doc({"doctype": "Item", "item_code": item_code, **item_values})

	if default_warehouse:
		company = frappe.db.get_value("Warehouse", default_warehouse, "company")
		_set_item_default_warehouse(item, company, default_warehouse)

	if item.is_new():
		item.insert(ignore_permissions=True)
	else:
		item.save(ignore_permissions=True)
	return item.name


def _set_item_default_warehouse(item, company: str | None, warehouse: str):
	for row in item.get("item_defaults") or []:
		if row.company == company:
			row.default_warehouse = warehouse
			return

	item.append(
		"item_defaults",
		{
			"company": company,
			"default_warehouse": warehouse,
		},
	)


def _ensure_uom(uom_name: str):
	if frappe.db.exists("UOM", uom_name):
		return

	frappe.get_doc({"doctype": "UOM", "uom_name": uom_name}).insert(ignore_permissions=True)
