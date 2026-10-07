# Copyright (c) 2026
# For license information, please see license.txt
"""
EOSB Provision Balance
لكل موظف بتاريخ معين:
  - المستحق اللحظي (نهاية الخدمة + الإجازات) بنفس حسبة EOSB Provision
  - المحجوز في آخر مخصص Submitted (شامل الافتتاحي)
  - اللي لسه ماتقيدش = المستحق − المحجوز
  - رصيد حسابات المخصص في الـ GL للموظف (للمطابقة)
والموظفين اللي سابوا أو عندهم Relieving Date بيظهروا برصيدهم المحجوز بس (مستني التسوية الفعلية).
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from eosb_provision.eosb_provision import calculation as calc


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.company:
		frappe.throw(_("اختار الشركة"))
	as_of = getdate(filters.as_of_date or nowdate())

	settings = calc.get_settings()
	rule = calc.load_rule(settings.gratuity_rule)
	live_rows, skipped, rule, settings = calc.compute_employees(
		filters.company, as_of, filters.department, filters.employee, settings, rule, with_accounts=False
	)
	live = {r.employee: r for r in live_rows}

	booked, leave_booked = calc.get_booked(filters.company, as_of)
	eosb_booked = {}
	for (emp, _comp), b in booked.items():
		eosb_booked[emp] = eosb_booked.get(emp, 0) + flt(b.required_amount)

	gl = calc.get_gl_balances(filters.company, as_of, calc.get_provision_accounts(filters.company, settings))

	# الموظفين اللي مش في الحسبة اللحظية بس ليهم رصيد محجوز (سابوا / عندهم Relieving Date / مالهمش Slip)
	others = set(eosb_booked) | set(leave_booked) | set(gl)
	others = [e for e in others if e not in live]
	emp_info = {}
	if others or live:
		for e in frappe.get_all(
			"Employee",
			filters={"name": ("in", list(others) + list(live))},
			fields=["name", "employee_name", "department", "status", "date_of_joining", "relieving_date"],
		):
			emp_info[e.name] = e

	data = []
	show = filters.show or "All"
	for emp in list(live) + sorted(others):
		info = emp_info.get(emp) or frappe._dict()
		if filters.department and info.department != filters.department:
			continue
		if filters.employee and emp != filters.employee:
			continue
		r = live.get(emp)
		in_calc = r is not None
		if show == "In Provision" and not in_calc:
			continue
		if show == "Not in Provision" and in_calc:
			continue

		eb = flt(eosb_booked.get(emp), 2)
		lb = flt(leave_booked.get(emp), 2)
		if in_calc:
			note = ""
		elif info.status == "Left" or info.relieving_date:
			note = _("ترك العمل / عنده Relieving Date: الرصيد مستني التسوية الفعلية")
		elif emp in skipped:
			note = _("مالوش Salary Slip: الرصيد زي ما هو")
		else:
			note = _("مش Active")

		eosb_live = flt(r.allocated_amount, 2) if in_calc else 0
		leave_live = flt(r.total_leave_amount, 2) if in_calc else 0
		data.append(
			{
				"employee": emp,
				"employee_name": info.employee_name,
				"department": info.department,
				"status": info.status,
				"date_of_joining": info.date_of_joining,
				"relieving_date": info.relieving_date,
				"service_text": r.service_text if in_calc else "",
				"number_of_years": r.number_of_years if in_calc else None,
				"last_salary": r.last_salary if in_calc else None,
				"eosb_live": eosb_live,
				"eosb_booked": eb,
				"eosb_unbooked": flt(eosb_live - eb, 2) if in_calc else 0,
				"leave_balance": r.leave_balance if in_calc else None,
				"leave_live": leave_live,
				"leave_booked": lb,
				"leave_unbooked": flt(leave_live - lb, 2) if in_calc else 0,
				"total_booked": flt(eb + lb, 2),
				"gl_balance": flt(gl.get(emp), 2),
				"note": note,
			}
		)

	columns = get_columns()
	summary = get_summary(data)
	chart = get_chart(data)
	return columns, data, None, chart, summary


def get_columns():
	c = lambda fieldname, label, fieldtype="Data", options=None, width=120: {  # noqa: E731
		"fieldname": fieldname, "label": label, "fieldtype": fieldtype, "options": options, "width": width
	}
	return [
		c("employee", _("Employee"), "Link", "Employee", 120),
		c("employee_name", _("Employee Name"), "Data", None, 170),
		c("department", _("Department"), "Link", "Department", 150),
		c("status", _("Status"), "Data", None, 80),
		c("date_of_joining", _("Date of Joining"), "Date", None, 105),
		c("relieving_date", _("Relieving Date"), "Date", None, 105),
		c("service_text", _("Service Period"), "Data", None, 160),
		c("number_of_years", _("Years"), "Float", None, 80),
		c("last_salary", _("Salary"), "Currency", None, 110),
		c("eosb_live", _("EOSB Due"), "Currency", None, 120),
		c("eosb_booked", _("EOSB Booked"), "Currency", None, 120),
		c("eosb_unbooked", _("EOSB Not Booked Yet"), "Currency", None, 130),
		c("leave_balance", _("Leave Days"), "Float", None, 90),
		c("leave_live", _("Leave Due"), "Currency", None, 110),
		c("leave_booked", _("Leave Booked"), "Currency", None, 110),
		c("leave_unbooked", _("Leave Not Booked Yet"), "Currency", None, 130),
		c("total_booked", _("Total Booked"), "Currency", None, 120),
		c("gl_balance", _("GL Balance (Employee)"), "Currency", None, 140),
		c("note", _("Note"), "Data", None, 260),
	]


def get_summary(data):
	s = lambda f: flt(sum(flt(d.get(f)) for d in data), 2)  # noqa: E731
	return [
		{"value": s("eosb_live") + s("leave_live"), "label": _("الالتزام المستحق (نهاية خدمة + إجازات)"), "datatype": "Currency", "indicator": "Blue"},
		{"value": s("total_booked"), "label": _("المحجوز في المخصصات"), "datatype": "Currency", "indicator": "Green"},
		{"value": s("eosb_unbooked") + s("leave_unbooked"), "label": _("لسه ماتقيدش"), "datatype": "Currency", "indicator": "Orange"},
		{"value": s("gl_balance"), "label": _("رصيد الـ GL بالموظفين"), "datatype": "Currency", "indicator": "Gray"},
	]


def get_chart(data):
	by_dept = {}
	for d in data:
		k = d.get("department") or _("بدون إدارة")
		by_dept[k] = by_dept.get(k, 0) + flt(d.get("total_booked"))
	labels = sorted(by_dept)
	return {
		"data": {"labels": labels, "datasets": [{"name": _("Total Booked"), "values": [flt(by_dept[k], 2) for k in labels]}]},
		"type": "bar",
	}
