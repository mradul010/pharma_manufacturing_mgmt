import frappe
from frappe.model.naming import make_autoname


def execute():
	quality_inspections = frappe.db.sql(
		"""
		SELECT name
		FROM `tabQuality Inspection`
		WHERE IFNULL(custom_ar_number, '') = ''
		ORDER BY creation ASC
		""",
		as_dict=True,
	)

	for quality_inspection in quality_inspections:
		frappe.db.set_value(
			"Quality Inspection",
			quality_inspection.name,
			"custom_ar_number",
			make_autoname("AR-.YY.-.#####"),
			update_modified=False,
		)

	frappe.db.commit()
	print("Backfilled A.R. numbers for {0} Quality Inspection(s).".format(len(quality_inspections)))
