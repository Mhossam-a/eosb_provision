app_name = "eosb_provision"
app_title = "EOSB Provision"
app_publisher = "Mhossam-a"
app_description = "End of Service Benefit (EOSB) and leave balance provisions for ERPNext / HRMS"
app_email = "mohammedhossam168@gmail.com"
app_license = "mit"

required_apps = ["erpnext", "hrms"]

after_install = "eosb_provision.install.after_install"
after_migrate = "eosb_provision.install.after_install"

doctype_js = {
	"Salary Component": "public/js/salary_component.js",
}

doc_events = {
	"Journal Entry": {
		"on_submit": "eosb_provision.events.journal_entry_on_submit",
		"before_cancel": "eosb_provision.events.journal_entry_before_cancel",
		"on_trash": "eosb_provision.events.journal_entry_on_trash",
	},
}

scheduler_events = {
	"daily": [
		"eosb_provision.eosb_provision.doctype.eosb_provision.eosb_provision.auto_create",
	],
}
