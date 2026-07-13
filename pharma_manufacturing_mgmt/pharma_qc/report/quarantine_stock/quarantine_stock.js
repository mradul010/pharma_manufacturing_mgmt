frappe.query_reports["Quarantine Stock"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "warehouse",
			label: __("Warehouse"),
			fieldtype: "Link",
			options: "Warehouse",
			get_query: function () {
				return {
					query: "pharma_manufacturing_mgmt.utils.settings.quarantine_warehouse_query",
					filters: {
						company: frappe.query_report.get_filter_value("company"),
					},
				};
			},
		},
		{
			fieldname: "item_code",
			label: __("Item Code"),
			fieldtype: "Link",
			options: "Item",
			get_query: function () {
				return {
					filters: {
						has_batch_no: 1,
					},
				};
			},
		},
		{
			fieldname: "qc_status",
			label: __("QC Status"),
			fieldtype: "Select",
			options: "\nQuarantine\nUnder Test\nApproved\nRejected",
		},
		{
			fieldname: "ageing_bucket",
			label: __("Ageing Bucket"),
			fieldtype: "Select",
			options: "\n0-3 days\n4-7 days\n>7 days",
		},
	],
};
