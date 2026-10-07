# Copyright (c) 2026
# For license information, please see license.txt
"""
EOSB Provision
  - Is Opening = ✓  → "تجاهل إنشاء القيد": بيسجل الرصيد القديم (الافتتاحي) كأساس، من غير قيد.
  - أي مخصص بعده    → المستحق الحالي − المحجوز (من آخر مخصص، أول مرة = الافتتاحي) = التسوية → قيد بالفرق بس.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, flt, get_first_day, get_last_day, getdate, nowdate

from eosb_provision.eosb_provision import calculation as calc


class EOSBProvision(Document):
	# ------------------------------------------------------------ Get Employees
	@frappe.whitelist()
	def get_employees(self):
		"""يملى الجدول بالموظفين الـ Active ويحسب المستحق والمحجوز والتسوية."""
		if not self.company or not self.posting_date:
			frappe.throw(_("اختار الشركة والتاريخ الأول"))

		settings = calc.get_settings()
		rule = calc.load_rule(settings.gratuity_rule)
		company_accounts = calc.get_company_accounts(settings, self.company)
		as_of = getdate(self.posting_date)
		self.gratuity_rule = rule.name

		employees = calc.get_active_employees(self.company, as_of, self.department)
		emp_ids = [e.name for e in employees]
		salaries = calc.get_salary_components(emp_ids, rule.components, self.company, as_of)
		leaves = {}
		if settings.include_leave_provision:
			leaves = calc.get_leave_balances([e for e in emp_ids if e in salaries], rule.leave_types, as_of)

		days_in_month = flt(settings.days_in_month) or 30
		self.set("employees", [])
		self.set("details", [])
		notes = [
			_("Gratuity Rule: {0} · Work Experience: {1} · Days/Year: {2} · Leave Types: {3}").format(
				rule.name, rule.method or "-", rule.days_per_year, ", ".join(rule.leave_types) or "-"
			)
		]
		skipped = []
		account_cache = {}

		for e in employees:
			sal = salaries.get(e.name)
			if not sal or not sum(sal["components"].values()):
				skipped.append(e.name)
				continue

			service_days, basis, years = calc.get_service(e.date_of_joining, as_of, rule, settings)
			weighted, parts = calc.get_weighted_days(service_days, basis, years, rule)
			earnings = flt(sum(sal["components"].values()), 2)
			total_eosb = flt(earnings * weighted / basis, 2)

			# الراتب متقسم على كذا مكوّن: المستحق بيتوزع عليهم بنسبة كل مكوّن من الأجر
			per_component = calc.distribute(total_eosb, sal["components"])
			for comp, comp_amount in sal["components"].items():
				key = (comp, self.company)
				if key not in account_cache:
					account_cache[key] = calc.get_component_accounts(comp, self.company, company_accounts)
				exp, prov = account_cache[key]
				self.append(
					"details",
					{
						"employee": e.name,
						"salary_component": comp,
						"component_amount": comp_amount,
						"required_amount": per_component[comp],
						"expense_account": exp,
						"provision_account": prov,
					},
				)

			leave_balance, _leave_text = leaves.get(e.name, (0, ""))
			slab_text = " | ".join(
				"{0:g}→{1}: {2:g} يوم × {3:g}".format(
					s.from_year, "∞" if s.to_year >= 9999 else f"{s.to_year:g}", portion, s.fraction
				)
				for s, portion in parts
			)
			self.append(
				"employees",
				{
					"employee": e.name,
					"employee_name": e.employee_name,
					"department": e.department,
					"date_of_joining": e.date_of_joining,
					"service_days": service_days,
					"number_of_years": flt(years, 4),
					"salary_slip": sal["slip"],
					"components": " | ".join(f"{c}: {a:,.2f}" for c, a in sal["components"].items()),
					"last_salary": earnings,
					"allocated_amount": total_eosb,
					"slab_details": slab_text,
					"leave_balance": leave_balance,
					"day_salary": flt(earnings / days_in_month, 2),
					"total_leave_amount": flt(leave_balance * earnings / days_in_month, 2),
				},
			)

		if skipped:
			notes.append(
				_(
					"موظفين Active من غير Salary Slip عاملة Submit فيها مكونات الـ Rule (اتخطوا، ورصيدهم المحجوز زي ما هو): {0}"
				).format(", ".join(skipped))
			)
		self.notes = "\n".join(notes)
		self.apply_booked()
		self.set_totals()
		return notes

	# ------------------------------------------------------------ booked / adjustment
	def apply_booked(self):
		"""المحجوز = آخر قيمة في آخر مخصص Submitted (أول مرة = الافتتاحي).
		التسوية = المستحق − المحجوز. ولو موظف اتشال منه مكوّن (اتنقل إدارة مثلاً) بيتعمله سطر عكس."""
		self.set("details", [d for d in self.details if not d.is_reversal])
		booked, leave_booked = calc.get_booked(self.company, self.posting_date, exclude=self.name)
		in_run = {e.employee for e in self.employees}
		present = {(d.employee, d.salary_component) for d in self.details}

		for d in self.details:
			b = booked.get((d.employee, d.salary_component))
			d.booked_amount = flt(b.required_amount, 2) if b else 0
			d.adjustment = 0 if self.is_opening else flt(flt(d.required_amount) - d.booked_amount, 2)

		for (emp, comp), b in booked.items():
			if emp in in_run and (emp, comp) not in present and flt(b.required_amount):
				self.append(
					"details",
					{
						"employee": emp,
						"salary_component": comp,
						"component_amount": 0,
						"required_amount": 0,
						"booked_amount": flt(b.required_amount, 2),
						"adjustment": 0 if self.is_opening else -flt(b.required_amount, 2),
						"expense_account": b.expense_account,
						"provision_account": b.provision_account,
						"is_reversal": 1,
					},
				)

		by_emp = {}
		for d in self.details:
			agg = by_emp.setdefault(d.employee, [0, 0])
			agg[0] += flt(d.booked_amount)
			agg[1] += flt(d.adjustment)
		for e in self.employees:
			e.eosb_booked = flt(by_emp.get(e.employee, [0, 0])[0], 2)
			e.eosb_adjustment = flt(by_emp.get(e.employee, [0, 0])[1], 2)
			e.leave_booked = flt(leave_booked.get(e.employee), 2)
			e.leave_adjustment = 0 if self.is_opening else flt(flt(e.total_leave_amount) - e.leave_booked, 2)

	# ------------------------------------------------------------ opening edits
	def sync_opening_amounts(self):
		"""في الافتتاحي: لو كتبت الرصيد القديم الفعلي في EOSB Amount، بيتوزع على مكونات الموظف بنفس النسبة."""
		if not self.is_opening:
			return
		for e in self.employees:
			rows = [d for d in self.details if d.employee == e.employee and not d.is_reversal]
			if not rows:
				continue
			weights = {i: flt(d.component_amount) for i, d in enumerate(rows)}
			split = calc.distribute(e.allocated_amount, weights)
			for i, d in enumerate(rows):
				d.required_amount = split[i]

	def set_totals(self):
		self.total_eosb = flt(sum(flt(e.allocated_amount) for e in self.employees), 2)
		self.total_eosb_booked = flt(sum(flt(d.booked_amount) for d in self.details), 2)
		self.total_eosb_adjustment = flt(sum(flt(d.adjustment) for d in self.details), 2)
		self.total_leave_amount = flt(sum(flt(e.total_leave_amount) for e in self.employees), 2)
		self.total_leave_booked = flt(sum(flt(e.leave_booked) for e in self.employees), 2)
		self.total_leave_adjustment = flt(sum(flt(e.leave_adjustment) for e in self.employees), 2)

	# ------------------------------------------------------------ document events
	def validate(self):
		if self.docstatus == 0:
			self.status = "Draft"
		self.sync_opening_amounts()
		if self.details:
			self.apply_booked()
		self.set_totals()

	def before_submit(self):
		if not self.employees:
			frappe.throw(_("اضغط Get Employees الأول"))
		self.validate_order()
		if self.is_opening:
			previous = frappe.db.exists(
				"EOSB Provision", {"company": self.company, "docstatus": 1, "name": ("!=", self.name)}
			)
			if previous:
				frappe.throw(_("مينفعش تعمل مخصص افتتاحي بعد مخصص اتعمله Submit ({0})").format(previous))
		self.apply_booked()
		self.set_totals()

	def on_submit(self):
		if self.is_opening:
			self.db_set("status", "Opening")
			return
		je = self.make_journal_entry()
		if not je:
			self.db_set("status", "No Adjustment")
			return
		self.db_set("journal_entry", je.name)
		self.db_set("status", "Posted" if je.docstatus == 1 else "JE Draft")

	def before_cancel(self):
		later = frappe.db.sql(
			"""select name from `tabEOSB Provision`
			where company = %s and docstatus = 1 and name != %s
			  and (posting_date > %s or (posting_date = %s and creation > %s))
			limit 1""",
			(self.company, self.name, self.posting_date, self.posting_date, self.creation),
		)
		if later:
			frappe.throw(_("لازم تعمل Cancel لـ {0} الأول، لأنه اتحسب على أساس المخصص ده").format(later[0][0]))
		self.ignore_linked_doctypes = ("GL Entry", "Journal Entry")

	def on_cancel(self):
		if self.journal_entry and frappe.db.exists("Journal Entry", self.journal_entry):
			je = frappe.get_doc("Journal Entry", self.journal_entry)
			je.flags.ignore_links = True
			je.flags.from_eosb_provision = True
			if je.docstatus == 1:
				je.cancel()
			elif je.docstatus == 0:
				frappe.delete_doc("Journal Entry", je.name, ignore_permissions=True, force=True)
		self.db_set("status", "Cancelled")

	def validate_order(self):
		latest = frappe.db.sql(
			"""select max(posting_date) from `tabEOSB Provision`
			where company = %s and docstatus = 1 and name != %s""",
			(self.company, self.name),
		)[0][0]
		if latest and getdate(self.posting_date) < getdate(latest):
			frappe.throw(
				_("فيه مخصص Submitted بتاريخ {0}. مينفعش تعمل مخصص بتاريخ أقدم").format(
					frappe.format(latest, "Date")
				)
			)

	# ------------------------------------------------------------ journal entry
	def make_journal_entry(self):
		settings = calc.get_settings()
		company_accounts = calc.get_company_accounts(settings, self.company)
		cost_center = company_accounts.get("cost_center") or frappe.get_cached_value(
			"Company", self.company, "cost_center"
		)

		lines = []

		def party_for(account, employee):
			if frappe.get_cached_value("Account", account, "account_type") in ("Payable", "Receivable"):
				return {"party_type": "Employee", "party": employee}
			return {}

		def add(account, amount, extra=None):
			amount = flt(amount, 2)
			if not amount:
				return
			row = {
				"account": account,
				"cost_center": cost_center,
				"debit_in_account_currency": amount if amount > 0 else 0,
				"credit_in_account_currency": -amount if amount < 0 else 0,
			}
			row.update(extra or {})
			lines.append(row)

		# نهاية الخدمة: المخصص سطر لكل (موظف + حساب)، والمصروف مجمّع على الحساب
		prov_by_emp = {}
		expense_totals = {}
		for d in self.details:
			adj = flt(d.adjustment, 2)
			if not adj:
				continue
			key = (d.employee, d.provision_account)
			prov_by_emp[key] = flt(prov_by_emp.get(key, 0) + adj, 2)
			expense_totals[d.expense_account] = flt(expense_totals.get(d.expense_account, 0) + adj, 2)

		# الإجازات
		if settings.include_leave_provision:
			leave_prov = company_accounts.get("leave_provision_account")
			leave_exp = company_accounts.get("leave_expense_account")
			for e in self.employees:
				adj = flt(e.leave_adjustment, 2)
				if not adj:
					continue
				if not (leave_prov and leave_exp):
					frappe.throw(
						_(
							"حط Leave Expense Account و Leave Provision Account لشركة {0} في EOSB Provision Settings"
						).format(self.company)
					)
				key = (e.employee, leave_prov)
				prov_by_emp[key] = flt(prov_by_emp.get(key, 0) + adj, 2)
				expense_totals[leave_exp] = flt(expense_totals.get(leave_exp, 0) + adj, 2)

		for account, amount in expense_totals.items():
			add(account, amount)  # مدين المصروف (دائن لو بالسالب)
		for (employee, account), amount in prov_by_emp.items():
			add(account, -amount, party_for(account, employee))  # دائن المخصص (مدين لو بالسالب)

		if not lines:
			return None

		je = frappe.new_doc("Journal Entry")
		je.voucher_type = "Journal Entry"
		je.company = self.company
		je.posting_date = self.posting_date
		je.eosb_provision = self.name
		je.user_remark = _("مخصص نهاية الخدمة ورصيد الإجازات حتى {0} - {1}").format(
			frappe.format(self.posting_date, "Date"), self.name
		)
		for row in lines:
			je.append("accounts", row)
		je.flags.ignore_permissions = True
		je.insert()
		if settings.submit_journal_entry:
			je.submit()
		return je


# ---------------------------------------------------------------- scheduler
def auto_create():
	"""Daily: أول يوم في الشهر (أو الربع) بيعمل مخصص لكل شركة في الإعدادات بتاريخ آخر يوم في الفترة اللي فاتت."""
	settings = frappe.get_single("EOSB Provision Settings")
	if not settings.auto_create or not settings.gratuity_rule:
		return
	today = getdate(nowdate())
	if today.day != 1:
		return
	if settings.frequency == "Quarterly" and today.month not in (1, 4, 7, 10):
		return
	posting_date = get_last_day(add_days(get_first_day(today), -1))

	for row in settings.company_accounts:
		if frappe.db.exists(
			"EOSB Provision", {"company": row.company, "posting_date": posting_date, "docstatus": ("<", 2)}
		):
			continue
		try:
			doc = frappe.new_doc("EOSB Provision")
			doc.company = row.company
			doc.posting_date = posting_date
			doc.get_employees()
			doc.insert(ignore_permissions=True)
			doc.submit()
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"EOSB Provision auto create failed: {row.company}")
