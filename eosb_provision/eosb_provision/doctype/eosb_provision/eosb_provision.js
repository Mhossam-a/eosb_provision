// Copyright (c) 2026
frappe.ui.form.on("EOSB Provision", {
	setup(frm) {
		frm.set_query("department", () => ({ filters: { company: frm.doc.company } }));
		frm.set_query("employee", "employees", () => ({
			filters: { company: frm.doc.company, status: "Active" },
		}));
	},

	refresh(frm) {
		frm.trigger("toggle_opening_fields");

		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(__("Get Employees"), () => frm.trigger("get_employees")).addClass(
				"btn-primary"
			);
			if ((frm.doc.employees || []).length) {
				frm.add_custom_button(__("Calculate"), () => {
					frm.events.calculate(frm);
				});
			}
		}
		if (frm.doc.journal_entry) {
			frm.add_custom_button(__("Journal Entry"), () =>
				frappe.set_route("Form", "Journal Entry", frm.doc.journal_entry)
			);
		}
		if (frm.doc.is_opening && frm.doc.docstatus === 0) {
			frm.dashboard.set_headline(
				__(
					"مخصص افتتاحي: مش هيتعمل قيد. ممكن تكتب الرصيد القديم الفعلي لكل موظف في EOSB Amount و Leave Amount قبل الـ Submit"
				),
				"orange"
			);
		}
	},

	is_opening(frm) {
		frm.trigger("toggle_opening_fields");
		if (frm.doc.employees && frm.doc.employees.length) {
			frm.events.calculate(frm);
		}
	},

	posting_date(frm) {
		// التاريخ اتغير: إعادة حساب الموظفين اللي في الجدول على التاريخ الجديد
		if (frm.doc.docstatus === 0 && (frm.doc.employees || []).length) {
			frm.events.calculate(frm);
		}
	},

	calculate(frm, employees) {
		if (!frm.doc.company || !frm.doc.posting_date) {
			frappe.msgprint(__("اختار الشركة والتاريخ الأول"));
			return;
		}
		return frappe.call({
			doc: frm.doc,
			method: "calculate",
			args: employees ? { employees: employees } : {},
			freeze: true,
			freeze_message: __("جاري الحساب..."),
			callback(r) {
				frm.dirty();
				frm.refresh();
				frm.events.show_notes(frm, r.message || [], !employees);
			},
		});
	},

	show_notes(frm, notes, always) {
		if (notes.length > 1 || always) {
			frappe.msgprint({
				title: __("ملاحظات الحسبة"),
				indicator: notes.length > 1 ? "orange" : "green",
				message: notes
					.map((n) => frappe.utils.escape_html(n).replace(/\n/g, "<br>"))
					.join("<br><br>"),
			});
		}
	},

	toggle_opening_fields(frm) {
		const grid = frm.fields_dict.employees.grid;
		const editable = frm.doc.is_opening && frm.doc.docstatus === 0;
		grid.update_docfield_property("allocated_amount", "read_only", editable ? 0 : 1);
		grid.update_docfield_property("total_leave_amount", "read_only", editable ? 0 : 1);
		frm.refresh_field("employees");
	},

	get_employees(frm) {
		if (!frm.doc.company || !frm.doc.posting_date) {
			frappe.msgprint(__("اختار الشركة والتاريخ الأول"));
			return;
		}
		frappe.call({
			doc: frm.doc,
			method: "get_employees",
			freeze: true,
			freeze_message: __("جاري حساب المخصص..."),
			callback(r) {
				frm.dirty();
				frm.refresh();
				frappe.show_alert({
					message: __("اتجاب {0} موظف", [(frm.doc.employees || []).length]),
					indicator: "green",
				});
				frm.events.show_notes(frm, r.message || [], false);
			},
		});
	},
});

frappe.ui.form.on("EOSB Provision Employee", {
	allocated_amount(frm) {
		frm.dirty();
	},

	employee(frm, cdt, cdn) {
		// أول ما تختار موظف في الجدول بيتحسب لوحده
		const row = locals[cdt][cdn];
		if (!row.employee || frm.doc.docstatus !== 0) return;
		const dup = (frm.doc.employees || []).filter((e) => e.employee === row.employee).length > 1;
		if (dup) {
			frappe.show_alert({ message: __("الموظف {0} موجود في الجدول", [row.employee]), indicator: "orange" });
			frappe.model.set_value(cdt, cdn, "employee", "");
			return;
		}
		frm.events.calculate(frm, [row.employee]);
	},
});
