import frappe
from frappe.utils import cint


def execute():
	"""Leave Balance Provision بقى Select (Disabled / Calculate only / Calculate and post) بدل الـ checkbox القديم.
	بننقل القيمة القديمة مرة واحدة: متعلّم = Calculate and post، مش متعلّم = Disabled."""
	if frappe.db.get_single_value("EOSB Provision Settings", "leave_provision_mode"):
		return
	include = frappe.db.get_single_value("EOSB Provision Settings", "include_leave_provision")
	mode = "Calculate and post" if cint(include) else "Disabled"
	frappe.db.set_single_value("EOSB Provision Settings", "leave_provision_mode", mode)
