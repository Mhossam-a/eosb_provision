import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

LEGACY_EXPENSE = "custom_gratuity_expense_account"
APP_CUSTOM_FIELDS = {
	"Salary Component": ["eosb_accounts_section", "eosb_accounts"],
	"Journal Entry": ["eosb_provision"],
}
LEGACY_PROVISION = "custom_gratuity_payable_account"


def after_install():
	create_custom_fields(
		{
			"Salary Component": [
				{
					"fieldname": "eosb_accounts_section",
					"fieldtype": "Section Break",
					"label": "EOSB Provision Accounts",
					"insert_after": "accounts",
				},
				{
					"fieldname": "eosb_accounts",
					"fieldtype": "Table",
					"label": "EOSB Provision Accounts",
					"options": "EOSB Component Account",
					"insert_after": "eosb_accounts_section",
					"description": "حساب المصروف وحساب المخصص لكل شركة، للمكوّن ده",
				},
			],
			"Journal Entry": [
				{
					"fieldname": "eosb_provision",
					"fieldtype": "Link",
					"label": "EOSB Provision",
					"options": "EOSB Provision",
					"insert_after": "voucher_type",
					"read_only": 1,
					"no_copy": 1,
					"print_hide": 1,
				},
			],
		},
		update=True,
	)
	migrate_legacy_component_accounts()


def migrate_legacy_component_accounts():
	"""ينقل الحقلين القديمين custom_gratuity_expense_account / custom_gratuity_payable_account
	(لو موجودين في الـ Salary Component) لجدول EOSB Provision Accounts، بشركة الحساب.
	مابيكررش صف موجود لنفس الشركة."""
	meta = frappe.get_meta("Salary Component")
	if not (meta.has_field(LEGACY_EXPENSE) and meta.has_field(LEGACY_PROVISION)):
		return

	rows = frappe.get_all(
		"Salary Component",
		filters={LEGACY_EXPENSE: ("is", "set"), LEGACY_PROVISION: ("is", "set")},
		fields=["name", LEGACY_EXPENSE, LEGACY_PROVISION],
	)
	moved = 0
	for r in rows:
		exp, prov = r.get(LEGACY_EXPENSE), r.get(LEGACY_PROVISION)
		company = frappe.db.get_value("Account", exp, "company")
		if not company or frappe.db.get_value("Account", prov, "company") != company:
			continue
		if frappe.db.exists(
			"EOSB Component Account", {"parent": r.name, "parenttype": "Salary Component", "company": company}
		):
			continue
		doc = frappe.get_doc("Salary Component", r.name)
		doc.append("eosb_accounts", {"company": company, "expense_account": exp, "provision_account": prov})
		doc.flags.ignore_validate = True
		doc.flags.ignore_permissions = True
		doc.save()
		moved += 1
	if moved:
		print(f"EOSB Provision: moved legacy accounts of {moved} Salary Component(s) to EOSB Provision Accounts")


def before_uninstall():
	"""يمسح الحقول اللي الـ App ضافها على Salary Component و Journal Entry،
	عشان الشاشات دي ماتبوظش بعد إزالة الـ App."""
	for dt, fields in APP_CUSTOM_FIELDS.items():
		for fieldname in fields:
			name = frappe.db.get_value("Custom Field", {"dt": dt, "fieldname": fieldname})
			if name:
				frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
	frappe.clear_cache(doctype="Salary Component")
	frappe.clear_cache(doctype="Journal Entry")
