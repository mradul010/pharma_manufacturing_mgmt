import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from pharma_manufacturing_mgmt.utils.batch_tools import (
	get_latest_submitted_quality_inspection,
	get_row_batches,
)
from pharma_manufacturing_mgmt.utils.settings import get_release_role, is_item_in_scope


LOGGER = frappe.logger("pharma_qc")

ALLOWED_TRANSITIONS = {
	"Draft": ["Dispensed", "On Hold"],
	"Dispensed": ["In-Process", "On Hold"],
	"In-Process": ["Bulk QC", "On Hold"],
	"Bulk QC": ["Packing", "On Hold"],
	"Packing": ["QA Review", "On Hold"],
	"QA Review": ["Released", "Rejected", "On Hold"],
	"On Hold": ["Draft", "Dispensed", "In-Process", "Bulk QC", "Packing", "QA Review"],
	"Released": [],
	"Rejected": [],
}

GATED_STATUSES = ("QA Review", "Released", "Rejected")


class BatchManufacturingRecord(Document):
	def validate(self):
		self.compute_yields()
		self.check_transition()

	def compute_yields(self):
		for row in self.yield_records or []:
			if not row.theoretical_qty:
				row.yield_pct = 0
				continue

			row.yield_pct = round(flt(row.actual_qty) / flt(row.theoretical_qty) * 100, 2)
			row.within_limit = 1 if (flt(row.limit_min) or 0) <= row.yield_pct <= (flt(row.limit_max) or 100) else 0

			if row.within_limit:
				continue

			already_mentioned = any(
				row.stage and dev.description and row.stage in dev.description for dev in (self.deviations or [])
			)
			if already_mentioned:
				continue

			self.append(
				"deviations",
				{
					"description": _("Yield out of limit at stage {0}: {1}% (limit {2}-{3}%)").format(
						row.stage, row.yield_pct, flt(row.limit_min) or 0, flt(row.limit_max) or 100
					),
					"classification": "Major",
					"status": "Open",
				},
			)

	def check_transition(self):
		if self.flags.get("system_transition"):
			return

		if self.is_new():
			return

		old = frappe.db.get_value(self.doctype, self.name, "status")
		if old == self.status:
			return

		if self.status not in ALLOWED_TRANSITIONS.get(old, []):
			frappe.throw(_("Illegal status transition from {0} to {1}.").format(old, self.status))

		if self.status in GATED_STATUSES:
			roles = frappe.get_roles()
			release_role = get_release_role()
			if release_role not in roles and "System Manager" not in roles:
				frappe.throw(
					_("Only users with the {0} role can move this BMR to {1}.").format(release_role, self.status)
				)

	def before_submit(self):
		missing = [
			self.meta.get_label(fieldname)
			for fieldname in ("reviewed_by", "released_by", "disposition")
			if not self.get(fieldname)
		]
		if missing:
			frappe.throw(_("The following fields are required before submission: {0}").format(", ".join(missing)))

		self.status = "Released" if self.disposition == "Approved" else "Rejected"
		# validate() already ran with the pre-submit status (Frappe runs validate before
		# before_submit), so re-run the transition check now that status actually changed.
		self.check_transition()

	def on_submit(self):
		if not self.batch:
			return

		frappe.get_doc("Batch", self.batch).add_comment(
			"Comment",
			_("BMR {0}: disposition {1} by {2} on {3}").format(
				self.name, self.disposition, self.released_by, frappe.utils.now_datetime()
			),
		)

	def on_cancel(self):
		if not self.batch:
			return

		frappe.get_doc("Batch", self.batch).add_comment("Comment", _("BMR {0} cancelled.").format(self.name))


def create_bmr_from_work_order(doc, method=None):
	if frappe.db.exists("Batch Manufacturing Record", {"work_order": doc.name}):
		return

	if not is_item_in_scope(doc.production_item):
		return

	bmr = frappe.get_doc(
		{
			"doctype": "Batch Manufacturing Record",
			"product": doc.production_item,
			"work_order": doc.name,
			"bom": doc.bom_no,
			"batch_size": doc.qty,
		}
	)
	_copy_optional_party(doc, bmr)
	_snapshot_formula(bmr, doc)
	bmr.insert(ignore_permissions=True)
	LOGGER.info("BMR %s created for Work Order %s", bmr.name, doc.name)
	frappe.msgprint(_("BMR {0} created for {1}").format(bmr.name, doc.name), indicator="green")


