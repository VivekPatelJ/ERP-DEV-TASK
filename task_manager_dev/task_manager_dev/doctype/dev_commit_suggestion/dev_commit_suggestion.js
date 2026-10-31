const LINKER = "task_manager_dev.linker";

frappe.ui.form.on("Dev Commit Suggestion", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.status !== "Pending") return;

		frm.add_custom_button(__("Link to Task"), () => {
			frappe.call({ method: `${LINKER}.accept_suggestion`, args: { name: frm.doc.name }, freeze: true }).then(() => {
				frappe.show_alert({ message: __("Commit linked to {0}", [frm.doc.task]), indicator: "green" });
				frm.reload_doc();
			});
		}).addClass("btn-primary");

		frm.add_custom_button(__("Dismiss"), () => {
			frappe.call({ method: `${LINKER}.reject_suggestion`, args: { name: frm.doc.name }, freeze: true }).then(() => frm.reload_doc());
		});
	},
});