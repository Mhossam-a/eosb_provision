# Copyright (c) 2026
# For license information, please see license.txt
"""
حسبة مخصص نهاية الخدمة ورصيد الإجازات (نظام العمل السعودي - المادة 84).

كل الإعدادات من الـ Gratuity Rule اللي في EOSB Provision Settings:
  - المكونات      : applicable_earnings_component   (الأجر = مجموعها من الـ Salary Structure Assignment أو آخر Salary Slip)
  - الشرائح       : gratuity_rule_slabs             (نفس خوارزمية HRMS Gratuity)
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
SUM_OF_SLABS = "Sum of all previous slabs"
SOURCE_SSA = "Salary Structure Assignment"
SOURCE_SLIP = "Last Salary Slip"
AS_PER_RULE = "As per Gratuity Rule"
LEAVE_OFF = "Disabled"
LEAVE_CALC = "Calculate only"
LEAVE_POST = "Calculate and post"
OPENING_RECORD = "Record only"
OPENING_POST = "Post entry"


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


def leave_mode(settings):
	"""Disabled: مفيش إجازات. Calculate only: بتتحسب وبتظهر في الشاشة والتقرير من غير قيد.
	Calculate and post: بتتحسب وبتدخل القيد. لو الخيار الجديد فاضي (إعدادات قديمة) بنقرا الـ checkbox القديم."""
	mode = settings.get("leave_provision_mode")
	if mode in (LEAVE_OFF, LEAVE_CALC, LEAVE_POST):
		return mode
	return LEAVE_POST if settings.get("include_leave_provision") else LEAVE_OFF


def opening_mode(settings):
	"""Record only (الافتراضي): الافتتاحي من غير قيد. Post entry: الافتتاحي بيعمل قيد على حساب مقابل."""
	return OPENING_POST if settings.get("opening_mode") == OPENING_POST else OPENING_RECORD


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
		based_on=rule.calculate_gratuity_amount_based_on or SUM_OF_SLABS,
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
	if rule.method == KSA_METHOD:
		pass  # الأيام زي ما هي (30/360)
	elif rule.method == "Round off Work Experience":
		years = round(years)
		service_days = years * basis
	else:
		# زي HRMS Gratuity.get_work_experience بالظبط: المدة بكسورها،
		# متقربة لدقة حقل Current Work Experience (مفيش تقريب لتحت لـ Take Exact Completed Years)
		years = flt(years, experience_precision())
		service_days = years * basis

	return service_days, basis, years, service_text(service_days, basis)


def experience_precision():
	if frappe.local.flags.get("eosb_experience_precision") is None:
		frappe.local.flags.eosb_experience_precision = frappe.new_doc("Gratuity").precision(
			"current_work_experience"
		)
	return frappe.local.flags.eosb_experience_precision


def service_text(service_days, basis):
	if basis == 360:
		d = int(round(service_days))
		return f"{d // 360} سنة  {(d % 360) // 30} شهر  {d % 30} يوم"
	return f"{service_days / basis:.4f} سنة"


def get_weighted_days(service_days, basis, years, rule, slab_mode=None):
	"""مجموع (أيام كل شريحة × نسبتها). المستحق = الأجر × weighted ÷ basis

	slab_mode:
	  - Sum of all previous slabs (الافتراضي): كل شريحة بتاخد سنينها بس × نسبتها
	  - As per Gratuity Rule: زي حقل Calculate Gratuity Amount Based On في الـ Rule
	    (لو Current Slab: كل المدة × نسبة الشريحة اللي الموظف واقف فيها، زي HRMS)

	نفس خوارزمية HRMS Gratuity.get_gratuity_amount خطوة بخطوة (نفس المقارنات وحدود الشرائح)،
	بس الحساب بالأيام بدل السنين عشان مايحصلش فرق تقريب. to_year = 9999 معناها مفتوحة (0 في الـ Rule)."""
	if years < rule.min_years:
		return 0, []

	based_on = rule.based_on if slab_mode == AS_PER_RULE else SUM_OF_SLABS
	weighted, parts = 0, []
	if based_on == "Current Slab":
		for s in rule.slabs:
			if s.from_year <= years <= s.to_year:
				weighted, parts = service_days * s.fraction, [(s, service_days)]
				if s.fraction:
					break
		return weighted, parts

	days_left = service_days
	for s in rule.slabs:
		if s.from_year < years and s.to_year < years:  # عدّى الشريحة كلها
			portion = (s.to_year - s.from_year) * basis
			weighted += portion * s.fraction
			days_left -= portion
			parts.append((s, portion))
		elif s.from_year <= years <= s.to_year:  # الشريحة اللي واقف فيها: باقي المدة كلها
			weighted += days_left * s.fraction
			parts.append((s, days_left))
			break
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
	period_start = تاريخ آخر مخصص Submitted.
	employee = موظف واحد أو list موظفين (لما تحسب الموظفين اللي في الجدول بس)."""
	employees = [employee] if isinstance(employee, str) else list(employee or [])
	emp_cond = "and name in %(employees)s" if employees else ""
	if (mode or EXCLUDE_RELIEVING) == EXCLUDE_RELIEVING:
		rows = frappe.db.sql(
			f"""
			select name, employee_name, department, date_of_joining, relieving_date, status
			from `tabEmployee`
			where status = 'Active' and relieving_date is null
			  and company = %(company)s
			  and date_of_joining <= %(as_of)s
			  and (%(department)s is null or department = %(department)s)
			  {emp_cond}
			order by department, name
			""",
			{"company": company, "as_of": as_of, "department": department or None, "employees": tuple(employees) or ("",)},
			as_dict=True,
		)
		for r in rows:
			r.calc_date = getdate(as_of)
		return rows

	cutoff = getdate(period_start) if period_start else add_days(getdate(as_of), -1)
	rows = frappe.db.sql(
		f"""
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
		  {emp_cond}
		order by department, name
		""",
		{
			"company": company,
			"as_of": as_of,
			"cutoff": cutoff,
			"department": department or None,
			"employees": tuple(employees) or ("",),
		},
		as_dict=True,
	)
	as_of = getdate(as_of)
	for r in rows:
		r.calc_date = min(as_of, getdate(r.relieving_date)) if r.relieving_date else as_of
	return rows


