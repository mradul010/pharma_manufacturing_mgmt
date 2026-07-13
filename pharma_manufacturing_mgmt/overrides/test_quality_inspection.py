import frappe
from erpnext.stock.doctype.quality_inspection.quality_inspection import (
	QualityInspection as CoreQualityInspection,
)
from frappe.tests import IntegrationTestCase
from frappe.utils import getdate, nowtime, today

COMPANY = "_Test Pharma QI Co"
COMPANY_ABBR = "TPQIC"
SUPPLIER = "_Test Pharma QI Supplier"
IN_SCOPE_GROUP = "_Test Pharma QI Scoped Group"
OUT_OF_SCOPE_GROUP = "_Test Pharma QI Unscoped Group"
IN_SCOPE_ITEM = "_TEST-PHARMA-QI-IN-SCOPE"
OUT_OF_SCOPE_ITEM = "_TEST-PHARMA-QI-OUT-OF-SCOPE"


class TestPharmaQualityInspectionOverrideContract(IntegrationTestCase):
	def test_core_method_still_exists(self):
		"""Guard the override contract: if erpnext ever renames/removes this
		method, PharmaQualityInspection silently stops overriding anything
		and native flag validation would start throwing again for in-scope
		items. Fail loudly here instead of discovering it in production."""
		self.assertTrue(
			hasattr(CoreQualityInspection, "validate_inspection_required"),
			"erpnext.stock.doctype.quality_inspection.quality_inspection.QualityInspection "
			"no longer has validate_inspection_required(). "
			"pharma_manufacturing_mgmt.overrides.quality_inspection.PharmaQualityInspection "
			"must be updated to override whatever method now raises "
			"'Inspection Required before Purchase' for out-of-scope items.",
		)


