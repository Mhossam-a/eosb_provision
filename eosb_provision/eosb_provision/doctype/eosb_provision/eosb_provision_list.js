frappe.listview_settings["EOSB Provision"] = {
	add_fields: ["status", "is_opening"],
	get_indicator(doc) {
		const map = {
			Draft: "red",
			Opening: "blue",
			"JE Draft": "orange",
			Posted: "green",
			"No Adjustment": "gray",
			Cancelled: "red",
		};
		return [__(doc.status), map[doc.status] || "gray", "status,=," + doc.status];
	},
};
