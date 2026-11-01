frappe.ui.form.on("Dev Release", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(__("Generate Release Notes"), () => {
			frappe.prompt(
				[{ fieldname: "use_llm", fieldtype: "Check", label: __("Polish wording with Ollama (AI)") }],
				(v) => {
					frappe
						.call({
							method: "task_manager_dev.release_notes.generate_notes",
							args: { release: frm.doc.name, use_llm: v.use_llm ? 1 : 0 },
							freeze: true,
							freeze_message: __("Generating release notes..."),
						})
						.then((r) => {
							const note = r.message && r.message.note;
							frappe.show_alert({ message: note || __("Release notes generated"), indicator: note ? "orange" : "green" }, 8);
							frm.reload_doc();
						});
				},
				__("Release Notes"),
				__("Generate")
			);
		});

		frm.add_custom_button(__("Download PDF"), () => {
			window.open(
				`/api/method/task_manager_dev.release_notes.download_pdf?release=${encodeURIComponent(frm.doc.name)}`
			);
		});
	},
});