def explain_not_eligible(employees, company, as_of, settings):
	"""ليه الموظف ده مش داخل المخصص (لما تختاره بإيدك في الجدول)."""
	out = {}
	if not employees:
		return out
	mode = settings.get("relieving_date_handling") or EXCLUDE_RELIEVING
	rows = frappe.get_all(
		"Employee",
		filters={"name": ("in", list(employees))},
		fields=["name", "company", "status", "date_of_joining", "relieving_date"],
	)
	for e in rows:
		if e.company != company:
			reason = _("تبع شركة {0}").format(e.company)
		elif e.date_of_joining and getdate(e.date_of_joining) > getdate(as_of):
			reason = _("تاريخ التعيين {0} بعد تاريخ المخصص").format(frappe.format(e.date_of_joining, "Date"))
		elif e.relieving_date and mode == EXCLUDE_RELIEVING:
			reason = _("عنده Relieving Date {0}، والإعدادات على Exclude once Relieving Date is set").format(
				frappe.format(e.relieving_date, "Date")
			)
		elif e.relieving_date:
			reason = _("ساب الشغل {0} قبل بداية الفترة").format(frappe.format(e.relieving_date, "Date"))
		elif e.status != "Active":
			reason = _("حالته {0} مش Active").format(e.status)
		else:
			reason = _("مش مستوفي شروط المخصص")
		out[e.name] = reason
	for emp in employees:
		out.setdefault(emp, _("الموظف مش موجود"))
	return out


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