class TestPharmaQualityInspectionOverride(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		# Also setup-wizard-only: batch-tracked items need this globally
		# enabled before a Serial and Batch Bundle can be created for them.
		frappe.db.set_single_value("Stock Settings", "enable_serial_and_batch_no_for_item", 1)

		cls.company = _ensure_company(COMPANY, COMPANY_ABBR)
		cls.warehouse = f"Stores - {COMPANY_ABBR}"
		cls.supplier = _ensure_supplier(SUPPLIER)

		in_scope_group = _ensure_item_group(IN_SCOPE_GROUP)
		_ensure_item_group(OUT_OF_SCOPE_GROUP)

		cls.in_scope_item = _ensure_item(IN_SCOPE_ITEM, in_scope_group)
		cls.out_of_scope_item = _ensure_item(OUT_OF_SCOPE_ITEM, OUT_OF_SCOPE_GROUP)

		settings = frappe.get_single("Pharma Settings")
		settings.enable_quarantine_workflow = 1
		settings.set("applicable_item_groups", [{"item_group": in_scope_group}])
		settings.save(ignore_permissions=True)
		frappe.clear_cache(doctype="Pharma Settings")

	def _submit_purchase_receipt(self, item_code, batch_id):
		# Hand-rolled rather than reusing erpnext's own
		# test_purchase_receipt.make_purchase_receipt: importing that module
		# pulls in erpnext.tests.utils, whose BootStrapTestData() runs at
		# *import time* and creates global master data (fiscal years, etc.)
		# unconditionally — it collides with real data on a site like
		# pharma-demo that already has its own Fiscal Year records. This
		# mirrors the plain frappe.get_doc() construction this app's own
		# utils.demo_setup already uses successfully for the same PO -> PR
		# chain.
		if not frappe.db.exists("Batch", batch_id):
			frappe.get_doc({"doctype": "Batch", "batch_id": batch_id, "item": item_code}).insert(
				ignore_permissions=True
			)

		po = frappe.get_doc(
			{
				"doctype": "Purchase Order",
				"company": self.company,
				"supplier": self.supplier,
				"transaction_date": today(),
				"schedule_date": today(),
				"items": [
					{
						"item_code": item_code,
						"qty": 1,
						"rate": 10,
						"warehouse": self.warehouse,
						"schedule_date": today(),
					}
				],
			}
		)
		po.insert(ignore_permissions=True)
		po.submit()

		pr = frappe.get_doc(
			{
				"doctype": "Purchase Receipt",
				"company": self.company,
				"supplier": self.supplier,
				"posting_date": today(),
				"posting_time": nowtime(),
				"set_posting_time": 1,
				"items": [
					{
						"item_code": item_code,
						"qty": 1,
						"received_qty": 1,
						"rate": 10,
						"warehouse": self.warehouse,
						"batch_no": batch_id,
						"use_serial_batch_fields": 1,
						"purchase_order": po.name,
						"purchase_order_item": po.items[0].name,
					}
				],
			}
		)
		pr.insert(ignore_permissions=True)
		pr.submit()
		return pr

	def _create_draft_qi(self, item_code, batch_id, pr_name):
		# Mirrors utils.quality_inspection_tools.create_draft_quality_inspection,
		# which sets ignore_validate=True for exactly this reason: to let a
		# draft QI be created for a flag-off item without tripping core
		# validation at insert time. The bug this override fixes only shows
		# up later, at submit.
		qi = frappe.get_doc(
			{
				"doctype": "Quality Inspection",
				"inspection_type": "Incoming",
				"reference_type": "Purchase Receipt",
				"reference_name": pr_name,
				"item_code": item_code,
				"batch_no": batch_id,
				"sample_size": 1,
				"status": "Accepted",
				"inspected_by": "Administrator",
				"report_date": today(),
			}
		)
		qi.flags.ignore_validate = True
		qi.insert(ignore_permissions=True)
		# Fresh object: a real submit (e.g. from the desk UI) never carries
		# over the draft-creation ignore_validate flag.
		return frappe.get_doc("Quality Inspection", qi.name)

	def test_in_scope_item_flag_off_submits(self):
		batch_id = "_TEST-PHARMA-QI-IN-SCOPE-BATCH"
		pr = self._submit_purchase_receipt(self.in_scope_item, batch_id)
		qi = self._create_draft_qi(self.in_scope_item, batch_id, pr.name)

		qi.submit()

		self.assertEqual(qi.docstatus, 1)

	def test_out_of_scope_item_flag_off_still_raises(self):
		batch_id = "_TEST-PHARMA-QI-OUT-OF-SCOPE-BATCH"
		pr = self._submit_purchase_receipt(self.out_of_scope_item, batch_id)
		qi = self._create_draft_qi(self.out_of_scope_item, batch_id, pr.name)

		self.assertRaises(frappe.ValidationError, qi.submit)


def _ensure_company(company_name, abbr):
	# Independent of whether the company itself already exists: a PO/PR
	# dated today always needs a Fiscal Year covering today.
	_ensure_fiscal_year(getdate(today()))

	if frappe.db.exists("Company", company_name):
		return company_name

	# Company.create_default_warehouses() creates a "Goods In Transit"
	# warehouse with warehouse_type="Transit". On a site that never went
	# through the setup wizard (e.g. a bare install-app), that Warehouse
	# Type master record doesn't exist yet, and the Link validation on
	# Warehouse.warehouse_type throws. Normally seeded by the setup wizard;
	# not our app's concern, just a prerequisite for this fixture.
	if not frappe.db.exists("Warehouse Type", "Transit"):
		frappe.get_doc({"doctype": "Warehouse Type", "name": "Transit"}).insert(ignore_permissions=True)

	company = frappe.get_doc(
		{
			"doctype": "Company",
			"company_name": company_name,
			"abbr": abbr,
			"default_currency": "USD",
			"country": "United States",
			# Keeps this fixture GL-free: no default accounts (stock
			# received but not billed, etc.) need to be configured for a
			# Purchase Receipt to submit. Irrelevant to what this override
			# actually gates (validate_inspection_required only cares about
			# workflow-enabled + item scope), so there's nothing lost by
			# skipping it.
			"enable_perpetual_inventory": 0,
		}
	)
	company.insert(ignore_permissions=True)
	return company.name


def _ensure_fiscal_year(for_date):
	# Also setup-wizard-only master data: a Purchase Order/Receipt dated
	# today needs a Fiscal Year covering today. Global (no company
	# restriction), so it covers whatever company this fixture uses.
	existing = frappe.db.exists(
		"Fiscal Year", {"year_start_date": ("<=", for_date), "year_end_date": (">=", for_date)}
	)
	if existing:
		return existing

	year_start = getdate(f"{for_date.year}-01-01")
	year_end = getdate(f"{for_date.year}-12-31")
	fiscal_year = frappe.get_doc(
		{
			"doctype": "Fiscal Year",
			"year": str(for_date.year),
			"year_start_date": year_start,
			"year_end_date": year_end,
		}
	)
	fiscal_year.insert(ignore_permissions=True)
	return fiscal_year.name


def _ensure_supplier(supplier_name):
	if frappe.db.exists("Supplier", supplier_name):
		return supplier_name

	supplier_group = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
	supplier = frappe.get_doc(
		{
			"doctype": "Supplier",
			"supplier_name": supplier_name,
			"supplier_group": supplier_group,
			"supplier_type": "Company",
		}
	)
	supplier.insert(ignore_permissions=True)
	return supplier.name


def _ensure_item_group(item_group_name):
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


def _ensure_item(item_code, item_group):
	if frappe.db.exists("Item", item_code):
		return item_code

	# Like Warehouse Type above: UOM master data ("Nos" included) is only
	# seeded by the setup wizard, not by a bare install-app. Not our app's
	# concern, just a prerequisite for this fixture.
	if not frappe.db.exists("UOM", "Nos"):
		frappe.get_doc({"doctype": "UOM", "uom_name": "Nos"}).insert(ignore_permissions=True)

	item = frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": item_code,
			"item_name": item_code,
			"item_group": item_group,
			"stock_uom": "Nos",
			"is_stock_item": 1,
			"is_purchase_item": 1,
			"has_batch_no": 1,
			"inspection_required_before_purchase": 0,
		}
	)
	item.insert(ignore_permissions=True)
	return item.name
