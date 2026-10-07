from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def after_install():
	create_custom_fields(
		{
			"Salary Component": [
				{
					"fieldname": "eosb_accounts_section",
					"fieldtype": "Section Break",
					"label": "EOSB Provision Accounts",
					"insert_after": "accounts",
					"collapsible": 0,
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
