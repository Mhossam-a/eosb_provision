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
