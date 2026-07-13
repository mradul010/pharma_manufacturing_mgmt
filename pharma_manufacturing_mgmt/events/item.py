import frappe
from frappe import _

from pharma_manufacturing_mgmt.utils.settings import (
	get_applicable_item_groups,
	workflow_enabled_for_doc,
)

LOGGER = frappe.logger("pharma_qc")


def clear_pr_inspection_flag(doc, method=None):
	LOGGER.info("Item validate hook started for %s", doc.name)
	if not workflow_enabled_for_doc():
		return

	if not _is_doc_item_in_scope(doc):
		LOGGER.info("Item %s skipped as out of scope", doc.name)
		return

	if not doc.get("inspection_required_before_purchase"):
		return

	doc.inspection_required_before_purchase = 0
	LOGGER.info("Item %s PR inspection flag cleared by pharma quarantine workflow", doc.name)
	frappe.msgprint(
		_("This app replaces PR-level inspection with quarantine-release QC."),
		alert=True,
	)


def _is_doc_item_in_scope(doc) -> bool:
	groups = get_applicable_item_groups()
	if not groups or not doc.item_group:
		return False

	item_bounds = frappe.get_cached_value("Item Group", doc.item_group, ["lft", "rgt"], as_dict=True)
	if not item_bounds:
		return False

	for group in groups:
		group_bounds = frappe.get_cached_value("Item Group", group, ["lft", "rgt"], as_dict=True)
		if not group_bounds:
			continue

		if item_bounds.lft >= group_bounds.lft and item_bounds.rgt <= group_bounds.rgt:
			return True

	return False
