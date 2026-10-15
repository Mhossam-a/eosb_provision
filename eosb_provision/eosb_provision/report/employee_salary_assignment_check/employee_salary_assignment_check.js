frappe.query_reports["Employee Salary Assignment Check"] = {
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
		{ fieldname: "only_problems", label: __("Only Problems"), fieldtype: "Check", default: 0 },
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;
		if (column.fieldname === "ssa_on_joining") {
			const ok = data.ssa_on_joining === "✓";
			value = `<span style="color:${ok ? "#067647" : "#b42318"};font-weight:600">${value}</span>`;
		}
		if (column.fieldname === "problem" && data.problem) {
			value = `<span style="color:#b42318">${value}</span>`;
		}
		return value;
	},
};
