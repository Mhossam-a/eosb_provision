import frappe
from frappe import _


def _provision(je):
	name = je.get("eosb_provision")
	if not name or not frappe.db.exists("EOSB Provision", name):
		return None
	return frappe.db.get_value("EOSB Provision", name, ["name", "docstatus"], as_dict=True)


def journal_entry_on_submit(doc, method=None):
	p = _provision(doc)
	if p and p.docstatus == 1:
		frappe.db.set_value("EOSB Provision", p.name, "status", "Posted", update_modified=False)


def journal_entry_before_cancel(doc, method=None):
	if doc.flags.get("from_eosb_provision"):
		return
	p = _provision(doc)
	if p and p.docstatus == 1:
		frappe.throw(
			_("القيد ده جاي من {0}. اعمل Cancel للمخصص نفسه، وهو هيلغي القيد").format(
				frappe.get_desk_link("EOSB Provision", p.name)
			)
		)


def journal_entry_on_trash(doc, method=None):
	if doc.flags.get("from_eosb_provision"):
		return
	p = _provision(doc)
	if p and p.docstatus == 1:
		frappe.throw(
			_("القيد ده جاي من {0}. اعمل Cancel للمخصص نفسه بدل ما تمسح القيد").format(
				frappe.get_desk_link("EOSB Provision", p.name)
			)
		)


def journal_entry_validate(doc, method=None):
	"""القيد اللي جاي من مخصص مينفعش تتعدل حساباته أو مبالغه يدوي (عشان المحجوز يفضل مطابق للـ GL).
	تقدر تغير البيان أو الـ Cost Center عادي."""
	if doc.flags.get("from_eosb_provision") or doc.is_new():
		return
	p = _provision(doc)
	if not p or p.docstatus != 1:
		return
	from eosb_provision.eosb_provision.doctype.eosb_provision.eosb_provision import journal_entry_hash

	stored = frappe.db.get_value("EOSB Provision", p.name, "journal_entry_hash")
	if stored and stored != journal_entry_hash(doc):
		frappe.throw(
			_(
				"مينفعش تعدّل الحسابات أو المبالغ في قيد جاي من {0}. لو محتاج تعديل: اعمل Cancel للمخصص، وصلّح الإعدادات، واعمل مخصص جديد"
			).format(frappe.get_desk_link("EOSB Provision", p.name))
		)