def get_ssa(employee, company, on_date):
	"""آخر Salary Structure Assignment عاملة Submit من تاريخها <= التاريخ.
	لو مفيش (مثلاً التاريخ قبل أول Assignment): أول Assignment للموظف، مع ملاحظة."""
	fields = ["name", "salary_structure", "from_date", "base", "variable"]
	ssa = frappe.db.get_value(
		"Salary Structure Assignment",
		{"employee": employee, "company": company, "docstatus": 1, "from_date": ("<=", on_date)},
		fields,
		order_by="from_date desc",
		as_dict=True,
	)
	if ssa:
		return ssa, None
	ssa = frappe.db.get_value(
		"Salary Structure Assignment",
		{"employee": employee, "company": company, "docstatus": 1},
		fields,
		order_by="from_date asc",
		as_dict=True,
	)
	if ssa:
		return ssa, _("الـ Assignment بيبدأ {0} بعد تاريخ الحسبة، فاتاخد أول Assignment").format(
			frappe.format(ssa.from_date, "Date")
		)
	return None, None


class FormulaError(Exception):
	pass


def _formula_globals():
	from datetime import date
	from math import ceil, floor

	from frappe.utils import get_first_day, get_last_day, rounded

	# نفس الدوال المسموحة في معادلات HRMS (Salary Slip.whitelisted_globals)
	return {
		"int": int,
		"float": float,
		"long": int,
		"round": round,
		"rounded": rounded,
		"date": date,
		"getdate": getdate,
		"get_first_day": get_first_day,
		"get_last_day": get_last_day,
		"ceil": ceil,
		"floor": floor,
	}


def _sanitize(expr):
	"""زي hrms.payroll.utils.sanitize_expression: يشيل المسافات ويخلي المعادلة سطر واحد."""
	if not expr:
		return None
	return " ".join(expr.strip().splitlines())


def _safe_eval(code, data):
	"""نفس تقييم HRMS للمعادلات (eval محمي من غير builtins)."""
	import unicodedata

	from frappe.utils.safe_exec import UNSAFE_ATTRIBUTES

	code = unicodedata.normalize("NFKC", code)
	for attr in set(UNSAFE_ATTRIBUTES).union(["__"]) - {"format"}:
		if attr in code:
			raise SyntaxError(f"Illegal rule {code}. Cannot use {attr}")
	g = _formula_globals()
	g["__builtins__"] = {}
	return eval(code, g, data)  # nosemgrep


def _period(structure, on_date, company):
	try:
		from hrms.payroll.doctype.payroll_entry.payroll_entry import get_start_end_dates

		d = get_start_end_dates(structure.payroll_frequency or "Monthly", on_date, company)
		return getdate(d.start_date), getdate(d.end_date)
	except Exception:
		from frappe.utils import get_first_day, get_last_day

		return getdate(get_first_day(on_date)), getdate(get_last_day(on_date))


