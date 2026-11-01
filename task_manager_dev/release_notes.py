import re

import frappe
import requests
from frappe import _
from frappe.utils import get_datetime, md_to_html
from frappe.utils.pdf import get_pdf

TYPE_HEADINGS = [
	("Feature", "New Features"),
	("Bug", "Bug Fixes"),
	("Hotfix", "Hotfixes"),
	("Refactor", "Improvements"),
	("Chore", "Maintenance"),
	("Docs", "Documentation"),
	("Test", "Tests"),
]

SYSTEM_PROMPT = (
	"You edit software release notes. Rewrite each bullet in clear, user-friendly language. "
	"Rules: keep every heading and every task ID (like TASK-00012) exactly as given; keep the same "
	"bullets in the same order; do not add, remove or merge items; do not invent features, numbers "
	"or details; output Markdown only, with no commentary."
)


# ----------------------------------------------------------------------
# 1) Build the plain Markdown from Done tasks
# ----------------------------------------------------------------------
def build_markdown(release):
	tasks = frappe.get_all(
		"Dev Task",
		filters={"release": release, "status": "Done"},
		fields=["name", "subject", "type", "release_note", "assignee", "commit_count"],
		order_by="completed_on asc",
		limit_page_length=0,
	)
	if not tasks:
		frappe.throw(_("No completed tasks are linked to release {0}.").format(release))

	by_type = {}
	for t in tasks:
		by_type.setdefault(t.type or "Chore", []).append(t)

	lines = [f"# Release {release}", ""]
	for type_, heading in TYPE_HEADINGS:
		items = by_type.get(type_)
		if not items:
			continue
		lines.append(f"## {heading}")
		for t in items:
			text = (t.release_note or t.subject or "").strip()
			lines.append(f"- {text} ({t.name})")
		lines.append("")
	return "\n".join(lines).strip() + "\n"


# ----------------------------------------------------------------------
# 2) Optional polish with Ollama (falls back to the plain version)
# ----------------------------------------------------------------------
def polish_with_ollama(markdown):
	"""Returns (markdown, note). note is None on success, else why the plain version was kept."""
	url = (frappe.conf.get("ollama_url") or "http://localhost:11434").rstrip("/")
	model = frappe.conf.get("ollama_model") or "llama3.2"

	try:
		r = requests.post(
			f"{url}/api/chat",
			json={
				"model": model,
				"stream": False,
				"options": {"temperature": 0.2},
				"messages": [
					{"role": "system", "content": SYSTEM_PROMPT},
					{"role": "user", "content": markdown},
				],
			},
			timeout=240,
		)
		r.raise_for_status()
		text = (r.json().get("message") or {}).get("content", "").strip()
	except (requests.RequestException, ValueError) as e:
		return markdown, _("Ollama not reachable ({0}). Plain notes were used.").format(str(e)[:120])

	text = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", text).strip()
	ids = set(re.findall(r"TASK-\d+", markdown))
	if not text or not ids <= set(re.findall(r"TASK-\d+", text)):
		return markdown, _("The model dropped some tasks, so plain notes were used.")
	return text + "\n", None


# ----------------------------------------------------------------------
# 3) Whitelisted API
# ----------------------------------------------------------------------
@frappe.whitelist()
def generate_notes(release, use_llm=0):
	frappe.has_permission("Dev Release", "write", doc=release, throw=True)
	md = build_markdown(release)
	note = None
	if frappe.utils.cint(use_llm):
		md, note = polish_with_ollama(md)
	frappe.db.set_value("Dev Release", release, "release_notes", md)
	return {"markdown": md, "note": note}


@frappe.whitelist()
def download_pdf(release):
	frappe.has_permission("Dev Release", "read", doc=release, throw=True)
	md = frappe.db.get_value("Dev Release", release, "release_notes") or build_markdown(release)

	html = f"""<html><head><meta charset="utf-8"><style>
		body {{ font-family: Helvetica, Arial, sans-serif; font-size: 12px; color: #1f272e; line-height: 1.5; }}
		h1 {{ font-size: 24px; border-bottom: 2px solid #1f272e; padding-bottom: 6px; }}
		h2 {{ font-size: 16px; margin-top: 22px; color: #2c5282; }}
		li {{ margin-bottom: 4px; }}
		.foot {{ margin-top: 30px; font-size: 10px; color: #888; }}
	</style></head><body>
		{md_to_html(md)}
		<div class="foot">Generated on {get_datetime().strftime('%d %b %Y')}</div>
	</body></html>"""

	frappe.local.response.filename = f"Release-{release}.pdf"
	frappe.local.response.filecontent = get_pdf(html)
	frappe.local.response.type = "pdf"