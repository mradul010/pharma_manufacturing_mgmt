import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from pharma_manufacturing_mgmt.utils.batch_tools import (
	get_latest_submitted_quality_inspection,
	get_row_batches,
)
from pharma_manufacturing_mgmt.utils.ipc_tools import get_ipc_template
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

JOB_CARD_STATUS_MAP = {
	"Open": "Pending",
	"Work In Progress": "In Process",
	"Partially Transferred": "In Process",
	"Material Transferred": "In Process",
	"Submitted": "In Process",
	"On Hold": "On Hold",
	"Completed": "Completed",
	"Cancelled": "Pending",
}


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
	_snapshot_stages(bmr, doc)
	_snapshot_ipc(bmr, doc)
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


def _snapshot_stages(bmr, doc):
	for row in doc.get("operations") or []:
		bmr.append(
			"stages",
			{
				"stage_no": row.idx,
				"stage_name": row.operation,
				"operation": row.operation,
				"workstation": row.workstation,
				"planned_qty": doc.qty,
				"status": "Pending",
			},
		)


def _snapshot_ipc(bmr, doc):
	for row in doc.get("operations") or []:
		template_name = get_ipc_template(row.operation, bmr.product)
		if not template_name:
			continue

		template = frappe.get_doc("Quality Inspection Template", template_name)
		for param in template.item_quality_inspection_parameter:
			already_seeded = any(
				ipc.stage == row.operation and ipc.parameter == param.specification for ipc in bmr.ipc_records
			)
			if already_seeded:
				continue

			bmr.append(
				"ipc_records",
				{
					"stage": row.operation,
					"parameter": param.specification,
					"specification": _format_spec(param),
					"limit_min": param.min_value if param.numeric else None,
					"limit_max": param.max_value if param.numeric else None,
				},
			)


def _format_spec(param):
	if not param.numeric:
		return param.value

	if param.min_value and param.max_value:
		return "{0} – {1}".format(param.min_value, param.max_value)
	if param.max_value:
		return "NMT {0}".format(param.max_value)
	if param.min_value:
		return "NLT {0}".format(param.min_value)
	return ""


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


def sync_bmr_stage_from_job_card(doc, method=None):
	if not doc.work_order:
		return

	bmr_name = frappe.db.exists("Batch Manufacturing Record", {"work_order": doc.work_order})
	if not bmr_name:
		return

	bmr = frappe.get_doc("Batch Manufacturing Record", bmr_name)
	if bmr.docstatus != 0:
		bmr.add_comment(
			"Comment",
			_("Job Card {0} updated after BMR submission — not synced.").format(doc.name),
		)
		return

	stage = _find_or_append_stage(bmr, doc)
	stage.job_card = doc.name
	stage.performed_by = _resolve_performed_by(doc)
	stage.started_at = doc.actual_start_date or _time_log_bound(doc, "from_time", min)
	stage.completed_at = doc.actual_end_date or _time_log_bound(doc, "to_time", max)
	stage.completed_qty = doc.total_completed_qty
	stage.status = JOB_CARD_STATUS_MAP.get(doc.status, stage.status)

	bmr.flags.ignore_permissions = True
	bmr.save()
	LOGGER.info("BMR %s stage synced from Job Card %s", bmr.name, doc.name)


def _find_or_append_stage(bmr, doc):
	for row in bmr.stages:
		if row.operation != doc.operation:
			continue
		if row.workstation and doc.workstation and row.workstation != doc.workstation:
			continue
		return row

	return bmr.append(
		"stages",
		{
			"stage_name": doc.operation,
			"operation": doc.operation,
			"workstation": doc.workstation,
			"planned_qty": doc.for_quantity,
			"status": "Pending",
		},
	)


def _resolve_performed_by(doc):
	for row in doc.get("time_logs") or []:
		if row.employee:
			user = frappe.db.get_value("Employee", row.employee, "user_id")
			if user:
				return user

	return doc.owner


def _time_log_bound(doc, fieldname, agg_fn):
	values = [row.get(fieldname) for row in doc.get("time_logs") or [] if row.get(fieldname)]
	if not values:
		return None

	return agg_fn(values)


