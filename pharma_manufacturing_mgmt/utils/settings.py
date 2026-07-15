import frappe
from frappe import _
from frappe.utils import cint


QC_STAGE_RM = "RM"
QC_STAGE_FG = "FG"
RELEASE_MODE_MANUAL = "Manual"
RELEASE_MODE_AUTO_DRAFT = "Auto Draft"
RELEASE_MODE_AUTO_SUBMIT = "Auto Submit"
SHELF_LIFE_ACTION_WARN = "Warn"
SHELF_LIFE_ACTION_STOP = "Stop"

DEFAULT_RELEASE_ROLE = "Pharma QA"

REQUIRED_WAREHOUSE_FIELDS = (
	"rm_quarantine_warehouse",
	"rm_approved_warehouse",
	"fg_quarantine_warehouse",
	"fg_approved_warehouse",
	"rejected_warehouse",
)


def get_pharma_settings():
	if not frappe.db.exists("DocType", "Pharma Settings"):
		return frappe._dict(
			{
				"enable_quarantine_workflow": 0,
				"auto_create_quality_inspection": 1,
				"auto_submit_release_transfer": 0,
				"release_mode": RELEASE_MODE_MANUAL,
				"restrict_quarantine_transfers": 1,
				"quarantine_release_role": DEFAULT_RELEASE_ROLE,
				"min_shelf_life_days_for_dispatch": 0,
				"shelf_life_action": SHELF_LIFE_ACTION_WARN,
				"rm_quarantine_warehouse": "",
				"rm_approved_warehouse": "",
				"fg_quarantine_warehouse": "",
				"fg_approved_warehouse": "",
				"rejected_warehouse": "",
			}
		)

	return frappe.get_cached_doc("Pharma Settings")


def is_workflow_enabled(settings=None) -> bool:
	settings = settings or get_pharma_settings()
	return bool(cint(settings.get("enable_quarantine_workflow")))


def is_quarantine_workflow_enabled(settings=None) -> bool:
	return is_workflow_enabled(settings)


def workflow_enabled_for_doc() -> bool:
	try:
		return is_workflow_enabled()
	except frappe.DoesNotExistError:
		return False


def has_configured_quarantine_warehouses(settings=None) -> bool:
	settings = settings or get_pharma_settings()
	return all(settings.get(fieldname) for fieldname in REQUIRED_WAREHOUSE_FIELDS)


def is_quarantine_workflow_active(settings=None) -> bool:
	settings = settings or get_pharma_settings()
	return is_workflow_enabled(settings) and has_configured_quarantine_warehouses(settings)


def quarantine_workflow_active_for_doc() -> bool:
	try:
		return is_quarantine_workflow_active()
	except frappe.DoesNotExistError:
		return False


def get_applicable_item_groups(settings=None) -> list[str]:
	settings = settings or get_pharma_settings()
	return [row.item_group for row in settings.get("applicable_item_groups", []) if row.item_group]


def is_item_in_scope(item_code: str) -> bool:
	if not item_code:
		return False

	groups = get_applicable_item_groups()
	cache_key = (item_code, tuple(groups))
	cache = getattr(frappe.local, "pharma_qc_item_scope_cache", None)
	if cache is None:
		cache = frappe.local.pharma_qc_item_scope_cache = {}

	if cache_key in cache:
		return cache[cache_key]

	if not groups:
		cache[cache_key] = False
		return False

	item_group = frappe.get_cached_value("Item", item_code, "item_group")
	if not item_group:
		cache[cache_key] = False
		return False

	item_bounds = frappe.get_cached_value("Item Group", item_group, ["lft", "rgt"], as_dict=True)
	if not item_bounds:
		cache[cache_key] = False
		return False

	for group in groups:
		group_bounds = frappe.get_cached_value("Item Group", group, ["lft", "rgt"], as_dict=True)
		if not group_bounds:
			continue

		if item_bounds.lft >= group_bounds.lft and item_bounds.rgt <= group_bounds.rgt:
			cache[cache_key] = True
			return True

	cache[cache_key] = False
	return False


def get_rm_quarantine_warehouse(settings=None) -> str:
	settings = settings or get_pharma_settings()
	return settings.get("rm_quarantine_warehouse") or ""


def get_rm_approved_warehouse(settings=None) -> str:
	settings = settings or get_pharma_settings()
	return settings.get("rm_approved_warehouse") or ""


def get_fg_quarantine_warehouse(settings=None) -> str:
	settings = settings or get_pharma_settings()
	return settings.get("fg_quarantine_warehouse") or ""


def get_fg_approved_warehouse(settings=None) -> str:
	settings = settings or get_pharma_settings()
	return settings.get("fg_approved_warehouse") or ""


