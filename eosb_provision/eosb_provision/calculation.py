# Copyright (c) 2026
# For license information, please see license.txt
"""
حسبة مخصص نهاية الخدمة ورصيد الإجازات.

كل الإعدادات من الـ Gratuity Rule اللي في EOSB Provision Settings:
  - المكونات      : applicable_earnings_component
  - الشرائح       : gratuity_rule_slabs  (كل شريحة بتاخد سنينها بس × نسبتها)
  - عدد الأيام    : total_working_days_per_year
  - أقل مدة       : minimum_year_for_gratuity
  - طريقة السنين  : work_experience_calculation_function
  - أنواع الإجازات: custom_applicable_leave_type  (لو موجود)
"""

import frappe
from frappe import _
from frappe.utils import date_diff, flt, getdate

KSA_METHOD = "KSA Labor Law"
DAYS_360 = "30/360 (inclusive)"


# ------------------------------------------------------------------ settings / rule
def get_settings():
	settings = frappe.get_single("EOSB Provision Settings")
	if not settings.gratuity_rule:
		frappe.throw(_("اختار الـ Gratuity Rule في EOSB Provision Settings الأول"))
	return settings


def get_company_accounts(settings, company):
	for row in settings.company_accounts or []:
		if row.company == company:
			return row
	return frappe._dict()


def load_rule(rule_name):
	rule = frappe.get_doc("Gratuity Rule", rule_name)
	components = [d.salary_component for d in rule.applicable_earnings_component]
	if not components:
		frappe.throw(_("الـ Gratuity Rule {0} مفيهاش Applicable Earnings Component").format(rule_name))

	leave_types = [d.leave_type for d in (rule.get("custom_applicable_leave_type") or []) if d.get("leave_type")]

	slabs = []
	for s in rule.gratuity_rule_slabs:
		slabs.append(
			frappe._dict(
				from_year=flt(s.from_year),
				to_year=flt(s.to_year) or 9999,  # 0 = مفيش حد أعلى
				fraction=flt(s.fraction_of_applicable_earnings),
			)
		)
	if not slabs:
		frappe.throw(_("الـ Gratuity Rule {0} مفيهاش شرائح في جدول Rules").format(rule_name))

	return frappe._dict(
		name=rule.name,
		method=rule.work_experience_calculation_function or "",
		days_per_year=flt(rule.total_working_days_per_year) or 365.25,
		min_years=flt(rule.minimum_year_for_gratuity),
		components=components,
		leave_types=leave_types,
		slabs=slabs,
	)


# ------------------------------------------------------------------ service period
def get_service(date_of_joining, as_of, rule, settings):
	"""يرجع (service_days, basis, years) حسب طريقة الـ Rule."""
	doj = getdate(date_of_joining)
	as_of = getdate(as_of)

	if rule.method == KSA_METHOD and (settings.ksa_day_count or DAYS_360) == DAYS_360:
		# الشهر 30 يوم والسنة 360، ويوم التعيين محسوب
		service_days = (
			(as_of.year - doj.year) * 360
			+ (as_of.month - doj.month) * 30
			+ (min(as_of.day, 30) - min(doj.day, 30))
			+ 1
		)
		basis = 360.0
	else:
		service_days = date_diff(as_of, doj)
		basis = rule.days_per_year

	years = service_days / basis
	if rule.method == "Round off Work Experience":
		years = round(years)
		service_days = years * basis
	elif rule.method == "Take Exact Completed Years":
		years = int(years)
		service_days = years * basis

	return max(service_days, 0), basis, max(years, 0)


def get_weighted_days(service_days, basis, years, rule):
	"""مجموع (أيام كل شريحة × نسبتها). المستحق = الأجر × weighted_days ÷ basis"""
	if years < rule.min_years:
		return 0, []
	weighted = 0
	parts = []
	for s in rule.slabs:
		portion = min(service_days, s.to_year * basis) - s.from_year * basis
		if portion > 0:
			weighted += portion * s.fraction
			parts.append((s, portion))
	return weighted, parts


# ------------------------------------------------------------------ data
def get_active_employees(company, as_of, department=None):
	return frappe.db.sql(
		"""
		select name, employee_name, department, date_of_joining
		from `tabEmployee`
		where status = 'Active'
		  and company = %(company)s
		  and date_of_joining <= %(as_of)s
		  and (%(department)s is null or department = %(department)s)
		order by department, name
		""",
		{"company": company, "as_of": as_of, "department": department or None},
		as_dict=True,
	)


def get_salary_components(employees, components, company, as_of):
	"""آخر Salary Slip عاملة Submit، فترتها بدأت في التاريخ أو قبله، ومكونات الـ Rule بس.
	يرجع {employee: {"slip": name, "components": {component: amount}}}"""
	if not employees:
		return {}
	rows = frappe.db.sql(
		"""
		select ss.employee, ss.name as slip, ss.start_date, sd.salary_component,
		       sum(sd.default_amount) as amount
		from `tabSalary Slip` ss
		join `tabSalary Detail` sd
		  on sd.parent = ss.name and sd.parenttype = 'Salary Slip' and sd.parentfield = 'earnings'
		where ss.docstatus = 1
		  and ss.company = %(company)s
		  and ss.start_date <= %(as_of)s
		  and ss.employee in %(employees)s
		  and sd.salary_component in %(components)s
		group by ss.employee, ss.name, ss.start_date, sd.salary_component
		order by ss.employee, ss.start_date desc, ss.name desc, sd.salary_component
		""",
		{
			"company": company,
			"as_of": as_of,
			"employees": tuple(employees),
			"components": tuple(components),
		},
		as_dict=True,
	)
	out = {}
	for r in rows:
		cur = out.get(r.employee)
		if cur is None:
			cur = out[r.employee] = {"slip": r.slip, "components": {}}
		if r.slip != cur["slip"]:
			continue  # Slip أقدم
		cur["components"][r.salary_component] = flt(r.amount)
	return out