def _copy_optional_party(doc, bmr):
	if not (doc.get("custom_party") and bmr.meta.has_field("party")):
		return

	bmr.party = doc.custom_party


def _snapshot_formula(bmr, doc):
	if not doc.bom_no:
		return

	bom = frappe.get_doc("BOM", doc.bom_no)
	if not flt(bom.quantity):
		frappe.throw(
			_("BOM {0} has zero quantity; cannot compute formula proportions for Work Order {1}.").format(
				doc.bom_no, doc.name
			)
		)

	for row in bom.items:
		std_qty = flt(row.qty) / flt(bom.quantity) * flt(doc.qty)
		bmr.append(
			"formula_items",
			{
				"item_code": row.item_code,
				"item_name": row.item_name,
				"std_qty": std_qty,
				"uom": row.uom,
			},
		)

	total_std_qty = sum(flt(row.std_qty) for row in bmr.formula_items)
	if not total_std_qty:
		return

	for row in bmr.formula_items:
		row.percentage = round(flt(row.std_qty) / total_std_qty * 100, 2)


def sync_bmr_on_manufacture(doc):
	if doc.purpose != "Manufacture" or not doc.work_order:
		return

	bmr_name = frappe.db.exists("Batch Manufacturing Record", {"work_order": doc.work_order})
	if not bmr_name:
		return

	bmr = frappe.get_doc("Batch Manufacturing Record", bmr_name)
	if bmr.docstatus != 0:
		bmr.add_comment(
			"Comment",
			_("Manufacture {0} posted after BMR submission — not synced.").format(doc.name),
		)
		return

	_stamp_fg_batch(bmr, doc)
	_rebuild_dispensing_items(bmr, doc)

	if bmr.status == "Draft":
		bmr.status = "In-Process"
		bmr.flags.system_transition = True
		bmr.add_comment(
			"Comment",
			_("Status auto-advanced to In-Process: dispensing and manufacture posted via {0}.").format(doc.name),
		)

	bmr.flags.ignore_permissions = True
	bmr.save()
	LOGGER.info("BMR %s synced from Manufacture Stock Entry %s", bmr.name, doc.name)


def _stamp_fg_batch(bmr, doc):
	meta = frappe.get_meta("Stock Entry Detail")
	has_is_finished_item = meta.has_field("is_finished_item")

	for row in doc.get("items") or []:
		if has_is_finished_item:
			if not (row.get("is_finished_item") and row.get("t_warehouse")):
				continue
		elif row.item_code != bmr.product:
			continue

		batches = get_row_batches(row)
		if not batches:
			continue

		batch_no = batches[0].batch_no
		bmr.batch = batch_no
		dates = frappe.db.get_value("Batch", batch_no, ["manufacturing_date", "expiry_date"], as_dict=True)
		if dates:
			bmr.mfg_date = dates.manufacturing_date
			bmr.exp_date = dates.expiry_date
		return


def _rebuild_dispensing_items(bmr, doc):
	std_qty_by_item = {row.item_code: row.std_qty for row in bmr.formula_items}

	stock_entries = frappe.get_all(
		"Stock Entry",
		filters={
			"work_order": doc.work_order,
			"purpose": "Material Transfer for Manufacture",
			"docstatus": 1,
		},
		pluck="name",
	)

	bmr.set("dispensing_items", [])

	for se_name in stock_entries:
		rows = frappe.get_all(
			"Stock Entry Detail",
			filters={"parent": se_name, "s_warehouse": ["is", "set"]},
			fields=[
				"name",
				"item_code",
				"item_name",
				"batch_no",
				"transfer_qty",
				"qty",
				"serial_and_batch_bundle",
			],
		)

		for row in rows:
			for batch in get_row_batches(row):
				bmr.append(
					"dispensing_items",
					{
						"item_code": row.item_code,
						"item_name": row.item_name,
						"batch_no": batch.batch_no,
						"dispensed_qty": batch.qty,
						"stock_entry": se_name,
						"std_qty": std_qty_by_item.get(row.item_code, ""),
						"ar_no": _get_ar_no(batch.batch_no, row.item_code),
					},
				)


def _get_ar_no(batch_no, item_code):
	qi = get_latest_submitted_quality_inspection(batch_no, "Accepted", item_code=item_code)
	if not qi:
		return "—"

	return frappe.db.get_value("Quality Inspection", qi, "custom_ar_number") or qi
