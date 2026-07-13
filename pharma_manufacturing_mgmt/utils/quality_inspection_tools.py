import frappe
from frappe import _


LOGGER = frappe.logger("pharma_qc")


def create_draft_quality_inspection(
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
	existing = get_existing_quality_inspection(reference_type, reference_name, item_code, batch_no)
	if existing:
		LOGGER.info(
			"Existing Quality Inspection %s found for %s %s item %s batch %s",
			existing,
			reference_type,
			reference_name,
			item_code,
			batch_no,
		)
		return existing

	company = company or _get_reference_company(reference_type, reference_name)
	template = frappe.get_cached_value("Item", item_code, "quality_inspection_template")
	qi = frappe.get_doc(
		{
			"doctype": "Quality Inspection",
			"inspection_type": inspection_type,
			"reference_type": reference_type,
			"reference_name": reference_name,
			"item_code": item_code,
			"batch_no": batch_no,
			"sample_size": sample_size,
			"company": company,
			"child_row_reference": child_row_reference,
		}
	)
	if template:
		qi.quality_inspection_template = template
		qi.get_item_specification_details()
	else:
		LOGGER.info("QI template missing for item %s; draft QI will be created without readings", item_code)
		_add_reference_comment(
			comment_reference,
			reference_type,
			reference_name,
			_(
				"Draft Quality Inspection for item {0}, batch {1} was created without template readings because no Quality Inspection Template is configured."
			).format(item_code, batch_no),
		)

	if frappe.session.user != "Guest":
		qi.inspected_by = frappe.session.user

	qi.flags.ignore_validate = True
	qi.insert(ignore_permissions=True)
	LOGGER.info(
		"Draft Quality Inspection %s created for %s %s item %s batch %s",
		qi.name,
		reference_type,
		reference_name,
		item_code,
		batch_no,
	)
	return qi.name


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
