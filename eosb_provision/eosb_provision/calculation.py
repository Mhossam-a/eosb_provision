# Copyright (c) 2026
# For license information, please see license.txt
"""
حسبة مخصص نهاية الخدمة ورصيد الإجازات (نظام العمل السعودي - المادة 84).

كل الإعدادات من الـ Gratuity Rule اللي في EOSB Provision Settings:
  - المكونات      : applicable_earnings_component   (الأجر الفعلي = مجموعها من آخر Salary Slip)
  - الشرائح       : gratuity_rule_slabs             (كل شريحة بتاخد سنينها بس × نسبتها)
  - عدد الأيام    : total_working_days_per_year     (للطرق العادية)
  - أقل مدة       : minimum_year_for_gratuity
  - طريقة السنين  : work_experience_calculation_function
  - أنواع الإجازات: custom_applicable_leave_type    (لو موجود)

KSA Labor Law: المدة = سنين + شهور/12 + أيام/360 (الشهر 30 يوم، ويوم التعيين محسوب)،
وأجزاء السنة بتتحسب بنسبتها (المادة 84).
"""

import frappe
from dateutil.relativedelta import relativedelta
from frappe import _
from frappe.utils import add_days, date_diff, flt, getdate

KSA_METHOD = "KSA Labor Law"
EXCLUDE_RELIEVING = "Exclude once Relieving Date is set"
INCLUDE_UNTIL_RELIEVING = "Include until Relieving Date"


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
def get_service(date_of_joining, as_of, rule, deduct_days=0):
	"""يرجع (service_days, basis, years, text) حسب طريقة الـ Rule."""
	doj = getdate(date_of_joining)
	as_of = getdate(as_of)

	if rule.method == KSA_METHOD:
		rd = relativedelta(add_days(as_of, 1), doj)  # +1 = يوم التعيين محسوب
		service_days = rd.years * 360 + rd.months * 30 + rd.days - flt(deduct_days)
		basis = 360.0
	else:
		service_days = date_diff(as_of, doj) - flt(deduct_days)
		basis = rule.days_per_year

	service_days = max(service_days, 0)
	years = service_days / basis
	if rule.method == "Round off Work Experience":
		years = round(years)
		service_days = years * basis
	elif rule.method == "Take Exact Completed Years":
		years = int(years)
		service_days = years * basis

	return service_days, basis, years, service_text(service_days, basis)


def service_text(service_days, basis):
	if basis == 360:
		d = int(round(service_days))
		return f"{d // 360} سنة  {(d % 360) // 30} شهر  {d % 30} يوم"
	return f"{service_days / basis:.4f} سنة"


def get_weighted_days(service_days, basis, years, rule):
	"""مجموع (أيام كل شريحة × نسبتها). المستحق = الأجر × weighted ÷ basis"""
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
def get_active_employees(company, as_of, department=None, employee=None, period_start=None, mode=None):
	"""الموظفين اللي هيدخلوا المخصص.
	mode = EXCLUDE_RELIEVING (الافتراضي): Active ومالوش Relieving Date خالص.
	  أول ما تحط Relieving Date للموظف (حتى لو في المستقبل) بيقف المخصص بتاعه،
	  ورصيده المحجوز بيفضل زي ما هو لحد التسوية الفعلية.
	mode = INCLUDE_UNTIL_RELIEVING: بيفضل يظهر لحد يوم الترك:
	  - عنده Relieving Date بعد بداية الفترة ← بيتحسب لحد min(تاريخ المخصص، Relieving Date)
	  - اللي ساب قبل بداية الفترة مايظهرش.
	period_start = تاريخ آخر مخصص Submitted."""
	if (mode or EXCLUDE_RELIEVING) == EXCLUDE_RELIEVING:
		rows = frappe.db.sql(
			"""
			select name, employee_name, department, date_of_joining, relieving_date, status
			from `tabEmployee`
			where status = 'Active' and relieving_date is null
			  and company = %(company)s
			  and date_of_joining <= %(as_of)s
			  and (%(department)s is null or department = %(department)s)
			  and (%(employee)s is null or name = %(employee)s)
			order by department, name
			""",
			{"company": company, "as_of": as_of, "department": department or None, "employee": employee or None},
			as_dict=True,
		)
		for r in rows:
			r.calc_date = getdate(as_of)
		return rows

	cutoff = getdate(period_start) if period_start else add_days(getdate(as_of), -1)
	rows = frappe.db.sql(
		"""
		select name, employee_name, department, date_of_joining, relieving_date, status
		from `tabEmployee`
		where status in ('Active', 'Left')
		  and (
		        (status = 'Active' and relieving_date is null)
		     or relieving_date > %(cutoff)s
		      )
		  and company = %(company)s
		  and date_of_joining <= %(as_of)s
		  and (%(department)s is null or department = %(department)s)
		  and (%(employee)s is null or name = %(employee)s)
		order by department, name
		""",
		{
			"company": company,
			"as_of": as_of,
			"cutoff": cutoff,
			"department": department or None,
			"employee": employee or None,
		},
		as_dict=True,
	)
	as_of = getdate(as_of)
	for r in rows:
		r.calc_date = min(as_of, getdate(r.relieving_date)) if r.relieving_date else as_of
	return rows


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