def evaluate_ssa_earnings(ssa_name, employee, company, on_date, trace=None):
	"""يحسب مكونات الـ Earnings من الـ Salary Structure بمعادلاتها و Base / Variable بتوع الـ Assignment.
	نفس طريقة HRMS بالظبط (السياق: كل اختصارات المكونات = 0، حقول الـ Salary Slip،
	حقول الـ Assignment زي base و variable، حقول الموظف، وفترة شهر كاملة من غير غياب)،
	بس من غير ما يعتمد على كلاس الـ Salary Slip، عشان أي أبلكيشن بيعدّل عليه مايبوظش الحسبة.
	يرجع {salary_component: amount} (المبلغ الكامل للشهر).
	trace: list اختياري، بيتحط فيه (salary_component, سبب) للمكونات اللي اتخطت (Condition مااتحققتش أو Statistical)،
	عشان نعرف ليه مكوّن مش ظاهر في النتيجة."""
	ssa = frappe.get_doc("Salary Structure Assignment", ssa_name)
	structure = frappe.get_cached_doc("Salary Structure", ssa.salary_structure)

	data = frappe._dict()
	for abbr in frappe.get_all("Salary Component", pluck="salary_component_abbr"):
		if abbr:
			data[abbr] = 0
	for df in frappe.get_meta("Salary Slip").fields:
		if df.fieldtype not in ("Table", "Table MultiSelect", "Section Break", "Column Break", "Tab Break", "HTML", "Button"):
			data.setdefault(df.fieldname, None)
	# الموظف الأول وبعده الـ Assignment: HRMS بيعمل self.update(ssa) على الـ Salary Slip نفسها فبتكسب على الموظف،
	# وبدون كده أي حقل custom على الـ Employee اسمه base أو variable (قيمته صفر) كان بيصفّر كل المعادلات
	data.update(frappe.get_cached_doc("Employee", employee).as_dict())
	data.update(ssa.as_dict())
	start, end = _period(structure, on_date, company)
	days = date_diff(end, start) + 1
	data.update(
		{
			"employee": employee,
			"company": company,
			"salary_structure": ssa.salary_structure,
			"payroll_frequency": structure.payroll_frequency,
			"start_date": start,
			"end_date": end,
			"posting_date": end,
			"payment_days": days,
			"total_working_days": days,
			"leave_without_pay": 0,
			"absent_days": 0,
			"unmarked_days": 0,
		}
	)

	out = {}
	for row in structure.get("earnings") or []:
		condition, formula = _sanitize(row.condition), _sanitize(row.formula)
		try:
			if condition and not _safe_eval(condition, data):
				if trace is not None:
					trace.append((row.salary_component, _("الـ Condition مااتحققتش: {0}").format(condition)))
				continue
			amount = flt(row.amount)
			if row.amount_based_on_formula and formula:
				amount = flt(_safe_eval(formula, data), row.precision("amount"))
		except Exception as e:
			raise FormulaError(
				_("معادلة {0} ({1}) في {2}: {3}").format(
					row.salary_component, row.abbr, structure.name, f"{type(e).__name__}: {e}"
				)
			)
		if row.abbr:
			data[row.abbr] = amount
		if row.statistical_component:
			if trace is not None:
				trace.append((row.salary_component, _("Statistical Component (مش بيتحسب في الأجر)")))
			continue
		out[row.salary_component] = flt(out.get(row.salary_component, 0) + amount, 2)
	return out


def get_slip_earnings(employees, components, company, as_of):
	"""آخر Salary Slip عاملة Submit، فترتها بدأت في التاريخ أو قبله، ومكونات الـ Rule بس."""
	return get_salary_components(employees, components, company, as_of)


def no_rule_component_message(ssa, amounts, trace, rule_components):
	"""ليه الـ Structure مطلعش أجر من مكونات الـ Gratuity Rule: بيقارن الاتنين ويقول السبب بالظبط."""
	in_both = [c for c in amounts if c in rule_components]  # موجود في الاتنين (قيمته صفر)
	cond_false = [c for c, _why in trace if c in rule_components]
	lines = [
		_("الـ Salary Structure «{0}» (في {1}) مطلعش أجر من مكونات الـ Gratuity Rule.").format(ssa.salary_structure, ssa.name)
	]
	if in_both:
		lines.append(
			_("المكونات دي موجودة في الـ Rule والـ Structure لكن قيمتها صفر (راجع الـ Formula): {0}").format(
				"، ".join(in_both)
			)
		)
	if cond_false:
		lines.append(_("المكونات دي في الـ Rule بس الـ Condition بتاعتها في الـ Structure مااتحققتش: {0}").format("، ".join(cond_false)))
	if not in_both and not cond_false:
		lines.append(_("مفيش أي مكوّن في الـ Structure اسمه زي أي مكوّن في الـ Rule (الأسامي لازم تتطابق بالحرف)."))
	lines.append(_("مكونات الـ Rule (Applicable Earnings Component): {0}").format("، ".join(sorted(rule_components)) or "-"))
	lines.append(
		_("مكونات الـ Structure بقيمها: {0}").format("، ".join(f"{c} = {flt(a):,.2f}" for c, a in amounts.items()) or "-")
	)
	if trace:
		lines.append(_("مكونات اتخطت: {0}").format("، ".join(f"{c} ({why})" for c, why in trace)))
	return "\n".join(lines)