def get_leave_balances(employees, leave_types, as_of):
	"""رصيد الإجازات من دالة HRMS نفسها (get_leave_balance_on)."""
	from hrms.hr.doctype.leave_application.leave_application import get_leave_balance_on

	out = {}
	if not leave_types:
		return out
	for emp in employees:
		total = 0
		parts = []
		for lt in leave_types:
			bal = flt(get_leave_balance_on(emp, lt, as_of))
			if bal:
				total += bal
				parts.append(f"{lt}: {bal:g}")
		out[emp] = (flt(total, 3), " | ".join(parts))
	return out


def get_component_accounts(component, company, company_accounts):
	"""حسابات المكوّن للشركة. الترتيب:
	1) جدول EOSB Provision Accounts في الـ Salary Component
	2) الحقول القديمة custom_gratuity_expense_account / custom_gratuity_payable_account (لو موجودة)
	3) الحسابات الافتراضية في الإعدادات"""
	row = frappe.db.get_value(
		"EOSB Component Account",
		{"parent": component, "parenttype": "Salary Component", "company": company},
		["expense_account", "provision_account"],
		as_dict=True,
	)
	if row and row.expense_account and row.provision_account:
		return row.expense_account, row.provision_account

	meta = frappe.get_meta("Salary Component")
	if meta.has_field("custom_gratuity_expense_account") and meta.has_field("custom_gratuity_payable_account"):
		exp, prov = frappe.db.get_value(
			"Salary Component", component, ["custom_gratuity_expense_account", "custom_gratuity_payable_account"]
		)
		if exp and prov and _account_company(exp) == company and _account_company(prov) == company:
			return exp, prov

	if company_accounts.get("default_expense_account") and company_accounts.get("default_provision_account"):
		return company_accounts.default_expense_account, company_accounts.default_provision_account

	frappe.throw(
		_(
			"المكوّن {0} مالوش حسابات مخصص نهاية الخدمة لشركة {1}. ضيفها في جدول EOSB Provision Accounts في الـ Salary Component، أو حط حسابات افتراضية في EOSB Provision Settings"
		).format(frappe.bold(component), frappe.bold(company))
	)


def _account_company(account):
	return frappe.get_cached_value("Account", account, "company")


# ------------------------------------------------------------------ booked (history)
def get_booked(company, as_of, exclude=None):
	"""الرصيد المحجوز = آخر قيمة اتسجلت في آخر EOSB Provision Submitted (شامل الافتتاحي)
	لكل (موظف + مكوّن)، ولكل موظف للإجازات."""
	params = {"company": company, "as_of": as_of, "exclude": exclude or ""}
	eosb_rows = frappe.db.sql(
		"""
		select employee, salary_component, required_amount, expense_account, provision_account
		from (
			select d.employee, d.salary_component, d.required_amount, d.expense_account, d.provision_account,
			       row_number() over (partition by d.employee, d.salary_component
			                          order by p.posting_date desc, p.creation desc) as rn
			from `tabEOSB Provision Detail` d
			join `tabEOSB Provision` p on p.name = d.parent
			where p.docstatus = 1 and p.company = %(company)s
			  and p.posting_date <= %(as_of)s and p.name != %(exclude)s
		) x
		where rn = 1
		""",
		params,
		as_dict=True,
	)
	eosb = {}
	for r in eosb_rows:
		eosb[(r.employee, r.salary_component)] = r

	leave_rows = frappe.db.sql(
		"""
		select employee, total_leave_amount
		from (
			select e.employee, e.total_leave_amount,
			       row_number() over (partition by e.employee
			                          order by p.posting_date desc, p.creation desc) as rn
			from `tabEOSB Provision Employee` e
			join `tabEOSB Provision` p on p.name = e.parent
			where p.docstatus = 1 and p.company = %(company)s
			  and p.posting_date <= %(as_of)s and p.name != %(exclude)s
		) x
		where rn = 1
		""",
		params,
		as_dict=True,
	)
	leave = {r.employee: flt(r.total_leave_amount) for r in leave_rows}
	return eosb, leave


# ------------------------------------------------------------------ helpers
def distribute(total, weights):
	"""يوزّع total على weights بالنسبة، ويحط فرق التقريب على أكبر وزن."""
	total = flt(total, 2)
	s = sum(weights.values())
	if not s:
		return {k: 0 for k in weights}
	out = {k: flt(total * v / s, 2) for k, v in weights.items()}
	diff = flt(total - sum(out.values()), 2)
	if diff:
		biggest = max(weights, key=lambda k: weights[k])
		out[biggest] = flt(out[biggest] + diff, 2)
	return out
