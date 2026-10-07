frappe.query_reports["EOSB Provision Balance"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			reqd: 1,
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "as_of_date",
			label: __("As of Date"),
			fieldtype: "Date",
			reqd: 1,
			default: frappe.datetime.get_today(),
		},
		{ fieldname: "department", label: __("Department"), fieldtype: "Link", options: "Department" },
		{ fieldname: "employee", label: __("Employee"), fieldtype: "Link", options: "Employee" },
		{
			fieldname: "show",
			label: __("Show"),
			fieldtype: "Select",
			options: "All\nIn Provision\nNot in Provision",
			default: "All",
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;
		if (["eosb_unbooked", "leave_unbooked"].includes(column.fieldname) && data[column.fieldname]) {
			value = `<span style="color:#a45a06;font-weight:600">${value}</span>`;
		}
		if (column.fieldname === "note" && data.note) {
			value = `<span style="color:#b42318">${value}</span>`;
		}
		return value;
	},
};