def get_earnings(employees, calc_dates, components, company, as_of, source=None):
	"""الأجر المعتمد لكل موظف (مكونات الـ Rule بس).
	source = Salary Structure Assignment (الافتراضي) أو Last Salary Slip.
	لو المصدر الأساسي مش لاقي حاجة للموظف، بيجرب المصدر التاني ويكتب ملاحظة.
	يرجع ({employee: {"source", "ref", "components", "note"}}, {employee: error})."""
	source = source or SOURCE_SSA
	comp_set = set(components)
	out, errors = {}, {}

	def from_ssa(emp):
		ssa, note = get_ssa(emp, company, calc_dates.get(emp) or as_of)
		if not ssa:
			errors[emp] = _("مالوش Salary Structure Assignment (Submitted) في الشركة دي")
			return None
		trace = []
		try:
			amounts = evaluate_ssa_earnings(ssa.name, emp, company, calc_dates.get(emp) or as_of, trace)
		except Exception as e:
			frappe.clear_last_message()
			errors[emp] = _("خطأ في {0}: {1}").format(ssa.name, frappe.utils.strip_html(str(e))[:250])
			return None
		comps = {c: flt(a, 2) for c, a in amounts.items() if c in comp_set and flt(a)}
		if not comps:
			if not flt(ssa.base):
				errors[emp] = _("الـ Base في {0} = صفر").format(ssa.name)
			else:
				errors[emp] = no_rule_component_message(ssa, amounts, trace, comp_set)
			return None
		return {"source": SOURCE_SSA, "ref": ssa.name, "components": comps, "note": note}

	slips = None

	def from_slip(emp):
		nonlocal slips
		if slips is None:
			slips = get_slip_earnings(employees, components, company, as_of)
		sal = slips.get(emp)
		if not sal or not sum(sal["components"].values()):
			msg = _("مالوش Salary Slip (Submitted) فيها مكونات الـ Gratuity Rule")
			errors[emp] = f"{errors[emp]} · {msg}" if errors.get(emp) else msg
			return None
		return {"source": SOURCE_SLIP, "ref": sal["slip"], "components": sal["components"], "note": None}

	order = (from_ssa, from_slip) if source == SOURCE_SSA else (from_slip, from_ssa)
	for emp in employees:
		res = order[0](emp)
		if not res:
			res = order[1](emp)
			if res:
				why = f" ({errors[emp]})" if errors.get(emp) else ""
				res["note"] = (res["note"] + " · " if res["note"] else "") + _("اتاخد من {0}").format(res["source"]) + why
		if res:
			out[emp] = res
			errors.pop(emp, None)
	return out, errors


