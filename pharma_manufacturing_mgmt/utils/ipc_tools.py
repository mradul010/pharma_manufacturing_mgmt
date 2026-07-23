import frappe


def get_ipc_template(operation: str, item_code: str | None = None) -> str | None:
	if not operation:
		return None

	item_group = frappe.get_cached_value("Item", item_code, "item_group") if item_code else None

	candidates = frappe.get_all(
		"Pharma Operation IPC",
		filters={"operation": operation, "is_active": 1},
		fields=["name", "quality_inspection_template", "item", "item_group"],
	)

	item_match = next((c for c in candidates if item_code and c.item == item_code), None)
	if item_match:
		return item_match.quality_inspection_template

	group_match = next((c for c in candidates if item_group and c.item_group == item_group), None)
	if group_match:
		return group_match.quality_inspection_template

	blank_match = next((c for c in candidates if not c.item and not c.item_group), None)
	return blank_match.quality_inspection_template if blank_match else None