def sync_bmr_ipc_from_qi(doc, method=None):
	if doc.reference_type != "Job Card" or not doc.reference_name:
		return

	work_order = frappe.db.get_value("Job Card", doc.reference_name, "work_order")
	bmr_name = work_order and frappe.db.exists("Batch Manufacturing Record", {"work_order": work_order})
	if not bmr_name:
		return

	bmr = frappe.get_doc("Batch Manufacturing Record", bmr_name)
	if bmr.docstatus != 0:
		bmr.add_comment(
			"Comment",
			_("In-process QI {0} submitted after BMR submission — not synced.").format(doc.name),
		)
		return

	stage_name = _stage_name_for_job_card(bmr, doc.reference_name)
	for reading in doc.get("readings") or []:
		ipc_row = _find_or_append_ipc(bmr, stage_name, reading.specification)
		ipc_row.observed_value = _join_readings(reading)
		ipc_row.result = "Pass" if reading.status == "Accepted" else "Fail"
		ipc_row.tested_by = doc.inspected_by or doc.verified_by
		ipc_row.test_time = doc.report_date
		ipc_row.ar_no = doc.get("custom_ar_number") or doc.name
		ipc_row.quality_inspection = doc.name
		ipc_row.job_card = doc.reference_name

		if ipc_row.result == "Fail":
			_append_ipc_deviation(bmr, ipc_row)

	bmr.flags.ignore_permissions = True
	bmr.save()
	LOGGER.info("BMR %s IPC synced from Quality Inspection %s", bmr.name, doc.name)


def clear_bmr_ipc_for_qi(doc, method=None):
	if doc.reference_type != "Job Card" or not doc.reference_name:
		return

	work_order = frappe.db.get_value("Job Card", doc.reference_name, "work_order")
	bmr_name = work_order and frappe.db.exists("Batch Manufacturing Record", {"work_order": work_order})
	if not bmr_name:
		return

	bmr = frappe.get_doc("Batch Manufacturing Record", bmr_name)
	if bmr.docstatus != 0:
		bmr.add_comment(
			"Comment",
			_("In-process QI {0} cancelled after BMR submission — not synced.").format(doc.name),
		)
		return

	changed = False
	for row in bmr.ipc_records:
		if row.quality_inspection != doc.name:
			continue

		row.observed_value = ""
		row.result = ""
		row.tested_by = ""
		row.test_time = None
		row.ar_no = ""
		row.quality_inspection = ""
		row.job_card = ""
		changed = True

	if not changed:
		return

	bmr.flags.ignore_permissions = True
	bmr.save()
	LOGGER.info("BMR %s IPC cleared for cancelled Quality Inspection %s", bmr.name, doc.name)


def _stage_name_for_job_card(bmr, job_card):
	operation = frappe.db.get_value("Job Card", job_card, "operation")
	if operation:
		return operation

	for row in bmr.stages:
		if row.job_card == job_card:
			return row.operation or row.stage_name

	return None


def _join_readings(reading):
	values = [reading.get("reading_{0}".format(i)) for i in range(1, 11)]
	return " / ".join(value for value in values if value)


def _find_or_append_ipc(bmr, stage, parameter):
	for row in bmr.ipc_records:
		if row.stage == stage and row.parameter == parameter:
			return row

	return bmr.append("ipc_records", {"stage": stage, "parameter": parameter})


def _append_ipc_deviation(bmr, ipc_row):
	marker = "{0}: {1}".format(ipc_row.stage, ipc_row.parameter)
	already_mentioned = any(
		dev.description and marker in dev.description for dev in (bmr.deviations or [])
	)
	if already_mentioned:
		return

	bmr.append(
		"deviations",
		{
			"description": _("IPC failure at stage {0}: {1} observed {2}, spec {3}").format(
				ipc_row.stage, ipc_row.parameter, ipc_row.observed_value, ipc_row.specification
			),
			"classification": "Major",
			"status": "Open",
		},
	)
