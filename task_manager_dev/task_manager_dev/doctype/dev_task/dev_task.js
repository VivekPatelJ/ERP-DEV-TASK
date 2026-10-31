const APP = "task_manager_dev";
const LINKER = `${APP}.linker`;

frappe.ui.form.on("Dev Task", {
	refresh(frm) {
		frm.set_intro("");
		if (frm.is_new()) return;

		show_review_banner(frm);
		render_suggestions(frm);

		frm.add_custom_button(__("Link Commit"), () => open_link_dialog(frm), __("Git"));

		if (frm.doc.review_status === "Pending Review" && can_review(frm)) {
			frm.add_custom_button(__("Approved"), () => review(frm, "Approved")).addClass("btn-primary");
			frm.add_custom_button(__("Denied"), () => review(frm, "Denied")).addClass("btn-danger");
		}
	},
});

function can_review(frm) {
	return frappe.session.user === frm.doc.reviewer || frappe.user.has_role(["System Manager", "Projects Manager"]);
}

function show_review_banner(frm) {
	const s = frm.doc.review_status;
	if (s === "Pending Review") {
		frm.set_intro(__("Waiting for review by {0}.", [frm.doc.reviewer || "-"]), "orange");
	} else if (s === "Denied") {
		frm.set_intro(
			__("Review denied: {0}. Link a new commit to send it for review again.", [
				frappe.utils.escape_html(frm.doc.review_comment || "-"),
			]),
			"red"
		);
	} else if (s === "Approved") {
		frm.set_intro(__("Approved by {0}.", [frm.doc.reviewed_by]), "green");
	}
}

function review(frm, decision) {
	if (frm.is_dirty()) {
		frappe.msgprint(__("Please save the task first."));
		return;
	}
	const send = (comment) =>
		frm
			.call({ method: "review", doc: frm.doc, args: { decision, comment }, freeze: true })
			.then(() => frm.reload_doc());

	if (decision === "Approved") {
		frappe.confirm(__("Approve this task and mark it as Done?"), () => send(""));
	} else {
		frappe.prompt(
			[{ fieldname: "comment", fieldtype: "Small Text", label: __("Reason for denial"), reqd: 1 }],
			(v) => send(v.comment),
			__("Deny Task"),
			__("Deny")
		);
	}
}

function render_suggestions(frm) {
	const field = frm.fields_dict.suggestions_html;
	if (!field) return;
	const $w = field.$wrapper;

	frappe.call({ method: `${LINKER}.get_suggestions`, args: { task: frm.doc.name } }).then((r) => {
		const rows = r.message || [];
		if (!rows.length) {
			$w.html(`<div class="text-muted small">${__("No suggested commits waiting for confirmation.")}</div>`);
			return;
		}
		const esc = frappe.utils.escape_html;
		const body = rows
			.map(
				(s) => `<tr>
				<td>${s.commit_url ? `<a href="${esc(s.commit_url)}" target="_blank">${esc(s.short_sha)}</a>` : esc(s.short_sha)}</td>
				<td>${esc(s.commit_summary || "")}<div class="text-muted small">${esc(s.reasons || "")}</div></td>
				<td>${esc(s.author_name || "")}</td>
				<td>${Math.round(s.score || 0)}%</td>
				<td style="white-space:nowrap">
					<button class="btn btn-xs btn-primary sg-accept" data-name="${esc(s.name)}">${__("Link")}</button>
					<button class="btn btn-xs btn-default sg-reject" data-name="${esc(s.name)}">${__("Dismiss")}</button>
				</td></tr>`
			)
			.join("");

		$w.html(`
			<p><b>${__("Suggested commits (please confirm)")}</b></p>
			<table class="table table-bordered table-sm">
				<thead><tr><th>${__("Commit")}</th><th>${__("Summary / why")}</th><th>${__("Author")}</th><th>${__("Match")}</th><th></th></tr></thead>
				<tbody>${body}</tbody>
			</table>`);

		const act = (method) =>
			function () {
				frappe.call({ method: `${LINKER}.${method}`, args: { name: $(this).data("name") }, freeze: true }).then(() => frm.reload_doc());
			};
		$w.find(".sg-accept").on("click", act("accept_suggestion"));
		$w.find(".sg-reject").on("click", act("reject_suggestion"));
	});
}

function open_link_dialog(frm) {
	frappe.call({ method: `${LINKER}.get_linkable_commits`, args: { task: frm.doc.name } }).then((r) => {
		const options = (r.message || []).map((c) => ({
			label: `${c.short_sha}  ${c.summary || ""}  (${frappe.datetime.str_to_user(c.committed_on)})`,
			value: c.sha,
		}));

		const fields = [];
		if (options.length) {
			fields.push({ fieldname: "picked", fieldtype: "MultiCheck", label: __("Recent unlinked commits"), options, columns: 1 });
		}
		fields.push({
			fieldname: "pasted",
			fieldtype: "Small Text",
			label: __("Or paste commit SHAs / commit URLs (one per line)"),
		});

		const d = new frappe.ui.Dialog({
			title: __("Link commits to {0}", [frm.doc.name]),
			fields,
			primary_action_label: __("Link"),
			primary_action(v) {
				let picked = v.picked || [];
				if (typeof picked === "string") picked = picked.split(",");
				const commits = [...picked, ...(v.pasted || "").split(/\s+/).filter(Boolean)];
				if (!commits.length) {
					frappe.msgprint(__("Select or paste at least one commit."));
					return;
				}
				frappe.call({ method: `${LINKER}.link_commits_manually`, args: { task: frm.doc.name, commits }, freeze: true }).then((res) => {
					d.hide();
					const m = res.message || {};
					let msg = __("{0} commit(s) linked.", [m.linked || 0]);
					if ((m.not_found || []).length) msg += " " + __("Not found: {0}", [m.not_found.join(", ")]);
					frappe.show_alert({ message: msg, indicator: m.linked ? "green" : "orange" }, 7);
					frm.reload_doc();
				});
			},
		});
		d.show();
	});
}