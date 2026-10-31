// Change this to your app's python package name
const APP = "task_manager_dev";
const WEBHOOK_METHOD = `${APP}.api.git_webhook`;
const IMPORT_METHOD = `${APP}.api.import_local_repo`;

frappe.ui.form.on("Dev Git Tool", {
	onload() {
		// Fired by the background job when a large import finishes
		frappe.realtime.off("dev_git_import_done");
		frappe.realtime.on("dev_git_import_done", (data) => {
			frappe.show_alert({
				message: __("Import finished: {0} commit(s) added to {1}", [data.added, data.name]),
				indicator: "green",
			});
			if (cur_frm && cur_frm.doctype === "Dev Git Tool" && cur_frm.doc.name === data.name) {
				cur_frm.reload_doc();
			}
		});

		// Fired by the background job when a large re-scan finishes
		frappe.realtime.off("dev_git_rescan_done");
		frappe.realtime.on("dev_git_rescan_done", (data) => {
			frappe.show_alert({ message: rescan_message(data), indicator: "green" }, 8);
			if (cur_frm && cur_frm.doctype === "Dev Git Tool" && cur_frm.doc.name === data.name) {
				cur_frm.reload_doc();
			}
		});
	},

	refresh(frm) {
		render_webhook_help(frm);

		frm.add_custom_button(__("Import from Local Repo"), () => open_import_dialog(frm)).addClass(
			frm.is_new() ? "btn-primary" : ""
		);

		if (frm.is_new()) return;

		if (frm.doc.enabled) {
			frm.add_custom_button(__("Sync Commits"), () => {
				frm.call({
					method: "sync_commits",
					doc: frm.doc,
					freeze: true,
					freeze_message: __("Fetching commits..."),
				}).then((r) => {
					frappe.show_alert({
						message: __("{0} new commit(s) imported", [r.message || 0]),
						indicator: "green",
					});
					frm.reload_doc();
				});
			});
		}

		if (frm.doc.total_commits > 0) {
			frm.add_custom_button(__("View Commits"), () => {
				frappe.set_route("Form", "Dev Git Commit", frm.doc.name);
			});

			frm.add_custom_button(
				__("Auto-Link Commits to Tasks"),
				() => {
					if (frm.is_dirty()) {
						frappe.msgprint(__("Please save the repository first."));
						return;
					}
					frm.call({
						method: "rescan_links",
						doc: frm.doc,
						freeze: true,
						freeze_message: __("Matching commits to tasks..."),
					}).then((r) => {
						const res = r.message || {};
						if (res.queued) {
							frappe.show_alert(
								{
									message: __("{0} commits are being matched in the background.", [res.count]),
									indicator: "blue",
								},
								8
							);
						} else {
							frappe.show_alert({ message: rescan_message(res), indicator: "green" }, 8);
							frm.reload_doc();
						}
					});
				},
				__("Tasks")
			);

			frm.add_custom_button(
				__("Review Suggestions"),
				() => frappe.set_route("List", "Dev Commit Suggestion", { repo: frm.doc.name, status: "Pending" }),
				__("Tasks")
			);
		}

		if (frm.doc.project) {
			frm.add_custom_button(__("Open Project"), () => {
				frappe.set_route("Form", "Dev Project", frm.doc.project);
			});
		}
	},

	provider(frm) {
		render_webhook_help(frm);
	},
});

function rescan_message(res) {
	return __("Linked by ID: {0}, auto-linked: {1}, suggested for confirmation: {2}", [
		res.explicit || 0,
		res.auto || 0,
		res.suggested || 0,
	]);
}

function open_import_dialog(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Import from Local Git Repository"),
		fields: [
			{
				fieldname: "path",
				fieldtype: "Data",
				label: __("Repository Folder Path"),
				reqd: 1,
				default: frm.doc.local_path || "",
				description: __(
					"Paste the folder path in any format, e.g. V:\\KB REPO\\my-repo or /home/you/my-repo. Windows drive paths are converted automatically. The folder must contain .git"
				),
			},
			{
				fieldtype: "HTML",
				options: `<div class="text-muted small">${__(
					"This will read the repo name, remote URL, branches, contributors and commits, create a Dev Project named after the repo, and fill this form."
				)}</div>`,
			},
		],
		primary_action_label: __("Fetch & Import"),
		primary_action(values) {
			d.hide();
			frappe.call({
				method: IMPORT_METHOD,
				args: { path: values.path, tool: frm.is_new() ? null : frm.doc.name },
				freeze: true,
				freeze_message: __("Reading repository and importing commits..."),
				callback(r) {
					const res = r.message;
					if (!res) return;

					const msg = res.queued
						? __("{0} commits are importing in the background. This page refreshes when done.", [res.commit_count])
						: __("Imported {0} commit(s) from {1} branch(es) and {2} contributor(s). Project: {3}", [
								res.added,
								res.branches,
								res.contributors,
								res.project,
						  ]);
					frappe.show_alert({ message: msg, indicator: "green" }, 8);

					if (frm.is_new() || frm.doc.name !== res.name) {
						frappe.set_route("Form", "Dev Git Tool", res.name);
					} else {
						frm.reload_doc();
					}
				},
			});
		},
	});
	d.show();
}

// Test Commit here 
// again testing 

function render_webhook_help(frm) {
	const wrapper = frm.fields_dict.webhook_html && frm.fields_dict.webhook_html.$wrapper;
	if (!wrapper) return;

	if (frm.is_new() || frm.doc.provider === "Local Repository") {
		wrapper.html("");
		return;
	}

	const url = `${frappe.urllib.get_base_url()}/api/method/${WEBHOOK_METHOD}?repo=${encodeURIComponent(
		frm.doc.name
	)}`;

	wrapper.html(`
		<div class="text-muted small" style="margin-top: 8px;">
			<p><b>${__("Webhook setup (optional, for live updates)")}</b></p>
			<p>${__("Payload URL")}: <code>${frappe.utils.escape_html(url)}</code></p>
			<p>${__("Content type: application/json. Event: push only. Use the secret above.")}</p>
		</div>
	`);
}