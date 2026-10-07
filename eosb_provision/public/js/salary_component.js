frappe.ui.form.on("Salary Component", {
	setup(frm) {
		const q = (field) =>
			frm.set_query(field, "eosb_accounts", (doc, cdt, cdn) => {
				const row = locals[cdt][cdn];
				return { filters: { company: row.company, is_group: 0 } };
			});
		q("expense_account");
		q("provision_account");
	},
});
