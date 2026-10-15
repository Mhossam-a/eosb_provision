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
	# ------------------------------------------------------------ Get Employees / Calculate
	@frappe.whitelist()
	def get_employees(self):
		"""يملى الجدول بكل الموظفين اللي عليهم مخصص ويحسبهم (بيمسح الجدول القديم)."""
		return self.calculate_rows(None, update_notes=True)

	@frappe.whitelist()
	def calculate(self, employees=None):
		"""يحسب الموظفين اللي في الجدول بس (من غير ما يجيب الكل من الأول).
		employees: موظف أو list (لما تختار موظف في الجدول بيتحسب لوحده)."""
		if isinstance(employees, str):
			employees = frappe.parse_json(employees) if employees.strip().startswith("[") else [employees]
		partial = bool(employees)
		employees = employees or [e.employee for e in self.employees if e.employee]
		employees = list(dict.fromkeys(e for e in employees if e))
		if not employees:
			frappe.throw(_("ضيف موظفين في الجدول أو اضغط Get Employees"))
		return self.calculate_rows(employees, update_notes=not partial)

	def calculate_rows(self, employees=None, update_notes=True):
		if not self.company or not self.posting_date:
			frappe.throw(_("اختار الشركة والتاريخ الأول"))

		period_start = frappe.db.sql(
			"""select max(posting_date) from `tabEOSB Provision`
			where company = %s and docstatus = 1 and posting_date < %s and name != %s""",
			(self.company, self.posting_date, self.name or ""),
		)[0][0]
		rows, skipped, rule, settings = calc.compute_employees(
			self.company,
			self.posting_date,
			None if employees else self.department,
			employee=employees,
			period_start=period_start,
		)
		reasons = dict(frappe.flags.get("eosb_skipped_reasons") or {})
		skipped = list(skipped)
		if employees:
			found = {r.employee for r in rows} | set(skipped)
			missing = [e for e in employees if e not in found]
			reasons.update(calc.explain_not_eligible(missing, self.company, self.posting_date, settings))
			skipped += missing
		self.gratuity_rule = rule.name

		new_rows = {r.employee: self.employee_row(r) for r in rows}
		if employees:
			targets = set(employees)
			kept = []
			for e in self.employees:
				if not e.employee:
					continue
				if e.employee in new_rows:
					kept.append(new_rows.pop(e.employee))
				elif e.employee not in targets:
					kept.append(
						{k: v for k, v in e.as_dict().items() if k not in ("name", "idx", "parent", "parentfield", "parenttype", "doctype")}
					)
			kept.extend(new_rows.values())
			details = [
				{k: v for k, v in d.as_dict().items() if k not in ("name", "idx", "parent", "parentfield", "parenttype", "doctype")}
				for d in self.details
				if d.employee not in targets and not d.is_reversal
			]
		else:
			kept = list(new_rows.values())
			details = []
		for r in rows:
			for c in r.components:
				details.append(dict(c, employee=r.employee))

		self.set("employees", [])
		for e in kept:
			self.append("employees", e)
		self.set("details", [])
		for d in details:
			self.append("details", d)

		days = "360 (KSA Labor Law: الشهر 30 يوم)" if rule.method == calc.KSA_METHOD else rule.days_per_year
		notes = [
			_("Gratuity Rule: {0} · Work Experience: {1} · Days/Year: {2} · Leave Types: {3} · Earnings: {4}").format(
				rule.name,
				rule.method or "-",
				days,
				", ".join(rule.leave_types) or "-",
				settings.get("earnings_source") or calc.SOURCE_SSA,
			)
		]
		leavers = [r.employee for r in rows if r.left_in_period]
		if leavers:
			notes.append(
				_(
					"موظفين سابوا جوه الفترة (اتحسبلهم لحد يوم الترك، وبعد كده مش هيظهروا): {0}"
				).format(", ".join(leavers))
			)
		if skipped:
			names = dict(
				frappe.get_all("Employee", filters={"name": ("in", skipped)}, fields=["name", "employee_name"], as_list=True)
			)
			notes.append(
				_("موظفين مااتحسبوش (رصيدهم المحجوز زي ما هو):")
				+ "\n"
				+ "\n".join(f"- {e} {names.get(e) or ''}: {reasons.get(e, '')}" for e in skipped)
			)
		if self.is_opening and not employees:
			notes.append(self.opening_gl_note(rows, settings))
		if update_notes:
			self.notes = "\n".join(notes)
		self.apply_booked()
		self.set_totals()
		return notes

	def opening_gl_note(self, rows, settings):
		"""مطابقة الافتتاحي مع رصيد حسابات المخصص الحالي في الـ GL (قبل ما تعمل Submit)."""
		accounts = calc.get_provision_accounts(self.company, settings)
		gl = flt(sum(calc.get_gl_account_balances(self.company, self.posting_date, accounts).values()), 2)
		total = sum(flt(r.allocated_amount) for r in rows)
		if calc.leave_mode(settings) == calc.LEAVE_POST:
			total += sum(flt(r.total_leave_amount) for r in rows)
		total = flt(total, 2)
		diff = flt(gl - total, 2)
		note = _("مطابقة الافتتاحي مع الـ GL: رصيد حسابات المخصص الحالي {0} · إجمالي الافتتاحي {1} · الفرق {2}").format(
			f"{gl:,.2f}", f"{total:,.2f}", f"{diff:,.2f}"
		)
		if calc.opening_mode(settings) == calc.OPENING_POST:
			note += "\n" + _(
				"Opening Provision = Post entry: القيد هيتعمل بإجمالي الافتتاحي كله، فلو الرصيد القديم لسه في الـ GL اعكسه الأول وإلا هيتحسب مرتين"
			)
		elif diff:
			note += "\n" + _(
				"الفرق ده هيظهر في تقرير EOSB Provision Balance كـ «فرق الافتتاحي» لحد ما تقفله بقيد تسوية واحد"
			)
		return note

	@staticmethod
	def employee_row(r):
		return {
			"employee": r.employee,
			"employee_name": r.employee_name,
			"department": r.department,
			"date_of_joining": r.date_of_joining,
			"relieving_date": r.relieving_date,
			"service_days": r.service_days,
			"service_text": r.service_text,
			"number_of_years": r.number_of_years,
			"salary_slip": r.salary_slip,
			"salary_structure_assignment": r.salary_structure_assignment,
			"earnings_note": r.earnings_note,
			"components": r.components_text,
			"last_salary": r.last_salary,
			"allocated_amount": r.allocated_amount,
			"slab_details": r.slab_details,
			"leave_balance": r.leave_balance,
			"day_salary": r.day_salary,
			"total_leave_amount": r.total_leave_amount,
		}

	def calculate_missing_rows(self):
		"""موظفين اتضافوا في الجدول بإيدك ومااتحسبوش: بيتحسبوا وقت الـ Save."""
		self.set("employees", [e for e in self.employees if e.employee])
		done = {d.employee for d in self.details}
		missing = [e.employee for e in self.employees if e.employee not in done]
		if not missing:
			return
		notes = self.calculate_rows(missing, update_notes=False)
		dropped = [e for e in missing if e not in {x.employee for x in self.employees}]
		if dropped and len(notes) > 1:
			frappe.msgprint("<br>".join(frappe.utils.escape_html(n).replace("\n", "<br>") for n in notes[1:]),
				title=_("موظفين اتشالوا من الجدول"), indicator="orange")

	# ------------------------------------------------------------ booked / adjustment
	def apply_booked(self):
		"""المحجوز = آخر قيمة في آخر مخصص Submitted (أول مرة = الافتتاحي).
		التسوية = المستحق − المحجوز.
		- لو موظف اتشال منه مكوّن (اتنقل إدارة مثلاً) بيتعمله سطر عكس.
		- لو حساب المخصص بتاع المكوّن اتغير، الرصيد القديم بيتعكس من الحساب القديم
		  والمستحق كله بيتقيد على الحساب الجديد (نقل رصيد بين الحسابين)."""
		self.set("details", [d for d in self.details if not d.is_reversal])
		booked, leave_booked = calc.get_booked(self.company, self.posting_date, exclude=self.name)
		in_run = {e.employee for e in self.employees}
		present = {(d.employee, d.salary_component) for d in self.details}
		moved = set()

		for d in self.details:
			b = booked.get((d.employee, d.salary_component))
			if b and b.provision_account and d.provision_account and b.provision_account != d.provision_account:
				moved.add((d.employee, d.salary_component))
				b = None
			d.booked_amount = flt(b.required_amount, 2) if b else 0
			d.adjustment = 0 if self.is_opening else flt(flt(d.required_amount) - d.booked_amount, 2)

		for (emp, comp), b in booked.items():
			if emp not in in_run or not flt(b.required_amount):
				continue
			if (emp, comp) in present and (emp, comp) not in moved:
				continue
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

		settings = calc.get_settings()
		leave_account = calc.get_company_accounts(settings, self.company).get("leave_provision_account")
		# Calculate only: الإجازات بتظهر كقيمة بس، مفيش حساب ولا محجوز ولا تسوية (مفيش قيد)
		post_leave = calc.leave_mode(settings) == calc.LEAVE_POST
		self.flags.leave_previous_accounts = {}
		for e in self.employees:
			e.eosb_booked = flt(by_emp.get(e.employee, [0, 0])[0], 2)
			e.eosb_adjustment = flt(by_emp.get(e.employee, [0, 0])[1], 2)
			lb = leave_booked.get(e.employee) or frappe._dict()
			e.leave_booked = flt(lb.get("amount"), 2)
			e.leave_provision_account = leave_account if post_leave else None
			if lb.get("account") and e.leave_provision_account and lb.account != e.leave_provision_account:
				self.flags.leave_previous_accounts[e.employee] = lb.account
			e.leave_adjustment = (
				0 if (self.is_opening or not post_leave) else flt(flt(e.total_leave_amount) - e.leave_booked, 2)
			)

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
			if self.company and self.posting_date:
				self.calculate_missing_rows()
		self.sync_opening_amounts()
		if self.details:
			self.apply_booked()
		self.set_totals()

	def before_submit(self):
		if not self.employees:
			frappe.throw(_("اضغط Get Employees الأول"))
		done = {d.employee for d in self.details}
		not_done = [e.employee for e in self.employees if e.employee not in done]
		if not_done:
			frappe.throw(_("الموظفين دول مااتحسبوش، اضغط Calculate: {0}").format(", ".join(not_done)))
		self.validate_order()
		if not self.is_opening or calc.opening_mode(calc.get_settings()) == calc.OPENING_POST:
			# Accounts Frozen Till Date: نفس فحص ERPNext، قبل ما يتعمل القيد
			# (v15: في Accounts Settings، v16: في الـ Company)
			import inspect

			from erpnext.accounts.general_ledger import check_freezing_date

			if "company" in inspect.signature(check_freezing_date).parameters:
				check_freezing_date(self.posting_date, self.company)
			else:
				check_freezing_date(self.posting_date)
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
			if calc.opening_mode(calc.get_settings()) != calc.OPENING_POST:
				self.db_set("status", "Opening")
				return
			je = self.make_opening_journal_entry()
		else:
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
		if calc.leave_mode(settings) == calc.LEAVE_POST:
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

		# حساب مخصص الإجازات اتغير في الإعدادات: نقل الرصيد القديم للحساب الجديد
		for e in self.employees:
			old_account = (self.flags.leave_previous_accounts or {}).get(e.employee)
			if old_account and flt(e.leave_booked):
				amt = flt(e.leave_booked, 2)
				add(old_account, amt, party_for(old_account, e.employee))  # مدين الحساب القديم
				key = (e.employee, e.leave_provision_account)
				prov_by_emp[key] = flt(prov_by_emp.get(key, 0) + amt, 2)  # دائن الحساب الجديد

		for account, amount in expense_totals.items():
			add(account, amount)  # مدين المصروف (دائن لو بالسالب)
		for (employee, account), amount in prov_by_emp.items():
			add(account, -amount, party_for(account, employee))  # دائن المخصص (مدين لو بالسالب)

		if not lines:
			return None
		return self.create_journal_entry(
			lines,
			settings,
			_("مخصص نهاية الخدمة ورصيد الإجازات حتى {0} - {1}").format(
				frappe.format(self.posting_date, "Date"), self.name
			),
		)

	def make_opening_journal_entry(self):
		"""Opening Provision = Post entry: دائن المخصص لكل موظف (مبلغ الافتتاحي كله)، ومدين Opening Counter Account.
		لو الرصيد القديم لسه في الـ GL لازم يتعكس قبل كده، وإلا هيتحسب مرتين."""
		settings = calc.get_settings()
		company_accounts = calc.get_company_accounts(settings, self.company)
		counter = company_accounts.get("opening_counter_account")
		if not counter:
			frappe.throw(
				_("حط Opening Counter Account لشركة {0} في EOSB Provision Settings (جدول Company Accounts)").format(
					self.company
				)
			)
		cost_center = company_accounts.get("cost_center") or frappe.get_cached_value(
			"Company", self.company, "cost_center"
		)

		credits = {}
		for d in self.details:
			if d.is_reversal or not flt(d.required_amount, 2):
				continue
			key = (d.employee, d.provision_account)
			credits[key] = flt(credits.get(key, 0) + flt(d.required_amount), 2)
		if calc.leave_mode(settings) == calc.LEAVE_POST:
			leave_prov = company_accounts.get("leave_provision_account")
			for e in self.employees:
				if not flt(e.total_leave_amount, 2):
					continue
				if not leave_prov:
					frappe.throw(
						_("حط Leave Provision Account لشركة {0} في EOSB Provision Settings").format(self.company)
					)
				key = (e.employee, leave_prov)
				credits[key] = flt(credits.get(key, 0) + flt(e.total_leave_amount), 2)

		lines = []
		for (employee, account), amount in credits.items():
			row = {
				"account": account,
				"cost_center": cost_center,
				"debit_in_account_currency": 0,
				"credit_in_account_currency": amount,
			}
			if frappe.get_cached_value("Account", account, "account_type") in ("Payable", "Receivable"):
				row.update({"party_type": "Employee", "party": employee})
			lines.append(row)
		total = flt(sum(credits.values()), 2)
		if not total:
			return None
		lines.append(
			{
				"account": counter,
				"cost_center": cost_center,
				"debit_in_account_currency": total,
				"credit_in_account_currency": 0,
			}
		)
		return self.create_journal_entry(
			lines,
			settings,
			_("رصيد افتتاحي لمخصص نهاية الخدمة ورصيد الإجازات حتى {0} - {1}").format(
				frappe.format(self.posting_date, "Date"), self.name
			),
		)

	def create_journal_entry(self, lines, settings, remark):
		je = frappe.new_doc("Journal Entry")
		je.voucher_type = "Journal Entry"
		je.company = self.company
		je.posting_date = self.posting_date
		je.eosb_provision = self.name
		je.user_remark = remark
		for row in lines:
			je.append("accounts", row)
		je.flags.ignore_permissions = True
		je.flags.from_eosb_provision = True
		je.insert()
		self.db_set("journal_entry_hash", journal_entry_hash(je))
		if settings.submit_journal_entry:
			je.submit()
		return je


def journal_entry_hash(je):
	"""بصمة سطور القيد (الحساب، والطرف، والمبالغ). بتتسجل وقت إنشاء القيد،
	وأي تعديل يدوي في المبالغ أو الحسابات بعد كده بيترفض."""
	import hashlib
	import json

	rows = sorted(
		(
			a.account,
			a.party_type or "",
			a.party or "",
			f"{flt(a.debit_in_account_currency, 2):.2f}",
			f"{flt(a.credit_in_account_currency, 2):.2f}",
		)
		for a in je.accounts
	)
	return hashlib.md5(json.dumps(rows).encode()).hexdigest()


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
