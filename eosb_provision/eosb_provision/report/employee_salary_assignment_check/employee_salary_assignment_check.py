# Copyright (c) 2026
# For license information, please see license.txt
"""
Employee Salary Assignment Check
لكل موظف Active: عنده Salary Structure Assignment من يوم التعيين ولا لأ،
والـ Assignment اللي هيتحسب عليه المخصص في التاريخ، وأجر مكونات الـ Gratuity Rule منه،
وأي مشكلة هتخلي الموظف مايتحسبش في المخصص.
"""

import frappe
from frappe import _
from frappe.utils import date_diff, flt, getdate, nowdate

from eosb_provision.eosb_provision import calculation as calc


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.company:
		frappe.throw(_("اختار الشركة"))
	as_of = getdate(filters.as_of_date or nowdate())

	settings = calc.get_settings()
	rule_components = []
	if settings.get("gratuity_rule"):
		rule_components = calc.load_rule(settings.gratuity_rule).components

	emp_filters = {"company": filters.company, "status": "Active", "date_of_joining": ("<=", as_of)}
	if filters.department:
		emp_filters["department"] = filters.department
	employees = frappe.get_all(
		"Employee",
		filters=emp_filters,
		fields=["name", "employee_name", "department", "date_of_joining", "relieving_date"],
		order_by="department, name",
	)

	ssas = frappe.get_all(
		"Salary Structure Assignment",
		filters={"company": filters.company, "docstatus": ("<", 2), "employee": ("in", [e.name for e in employees] or [""])},
		fields=["name", "employee", "from_date", "salary_structure", "base", "docstatus"],
		order_by="from_date asc",
	)
	by_emp = {}
	drafts = {}
	for s in ssas:
		if s.docstatus == 1:
			by_emp.setdefault(s.employee, []).append(s)
		else:
			drafts[s.employee] = drafts.get(s.employee, 0) + 1

	data = []
	for e in employees:
		rows = by_emp.get(e.name, [])
		first = rows[0] if rows else None
		current = None
		for s in rows:
			if getdate(s.from_date) <= as_of:
				current = s
		used = current or first
		problems = []
		on_joining = bool(first and getdate(first.from_date) <= getdate(e.date_of_joining))
		gap = date_diff(first.from_date, e.date_of_joining) if first and not on_joining else 0

		if not rows:
			problems.append(_("مالوش Salary Structure Assignment (Submitted)"))
		elif not on_joining:
			problems.append(_("أول Assignment بيبدأ بعد التعيين بـ {0} يوم").format(gap))
		if drafts.get(e.name):
			problems.append(_("عنده {0} Assignment لسه Draft").format(drafts[e.name]))

		rule_earnings = 0
		if used:
			if not flt(used.base):
				problems.append(_("الـ Base = صفر"))
			try:
				amounts = calc.evaluate_ssa_earnings(used.name, e.name, filters.company, as_of)
				rule_earnings = flt(sum(flt(a) for c, a in amounts.items() if c in rule_components), 2)
				if rule_components and not rule_earnings:
					problems.append(_("الـ Structure مفيهاش مكونات الـ Gratuity Rule بقيمة"))
			except Exception as ex:
				frappe.clear_last_message()
				problems.append(_("خطأ في المعادلات: {0}").format(frappe.utils.strip_html(str(ex))[:200]))

		if filters.only_problems and not problems:
			continue
		data.append(
			{
				"employee": e.name,
				"employee_name": e.employee_name,
				"department": e.department,
				"date_of_joining": e.date_of_joining,
				"first_ssa_from": first.from_date if first else None,
				"ssa_on_joining": "✓" if on_joining else "✗",
				"days_without_ssa": gap,
				"ssa": used.name if used else None,
				"ssa_from": used.from_date if used else None,
				"salary_structure": used.salary_structure if used else None,
				"base": flt(used.base) if used else 0,
				"rule_earnings": rule_earnings,
				"ssa_count": len(rows),
				"problem": " · ".join(problems),
			}
		)

	total = len(data)
	missing = len([d for d in data if d["ssa_on_joining"] == "✗"])
	with_problems = len([d for d in data if d["problem"]])
	summary = [
		{"value": total, "label": _("عدد الموظفين"), "datatype": "Int", "indicator": "Blue"},
		{"value": total - missing, "label": _("عندهم Assignment من يوم التعيين"), "datatype": "Int", "indicator": "Green"},
		{"value": missing, "label": _("مالهمش Assignment يوم التعيين"), "datatype": "Int", "indicator": "Red"},
		{"value": with_problems, "label": _("عندهم مشكلة"), "datatype": "Int", "indicator": "Orange"},
	]
	return get_columns(), data, None, None, summary


def get_columns():
	def c(fieldname, label, fieldtype="Data", options=None, width=120):
		return {"fieldname": fieldname, "label": label, "fieldtype": fieldtype, "options": options, "width": width}

	return [
		c("employee", _("Employee"), "Link", "Employee", 130),
		c("employee_name", _("Employee Name"), "Data", None, 180),
		c("department", _("Department"), "Link", "Department", 160),
		c("date_of_joining", _("Date of Joining"), "Date", None, 110),
		c("first_ssa_from", _("First Assignment From"), "Date", None, 130),
		c("ssa_on_joining", _("Assignment on Joining"), "Data", None, 110),
		c("days_without_ssa", _("Days without Assignment"), "Int", None, 110),
		c("ssa", _("Assignment Used"), "Link", "Salary Structure Assignment", 160),
		c("ssa_from", _("Used From"), "Date", None, 100),
		c("salary_structure", _("Salary Structure"), "Link", "Salary Structure", 200),
		c("base", _("Base"), "Currency", None, 110),
		c("rule_earnings", _("Gratuity Rule Earnings"), "Currency", None, 140),
		c("ssa_count", _("Assignments"), "Int", None, 90),
		c("problem", _("Problem"), "Data", None, 320),
	]
