frappe.ui.form.on("Delivery Note", {
	setup(frm) {
		frm.set_query("batch_no", "items", (doc, cdt, cdn) => {
			const row = locals[cdt][cdn];
			const filters = {
				custom_qc_status: "Approved",
			};

			if (row.item_code) {
				filters.item = row.item_code;
			}

			return { filters };
		});
	},

	items_add(frm, cdt, cdn) {
		set_default_fg_approved_warehouse(frm, cdt, cdn);
	},
});

frappe.ui.form.on("Delivery Note Item", {
	item_code(frm, cdt, cdn) {
		set_default_fg_approved_warehouse(frm, cdt, cdn);
	},
});

function set_default_fg_approved_warehouse(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	if (!row.item_code || row.warehouse) {
		return;
	}

	frappe.call({
		method: "pharma_manufacturing_mgmt.utils.settings.get_dispatch_defaults",
		args: {
			item_code: row.item_code,
		},
		callback(response) {
			const defaults = response.message || {};
			if (!defaults.is_item_in_scope || !defaults.fg_approved_warehouse) {
				return;
			}

			frappe.model.set_value(cdt, cdn, "warehouse", defaults.fg_approved_warehouse);
		},
	});
}