def get_unpaid_days(employees, as_of):
	"""أيام الغياب والإجازات بدون أجر من الـ Attendance (زي HRMS Gratuity)."""
	if not employees:
		return {}
	lwp = tuple(frappe.get_all("Leave Type", filters={"is_lwp": 1}, pluck="name")) or ("__none__",)
	rows = frappe.db.sql(
		"""
		select employee,
		       sum(case
		             when status = 'Absent' then 1
		             when status = 'On Leave' and leave_type in %(lwp)s then 1
		             when status = 'Half Day' and leave_type in %(lwp)s then 0.5
		             else 0 end) as days
		from `tabAttendance`
		where docstatus = 1 and employee in %(employees)s and attendance_date <= %(as_of)s
		group by employee
		""",
		{"employees": tuple(employees), "lwp": lwp, "as_of": as_of},
		as_dict=True,
	)
	return {r.employee: flt(r.days) for r in rows}


def get_leave_balances(employees, leave_types, as_of):
	"""رصيد الإجازات من دالة HRMS نفسها (get_leave_balance_on).
	as_of ممكن يكون تاريخ واحد أو dict {employee: date}."""
	from hrms.hr.doctype.leave_application.leave_application import get_leave_balance_on

	out = {}
	if not leave_types:
		return out
	for emp in employees:
		total = 0
		parts = []
		for lt in leave_types:
			date = as_of.get(emp) if isinstance(as_of, dict) else as_of
			bal = flt(get_leave_balance_on(emp, lt, date))
			if bal:
				total += bal
				parts.append(f"{lt}: {bal:g}")
		out[emp] = (flt(total, 3), " | ".join(parts))
	return out


def get_component_accounts(component, company, company_accounts):
	"""حسابات المكوّن للشركة: جدول EOSB Provision Accounts في الـ Salary Component،
	ولو مش موجود: الحسابات الافتراضية في الإعدادات."""
	row = frappe.db.get_value(
		"EOSB Component Account",
		{"parent": component, "parenttype": "Salary Component", "company": company},
		["expense_account", "provision_account"],
		as_dict=True,
	)
	if row and row.expense_account and row.provision_account:
		return row.expense_account, row.provision_account

	if company_accounts.get("default_expense_account") and company_accounts.get("default_provision_account"):
		return company_accounts.default_expense_account, company_accounts.default_provision_account

	frappe.throw(
		_(
			"المكوّن {0} مالوش حسابات مخصص نهاية الخدمة لشركة {1}. ضيفها في جدول EOSB Provision Accounts في الـ Salary Component، أو حط حسابات افتراضية في EOSB Provision Settings"
		).format(frappe.bold(component), frappe.bold(company))
	)


