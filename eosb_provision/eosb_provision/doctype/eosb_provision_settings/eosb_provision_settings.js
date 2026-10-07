frappe.ui.form.on("EOSB Provision Settings", {
	setup(frm) {
		[
			"default_expense_account",
			"default_provision_account",
			"leave_expense_account",
			"leave_provision_account",
		].forEach((field) =>
			frm.set_query(field, "company_accounts", (doc, cdt, cdn) => ({
				filters: { company: locals[cdt][cdn].company, is_group: 0 },
			}))
		);
		frm.set_query("cost_center", "company_accounts", (doc, cdt, cdn) => ({
			filters: { company: locals[cdt][cdn].company, is_group: 0 },
		}));
	},
});
