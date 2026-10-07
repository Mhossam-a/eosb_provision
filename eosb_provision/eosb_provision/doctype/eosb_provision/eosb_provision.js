// Copyright (c) 2026
frappe.ui.form.on("EOSB Provision", {
	setup(frm) {
		frm.set_query("department", () => ({ filters: { company: frm.doc.company } }));
	},

	refresh(frm) {
		frm.trigger("toggle_opening_fields");

		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(__("Get Employees"), () => frm.trigger("get_employees")).addClass(
				"btn-primary"
			);
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
			frm.trigger("get_employees");
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
				const notes = r.message || [];
				frappe.show_alert({
					message: __("اتجاب {0} موظف", [(frm.doc.employees || []).length]),
					indicator: "green",
				});
				if (notes.length > 1) {
					frappe.msgprint({
						title: __("ملاحظات الحسبة"),
						indicator: "orange",
						message: notes.join("<br><br>"),
					});
				}
			},
		});
	},
});

frappe.ui.form.on("EOSB Provision Employee", {
	allocated_amount(frm) {
		frm.dirty();
	},
});
