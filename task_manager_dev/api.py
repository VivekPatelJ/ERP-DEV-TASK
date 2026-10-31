import hashlib
import hmac
import json

import frappe
from frappe import _


def _verify(secret, body):
	"""Supports GitHub (HMAC signature) and GitLab (shared token)."""
	signature = frappe.get_request_header("X-Hub-Signature-256")
	gitlab_token = frappe.get_request_header("X-Gitlab-Token")

	if signature:
		expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
		return hmac.compare_digest(signature, expected)
	if gitlab_token:
		return hmac.compare_digest(gitlab_token, secret)
	return False


@frappe.whitelist(allow_guest=True)
def git_webhook(repo):
	"""URL: /api/method/<your_app>.api.git_webhook?repo=<Dev Git Tool name>"""
	if not frappe.db.exists("Dev Git Tool", repo):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	tool = frappe.get_doc("Dev Git Tool", repo)
	secret = tool.get_password("webhook_secret", raise_exception=False)
	body = frappe.request.get_data()

	if not tool.enabled or not secret or not _verify(secret, body):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	event = frappe.get_request_header("X-GitHub-Event") or frappe.get_request_header("X-Gitlab-Event")
	if event not in ("push", "Push Hook"):
		return {"status": "ignored", "event": event}

	payload = json.loads(body)
	ref = payload.get("ref") or ""
	if not ref.startswith("refs/heads/"):  # ignore tags
		return {"status": "ignored", "ref": ref}
	branch = ref[len("refs/heads/") :]

	pairs = []
	for c in payload.get("commits", []):
		author = c.get("author") or {}
		pairs.append(
			(
				branch,
				{
					"sha": c["id"],
					"message": c.get("message"),
					"author_name": author.get("name"),
					"author_email": author.get("email"),
					"committed_on": c.get("timestamp"),
				},
			)
		)

	added = tool.add_commits(pairs)
	return {"status": "ok", "added": added}


@frappe.whitelist()
def import_local_repo(path, tool=None):
	"""
	Read a local git repo and fill/create the Dev Git Tool, its Dev Project and all commits.
	Called by the "Import from Local Repo" button.
	"""
	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only a System Manager can import a local repository."), frappe.PermissionError)

	if tool and frappe.db.exists("Dev Git Tool", tool):
		doc = frappe.get_doc("Dev Git Tool", tool)
	else:
		doc = frappe.new_doc("Dev Git Tool")

	return doc.import_from_local(path)