# ------------------------------------------------------------------ main computation
def compute_employees(
	company, as_of, department=None, employee=None, settings=None, rule=None, with_accounts=True, period_start=None
):
	"""يحسب لكل موظف Active: المدة، والأجر، والمستحق موزّع على المكونات، ورصيد الإجازات.
	يرجع (rows, skipped, rule, settings)."""
	settings = settings or get_settings()
	rule = rule or load_rule(settings.gratuity_rule)
	company_accounts = get_company_accounts(settings, company)
	as_of = getdate(as_of)

	employees = get_active_employees(
		company, as_of, department, employee, period_start, settings.get("relieving_date_handling")
	)
	emp_ids = [e.name for e in employees]
	calc_dates = {e.name: e.calc_date for e in employees}
	salaries = get_salary_components(emp_ids, rule.components, company, as_of)
	unpaid = get_unpaid_days(emp_ids, as_of) if settings.deduct_unpaid_days else {}
	leaves = {}
	if settings.include_leave_provision:
		leaves = get_leave_balances([e for e in emp_ids if e in salaries], rule.leave_types, calc_dates)
	days_in_month = flt(settings.days_in_month) or 30

	rows, skipped, account_cache = [], [], {}
	for e in employees:
		sal = salaries.get(e.name)
		if not sal or not sum(sal["components"].values()):
			skipped.append(e.name)
			continue

		deduct = unpaid.get(e.name, 0)
		service_days, basis, years, text = get_service(e.date_of_joining, e.calc_date, rule, deduct)
		weighted, parts = get_weighted_days(service_days, basis, years, rule)
		earnings = flt(sum(sal["components"].values()), 2)
		total_eosb = flt(earnings * weighted / basis, 2)
		per_component = distribute(total_eosb, sal["components"])

		components = []
		for comp, comp_amount in sal["components"].items():
			exp = prov = None
			if with_accounts:
				key = (comp, company)
				if key not in account_cache:
					account_cache[key] = get_component_accounts(comp, company, company_accounts)
				exp, prov = account_cache[key]
			components.append(
				frappe._dict(
					salary_component=comp,
					component_amount=comp_amount,
					required_amount=per_component[comp],
					expense_account=exp,
					provision_account=prov,
				)
			)

		leave_balance = leaves.get(e.name, (0, ""))[0]
		slab_text = " | ".join(
			"{0:g}→{1}: {2:g} يوم × {3:g}".format(
				s.from_year, "∞" if s.to_year >= 9999 else f"{s.to_year:g}", portion, s.fraction
			)
			for s, portion in parts
		)
		rows.append(
			frappe._dict(
				employee=e.name,
				employee_name=e.employee_name,
				department=e.department,
				date_of_joining=e.date_of_joining,
				relieving_date=e.relieving_date,
				left_in_period=1 if (e.relieving_date and getdate(e.relieving_date) < as_of) else 0,
				service_days=service_days,
				service_text=text + (f"  (مخصوم {deduct:g} يوم بدون أجر)" if deduct else ""),
				unpaid_days=deduct,
				number_of_years=flt(years, 4),
				salary_slip=sal["slip"],
				components_text=" | ".join(f"{c}: {a:,.2f}" for c, a in sal["components"].items()),
				last_salary=earnings,
				allocated_amount=total_eosb,
				slab_details=slab_text,
				leave_balance=leave_balance,
				day_salary=flt(earnings / days_in_month, 2),
				total_leave_amount=flt(leave_balance * earnings / days_in_month, 2),
				components=components,
			)
		)
	return rows, skipped, rule, settings


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


def get_provision_accounts(company, settings):
	accounts = set(
		frappe.get_all(
			"EOSB Component Account", filters={"company": company, "parenttype": "Salary Component"}, pluck="provision_account"
		)
	)
	ca = get_company_accounts(settings, company)
	for f in ("default_provision_account", "leave_provision_account"):
		if ca.get(f):
			accounts.add(ca.get(f))
	return [a for a in accounts if a]


def get_gl_balances(company, as_of, accounts):
	"""رصيد حسابات المخصص لكل موظف (Party) في الـ GL."""
	if not accounts:
		return {}
	rows = frappe.db.sql(
		"""
		select party, sum(credit - debit) as balance
		from `tabGL Entry`
		where company = %(company)s and is_cancelled = 0 and party_type = 'Employee'
		  and account in %(accounts)s and posting_date <= %(as_of)s
		group by party
		""",
		{"company": company, "as_of": as_of, "accounts": tuple(accounts)},
		as_dict=True,
	)
	return {r.party: flt(r.balance) for r in rows}


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
