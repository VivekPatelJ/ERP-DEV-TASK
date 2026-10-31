from datetime import datetime

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_datetime, getdate, now_datetime

REVIEW_ROLES = {"System Manager", "Projects Manager"}


def _row_time(row):
	return get_datetime(row.committed_on) if row.committed_on else datetime.min


class DevTask(Document):
	# ------------------------------------------------------------------
	# Validation
	# ------------------------------------------------------------------
	def validate(self):
		if self.start_date and self.due_date and getdate(self.due_date) < getdate(self.start_date):
			frappe.throw(_("Due Date cannot be before Start Date."))
		if self.reviewer and self.reviewer == self.assignee:
			frappe.throw(_("The reviewer must be a different person than the assignee."))

		self._sync_git_summary()
		self._sync_review_state()
		self._guard_completion()

		if self.status == "Done":
			self.completed_on = self.completed_on or now_datetime()
		else:
			self.completed_on = None

	def _sync_git_summary(self):
		rows = self.get("task_commits") or []
		rows.sort(key=_row_time, reverse=True)  # newest first
		for i, r in enumerate(rows, start=1):
			r.idx = i
		self.commit_count = len(rows)
		self.last_commit_sha = rows[0].commit_sha if rows else None
		self.last_commit_on = rows[0].committed_on if rows else None

	def _sync_review_state(self):
		open_task = self.status not in ("Done", "Cancelled")
		if self.review_status == "Pending Review" and (not self.reviewer or not self.task_commits):
			self.review_status = "Not Requested"
		elif self.reviewer and self.task_commits and open_task and self.review_status in (None, "", "Not Requested"):
			self.review_status = "Pending Review"  # reviewer added after commits were linked

	def _guard_completion(self):
		if not self.reviewer or self.status != "Done" or self.flags.via_review:
			return
		before = self.get_doc_before_save()
		if not before or before.status != "Done":
			frappe.throw(_("This task has a reviewer. It can only be completed when the reviewer clicks Approve."))

	def on_update(self):
		before = self.get_doc_before_save()
		if not before:
			return
		still = {r.commit_sha for r in self.task_commits}
		removed = [(r.repo, r.commit_sha) for r in before.task_commits if r.commit_sha not in still]
		if removed:
			from task_manager_dev.linker import unlink_commits

			unlink_commits(self.name, removed)

	# ------------------------------------------------------------------
	# Commit linking (called by task_manager_dev.linker)
	# ------------------------------------------------------------------
	def add_commit_link(self, c, method, score=None):
		"""Add a commit row unless this SHA is already on the task. Returns True if added."""
		if any(r.commit_sha == c["sha"] for r in self.task_commits):
			return False

		self.append(
			"task_commits",
			{
				"commit_sha": c["sha"],
				"short_sha": c.get("short_sha") or c["sha"][:7],
				"summary": c.get("summary"),
				"commit_url": c.get("commit_url"),
				"committed_on": c.get("committed_on"),
				"author_name": c.get("author_name"),
				"branch": c.get("branch"),
				"repo": c.get("repo"),
				"link_method": method,
				"score": score,
			},
		)

		if self.status in ("Backlog", "To Do"):
			self.status = "In Progress"
		if self.reviewer and self.status not in ("Done", "Cancelled") and self.review_status in (
			None, "", "Not Requested", "Denied",
		):  # fmt: skip
			self.review_status = "Pending Review"
		return True

	# ------------------------------------------------------------------
	# Review
	# ------------------------------------------------------------------
	@frappe.whitelist()
	def review(self, decision, comment=None):
		self.reload()  # never trust reviewer / status sent by the browser
		user = frappe.session.user

		if decision not in ("Approved", "Denied"):
			frappe.throw(_("Invalid decision."))
		if self.review_status != "Pending Review":
			frappe.throw(_("This task is not waiting for review."))
		if user != self.reviewer and not (REVIEW_ROLES & set(frappe.get_roles(user))):
			frappe.throw(_("Only the reviewer can approve or deny this task."), frappe.PermissionError)
		comment = (comment or "").strip()
		if decision == "Denied" and not comment:
			frappe.throw(_("Please give a reason when denying a task."))

		self.review_status = decision
		self.reviewed_by = user
		self.reviewed_on = now_datetime()
		self.review_comment = comment or None

		if decision == "Approved":
			self.flags.via_review = True
			self.status = "Done"
			self.completed_on = now_datetime()

		self.flags.ignore_permissions = True
		self.save()
		self.add_comment("Comment", _("Review {0}: {1}").format(decision.lower(), comment or "-"))
		return self.review_status