def get_unpaid_days(employees, calc_dates):
	"""الأيام اللي بتتخصم من مدة الخدمة، بنفس طريقة HRMS Gratuity.get_total_working_days بالظبط:
	حسب Payroll Based On في Payroll Settings
	  Leave      ← أيام Attendance «On Leave» بنوع إجازة Is Leave Without Pay
	  Attendance ← أيام Attendance «Absent»
	لحد تاريخ الحسبة بتاع كل موظف. calc_dates = {employee: date}"""
	if not employees:
		return {}
	based_on = frappe.db.get_single_value("Payroll Settings", "payroll_based_on") or "Leave"
	params = {"employees": tuple(employees), "to_date": max(getdate(d) for d in calc_dates.values())}
	if based_on == "Attendance":
		cond = "status = 'Absent'"
	else:
		params["lwp"] = tuple(frappe.get_all("Leave Type", filters={"is_lwp": 1}, pluck="name")) or ("__none__",)
		cond = "status = 'On Leave' and leave_type in %(lwp)s"
	rows = frappe.db.sql(
		f"""
		select employee, attendance_date
		from `tabAttendance`
		where docstatus = 1 and {cond}
		  and employee in %(employees)s and attendance_date <= %(to_date)s
		""",
		params,
		as_dict=True,
	)
	out = {}
	for r in rows:
		if getdate(r.attendance_date) <= getdate(calc_dates[r.employee]):
			out[r.employee] = out.get(r.employee, 0) + 1
	return out


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
	salaries, salary_errors = get_earnings(
		emp_ids, calc_dates, rule.components, company, as_of, settings.get("earnings_source")
	)
	unpaid = get_unpaid_days(emp_ids, calc_dates) if settings.deduct_unpaid_days else {}
	leaves = {}
	if leave_mode(settings) != LEAVE_OFF:
		leaves = get_leave_balances([e for e in emp_ids if e in salaries], rule.leave_types, calc_dates)
	days_in_month = flt(settings.days_in_month) or 30

	rows, skipped, account_cache = [], [], {}
	skipped_reasons = {}
	for e in employees:
		sal = salaries.get(e.name)
		if not sal or not sum(sal["components"].values()):
			skipped.append(e.name)
			skipped_reasons[e.name] = salary_errors.get(e.name) or _(
				"مالوش Salary Structure Assignment ولا Salary Slip فيهم مكونات الـ Rule"
			)
			continue

		deduct = unpaid.get(e.name, 0)
		service_days, basis, years, text = get_service(e.date_of_joining, e.calc_date, rule, deduct)
		weighted, parts = get_weighted_days(service_days, basis, years, rule, settings.get("slab_calculation"))
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
				salary_slip=sal["ref"] if sal["source"] == SOURCE_SLIP else None,
				salary_structure_assignment=sal["ref"] if sal["source"] == SOURCE_SSA else None,
				earnings_note=sal.get("note"),
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
	frappe.flags.eosb_skipped_reasons = skipped_reasons
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
			                          order by p.posting_date desc, p.creation desc, d.is_reversal asc, d.idx desc) as rn
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
		select employee, total_leave_amount, leave_provision_account
		from (
			select e.employee, e.total_leave_amount, e.leave_provision_account,
			       row_number() over (partition by e.employee
			                          order by p.posting_date desc, p.creation desc) as rn
			from `tabEOSB Provision Employee` e
			join `tabEOSB Provision` p on p.name = e.parent
			where p.docstatus = 1 and p.company = %(company)s
			  and p.posting_date <= %(as_of)s and p.name != %(exclude)s
			  and ifnull(e.leave_provision_account, '') != ''
		) x
		where rn = 1
		""",
		params,
		as_dict=True,
	)
	leave = {
		r.employee: frappe._dict(amount=flt(r.total_leave_amount), account=r.leave_provision_account)
		for r in leave_rows
	}
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
	# الحسابات القديمة اللي اتقيد عليها قبل كده (لو حساب اتغير، النقل بين القديم والجديد بيصفّر بعضه)
	accounts.update(
		frappe.db.sql_list(
			"""
			select distinct d.provision_account from `tabEOSB Provision Detail` d
			join `tabEOSB Provision` p on p.name = d.parent
			where p.docstatus = 1 and p.company = %(company)s and ifnull(d.provision_account, '') != ''
			union
			select distinct e.leave_provision_account from `tabEOSB Provision Employee` e
			join `tabEOSB Provision` p on p.name = e.parent
			where p.docstatus = 1 and p.company = %(company)s and ifnull(e.leave_provision_account, '') != ''
			""",
			{"company": company},
		)
	)
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


def get_gl_account_balances(company, as_of, accounts):
	"""رصيد كل حساب مخصص في الـ GL (دائن − مدين) للحساب كله، بغض النظر عن الموظف (Party).
	ده اللي بنطابق عليه: الرصيد القديم المجمّع + حركات الأبلكيشن."""
	if not accounts:
		return {}
	rows = frappe.db.sql(
		"""
		select account, sum(credit - debit) as balance
		from `tabGL Entry`
		where company = %(company)s and is_cancelled = 0
		  and account in %(accounts)s and posting_date <= %(as_of)s
		group by account
		""",
		{"company": company, "as_of": as_of, "accounts": tuple(accounts)},
		as_dict=True,
	)
	return {r.account: flt(r.balance, 2) for r in rows}


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