def get_rejected_warehouse(settings=None) -> str:
	settings = settings or get_pharma_settings()
	return settings.get("rejected_warehouse") or ""


def get_quarantine_warehouses(settings=None) -> list[str]:
	settings = settings or get_pharma_settings()
	return _unique(
		warehouse
		for warehouse in (get_rm_quarantine_warehouse(settings), get_fg_quarantine_warehouse(settings))
		if warehouse
	)


def get_approved_warehouses(settings=None) -> list[str]:
	settings = settings or get_pharma_settings()
	return _unique(
		warehouse
		for warehouse in (get_rm_approved_warehouse(settings), get_fg_approved_warehouse(settings))
		if warehouse
	)


def get_approved_warehouse(quarantine_warehouse: str, settings=None) -> str:
	settings = settings or get_pharma_settings()
	if not quarantine_warehouse:
		return ""

	if quarantine_warehouse == get_rm_quarantine_warehouse(settings):
		return get_rm_approved_warehouse(settings)

	if quarantine_warehouse == get_fg_quarantine_warehouse(settings):
		return get_fg_approved_warehouse(settings)

	return ""


def get_stage_for_quarantine_warehouse(quarantine_warehouse: str, settings=None) -> str:
	settings = settings or get_pharma_settings()
	if quarantine_warehouse and quarantine_warehouse == get_rm_quarantine_warehouse(settings):
		return QC_STAGE_RM

	if quarantine_warehouse and quarantine_warehouse == get_fg_quarantine_warehouse(settings):
		return QC_STAGE_FG

	return ""


def is_quarantine_warehouse(warehouse: str, settings=None) -> bool:
	return bool(warehouse and warehouse in get_quarantine_warehouses(settings))


def is_approved_warehouse(warehouse: str, settings=None) -> bool:
	return bool(warehouse and warehouse in get_approved_warehouses(settings))


def get_release_role(settings=None) -> str:
	settings = settings or get_pharma_settings()
	return settings.get("quarantine_release_role") or DEFAULT_RELEASE_ROLE


def should_auto_create_qi(settings=None) -> bool:
	settings = settings or get_pharma_settings()
	return bool(cint(settings.get("auto_create_quality_inspection", 1)))


def should_auto_submit_release_transfer(settings=None) -> bool:
	settings = settings or get_pharma_settings()
	return get_release_mode(settings) == RELEASE_MODE_AUTO_SUBMIT


def get_release_mode(settings=None) -> str:
	settings = settings or get_pharma_settings()
	mode = settings.get("release_mode") or RELEASE_MODE_MANUAL
	if mode not in (RELEASE_MODE_MANUAL, RELEASE_MODE_AUTO_DRAFT, RELEASE_MODE_AUTO_SUBMIT):
		return RELEASE_MODE_MANUAL

	return mode


def get_min_shelf_life_days_for_dispatch(settings=None) -> int:
	settings = settings or get_pharma_settings()
	return cint(settings.get("min_shelf_life_days_for_dispatch") or 0)


def get_shelf_life_action(settings=None) -> str:
	settings = settings or get_pharma_settings()
	action = settings.get("shelf_life_action") or SHELF_LIFE_ACTION_WARN
	if action not in (SHELF_LIFE_ACTION_WARN, SHELF_LIFE_ACTION_STOP):
		return SHELF_LIFE_ACTION_WARN

	return action


def should_restrict_quarantine_transfers(settings=None) -> bool:
	settings = settings or get_pharma_settings()
	return bool(cint(settings.get("restrict_quarantine_transfers", 1)))


@frappe.whitelist()
def get_dispatch_defaults(item_code: str | None = None):
	settings = get_pharma_settings()
	return {
		"is_item_in_scope": is_item_in_scope(item_code) if item_code else False,
		"fg_approved_warehouse": get_fg_approved_warehouse(settings),
	}


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def quarantine_warehouse_query(doctype, txt, searchfield, start, page_len, filters):
	warehouses = get_quarantine_warehouses()
	if not warehouses:
		return []

	warehouse = frappe.qb.DocType("Warehouse")
	query = (
		frappe.qb.from_(warehouse)
		.select(warehouse.name)
		.where(warehouse.name.isin(warehouses))
		.where(warehouse[searchfield].like("%{0}%".format(txt)))
		.orderby(warehouse.name)
		.limit(page_len)
		.offset(start)
	)

	if filters and filters.get("company"):
		query = query.where(warehouse.company == filters.get("company"))

	return query.run()


def _unique(values) -> list[str]:
	seen = set()
	out = []
	for value in values:
		if value in seen:
			continue
		seen.add(value)
		out.append(value)
	return out
