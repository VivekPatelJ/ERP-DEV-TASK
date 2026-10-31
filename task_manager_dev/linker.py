import json
import re
from collections import defaultdict
from datetime import datetime

import frappe
from frappe import _
from frappe.utils import cint, date_diff, flt, getdate, strip_html

DEFAULT_AUTO = 75  # score (0-100) needed to auto-link
DEFAULT_SUGGEST = 35  # score needed to suggest
AUTO_MARGIN = 15  # winner must beat the runner-up by this much to auto-link
MAX_SUGGESTIONS = 3  # per commit
MAX_FILES_CHARS = 4000
FILE_BATCH = 100

TASK_RE = re.compile(r"\btask[\s_#-]*0*(\d{1,6})\b", re.I)
ISSUE_RE = re.compile(r"\b(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?|refs?)\s*:?\s*#0*(\d{1,6})\b", re.I)

STOPWORDS = set(
	"""the and for with from this that into over under are was were will can has have had not but you your our
add added adds fix fixes fixed update updated updates change changed changes remove removed use used using make made
new task tasks issue bug feature work wip merge merged branch commit commits pull request src app apps lib dist build
file files code test tests init index main json html css txt readme package lock""".split()
)


# ----------------------------------------------------------------------
# Task ID extraction
# ----------------------------------------------------------------------
def task_name(n):
	return f"TASK-{int(n):05d}"


def explicit_ids(message, branch=None):
	"""Returns (strong, weak) sets of task names. weak = '#12' style (could be a GitHub issue)."""
	text = f"{message or ''}\n{branch or ''}"
	strong = {task_name(m) for m in TASK_RE.findall(text)}
	weak = {task_name(m) for m in ISSUE_RE.findall(message or "")}
	return strong, weak


# ----------------------------------------------------------------------
# Keyword scoring
# ----------------------------------------------------------------------
def _stem(w):
	for suf in ("ing", "ed", "es", "s"):
		if len(w) > len(suf) + 3 and w.endswith(suf):
			return w[: -len(suf)]
	return w


def tokenize(text, extra_stop=frozenset()):
	text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text or "")  # camelCase -> camel Case
	out = set()
	for w in re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", text):
		w = w.lower()
		if w in STOPWORDS or w in extra_stop:
			continue
		out.add(_stem(w))
	return out


def load_profiles(project, extra_stop):
	filters = {"status": ["not in", ["Done", "Cancelled"]]}
	if project:
		filters["project"] = project
	tasks = frappe.get_all(
		"Dev Task",
		filters=filters,
		fields=["name", "subject", "description", "start_date", "due_date", "assignee", "branch_name"],
		limit_page_length=0,
	)
	profiles = {}
	for t in tasks:
		subject = tokenize(t.subject, extra_stop)
		profiles[t.name] = {
			"subject": subject,
			"desc": tokenize(strip_html(t.description or ""), extra_stop) - subject,
			"start": getdate(t.start_date) if t.start_date else None,
			"due": getdate(t.due_date) if t.due_date else None,
			"assignee": (t.assignee or "").lower(),
			"branch": (t.branch_name or "").strip().lower(),
		}
	return profiles


def score_commit(rec, p, msg_tokens, file_tokens):
	"""Returns (score 0-100, [reasons])."""
	all_tokens = msg_tokens | file_tokens
	m_subj = p["subject"] & all_tokens
	m_desc = p["desc"] & all_tokens
	branch_hit = bool(p["branch"]) and (rec.get("branch") or "").strip().lower() == p["branch"]

	if len(m_subj) + len(m_desc) < 2 and not branch_hit:
		return 0, []

	reasons, score = [], 0.0
	if p["subject"]:
		score += 60 * len(m_subj) / len(p["subject"])
	score += 25 * min(len(m_desc) / 4, 1)
	if m_subj & msg_tokens:
		score += 10
	if m_subj:
		reasons.append(_("subject: {0}").format(", ".join(sorted(m_subj))))
	if m_desc:
		reasons.append(_("description: {0}").format(", ".join(sorted(m_desc)[:6])))
	if m_subj & file_tokens or m_desc & file_tokens:
		reasons.append(_("file paths match"))
	if branch_hit:
		score += 35
		reasons.append(_("same branch as task"))
	if p["assignee"] and p["assignee"] == (rec.get("author_email") or "").lower():
		score += 10
		reasons.append(_("author is the assignee"))

	when = rec.get("committed_on")
	if when and (p["start"] or p["due"]):
		day = when.date() if isinstance(when, datetime) else getdate(when)
		early = p["start"] and date_diff(p["start"], day) > 2
		late = p["due"] and date_diff(day, p["due"]) > 14
		if early or late:
			score -= 15
			reasons.append(_("outside task dates"))
		else:
			score += 5
			reasons.append(_("within task dates"))

	return max(0, min(100, round(score))), reasons


# ----------------------------------------------------------------------
# Pipeline
# ----------------------------------------------------------------------
def process_commits(tool, records):
	"""
	records: [{name, sha, short_sha, summary, message, branch, author_name, author_email,
	           committed_on, commit_url, files, repo, task?}]
	Links by explicit ID, then by keywords (auto-link or suggest). Returns counts.
	"""
	stats = {"explicit": 0, "auto": 0, "suggested": 0}
	if not records:
		return stats

	rejected = _rejected_pairs(tool.name)
	links = defaultdict(list)  # task -> [(rec, method, score)]
	pending = []

	# 1) explicit task IDs
	found = {rec["name"]: explicit_ids(rec.get("message"), rec.get("branch")) for rec in records}
	wanted = set()
	for strong, weak in found.values():
		wanted |= strong | weak
	projects = {}
	if wanted:
		projects = {
			t.name: t.project
			for t in frappe.get_all(
				"Dev Task", filters={"name": ["in", list(wanted)]}, fields=["name", "project"]
			)
		}

	for rec in records:
		strong, weak = found[rec["name"]]
		hits = [t for t in sorted(strong) if t in projects]
		hits += [
			t for t in sorted(weak - strong) if projects.get(t) and projects[t] == tool.project
		]
		hits = [t for t in hits if (rec["sha"], t) not in rejected]
		if hits:
			for t in hits:
				links[t].append((rec, "Explicit", 100))
			stats["explicit"] += 1
		else:
			pending.append(rec)

	# 2) keyword matching for the rest
	suggested = set()
	if pending and not cint(tool.get("disable_keyword_matching")):
		auto_thr = flt(tool.get("auto_link_threshold")) or DEFAULT_AUTO
		sug_thr = flt(tool.get("suggest_threshold")) or DEFAULT_SUGGEST
		extra = tokenize(tool.name)
		profiles = load_profiles(tool.project, extra)
		if profiles:
			fill_files(tool, pending)
			for rec in pending:
				tm = tokenize(rec.get("message") or rec.get("summary"), extra)
				tf = tokenize(rec.get("files"), extra)
				scored = []
				for tname, p in profiles.items():
					if (rec["sha"], tname) in rejected:
						continue
					s, why = score_commit(rec, p, tm, tf)
					if s >= sug_thr:
						scored.append((s, tname, why))
				if not scored:
					continue
				scored.sort(key=lambda x: -x[0])
				runner = scored[1][0] if len(scored) > 1 else 0
				if scored[0][0] >= auto_thr and scored[0][0] - runner >= AUTO_MARGIN:
					links[scored[0][1]].append((rec, "Auto", scored[0][0]))
					stats["auto"] += 1
				else:
					for s, tname, why in scored[:MAX_SUGGESTIONS]:
						_upsert_suggestion(tool, rec, tname, s, why)
					suggested.add(rec["name"])
					stats["suggested"] += 1

	# 3) write everything
	done, _added = _apply_links(links)

	updates = {}
	for rec in pending:
		vals = {}
		if rec.get("_files_filled"):
			vals["files_changed"] = rec.get("files") or ""
		if rec["name"] in suggested and rec["name"] not in done:
			vals["link_status"] = "Suggested"
		if vals:
			updates[rec["name"]] = vals
	_bulk_item_update(updates)
	return stats


def _apply_links(links):
	"""links: {task: [(rec, method, score)]}. Returns ({item_name: (task, method, score)}, newly_added_count)."""
	done, added_total = {}, 0
	for task, entries in links.items():
		try:
			doc = frappe.get_doc("Dev Task", task)
			ok = []
			for rec, method, score in entries:
				if doc.add_commit_link(rec, method, score):
					added_total += 1
				ok.append((rec, method, score))
			doc.flags.ignore_permissions = True
			doc.save()
			for rec, method, score in ok:
				done.setdefault(rec["name"], (task, method, score, rec))
		except Exception:
			frappe.log_error(title=f"Dev Git: could not link commits to {task}")

	updates = {}
	for item, (task, method, score, rec) in done.items():
		vals = {"link_status": "Linked", "link_method": method, "match_score": score}
		if not rec.get("task"):
			vals["task"] = task
		if rec.get("_files_filled"):
			vals["files_changed"] = rec.get("files") or ""
		updates[item] = vals
	_bulk_item_update(updates)
	return {k: v[:3] for k, v in done.items()}, added_total


def _bulk_item_update(updates):
	if not updates:
		return
	try:
		frappe.db.bulk_update("Dev Git Commit Item", updates, update_modified=False)
	except Exception:
		for name, vals in updates.items():
			frappe.db.set_value("Dev Git Commit Item", name, vals, update_modified=False)


def _rejected_pairs(repo):
	rows = frappe.get_all(
		"Dev Commit Suggestion",
		filters={"repo": repo, "status": "Rejected"},
		fields=["commit_sha", "task"],
		limit_page_length=0,
	)
	return {(r.commit_sha, r.task) for r in rows}


def _upsert_suggestion(tool, rec, task, score, reasons):
	key = {"repo": tool.name, "commit_sha": rec["sha"], "task": task}
	existing = frappe.db.get_value("Dev Commit Suggestion", key, ["name", "status"], as_dict=True)
	text = "; ".join(reasons)[:1000]
	if existing:
		if existing.status == "Pending":
			frappe.db.set_value(
				"Dev Commit Suggestion", existing.name, {"score": score, "reasons": text}, update_modified=False
			)
		return
	frappe.get_doc(
		{
			"doctype": "Dev Commit Suggestion",
			**key,
			"short_sha": rec.get("short_sha"),
			"commit_summary": rec.get("summary"),
			"commit_url": rec.get("commit_url"),
			"committed_on": rec.get("committed_on"),
			"author_name": rec.get("author_name"),
			"score": score,
			"reasons": text,
			"status": "Pending",
		}
	).insert(ignore_permissions=True)


def fill_files(tool, recs):
	"""Local repos only: read changed file paths in batches (webhook/API commits supply them themselves)."""
	if tool.provider != "Local Repository" or not tool.local_path:
		return
	from task_manager_dev.task_manager_dev.doctype.dev_git_tool.dev_git_tool import _git

	need = [r for r in recs if not r.get("files")]
	for i in range(0, len(need), FILE_BATCH):
		chunk = need[i : i + FILE_BATCH]
		out = _git(
			tool.local_path,
			"show",
			"--name-only",
			"--no-renames",
			"--pretty=format:%x1e%H",
			*[r["sha"] for r in chunk],
			"--",
			check=False,
			timeout=600,
		)
		by_sha = {}
		for block in out.split("\x1e"):
			lines = block.strip().splitlines()
			if lines:
				by_sha[lines[0].strip()] = lines[1:]
		for r in chunk:
			r["files"] = "\n".join(by_sha.get(r["sha"], []))[:MAX_FILES_CHARS]
			r["_files_filled"] = True


# ----------------------------------------------------------------------
# Re-scan existing commits (backfill / after tasks change)
# ----------------------------------------------------------------------
_RESCAN_WHERE = (
	"parent = %s and parenttype = 'Dev Git Commit' and ifnull(link_status, '') in ('', 'Unlinked', 'Suggested')"
)


def unlinked_count(repo):
	return cint(frappe.db.sql(f"select count(*) from `tabDev Git Commit Item` where {_RESCAN_WHERE}", repo)[0][0])


def rescan(tool):
	rows = frappe.db.sql(
		f"""select name, sha, short_sha, summary, message, branch, author_name, author_email,
		committed_on, commit_url, files_changed as files, task, parent as repo
		from `tabDev Git Commit Item` where {_RESCAN_WHERE} order by committed_on desc""",
		tool.name,
		as_dict=True,
	)
	return process_commits(tool, rows)


# ----------------------------------------------------------------------
# Unlink (called by Dev Task when a commit row is deleted)
# ----------------------------------------------------------------------
def unlink_commits(task, pairs):
	for repo, sha in pairs:
		if not repo:
			continue
		frappe.db.set_value(
			"Dev Git Commit Item",
			{"parent": repo, "parenttype": "Dev Git Commit", "sha": sha, "task": task},
			{"task": None, "link_status": "Unlinked", "link_method": None, "match_score": 0},
			update_modified=False,
		)
		key = {"repo": repo, "commit_sha": sha, "task": task}
		existing = frappe.db.get_value("Dev Commit Suggestion", key)
		if existing:
			frappe.db.set_value("Dev Commit Suggestion", existing, "status", "Rejected", update_modified=False)
		else:
			frappe.get_doc({"doctype": "Dev Commit Suggestion", **key, "status": "Rejected"}).insert(
				ignore_permissions=True
			)


# ----------------------------------------------------------------------
# Whitelisted API used by the Dev Task / Dev Commit Suggestion forms
# ----------------------------------------------------------------------
ITEM_COLS = """i.name, i.sha, i.short_sha, i.summary, i.message, i.branch, i.author_name, i.author_email,
	i.committed_on, i.commit_url, i.files_changed as files, i.task, i.parent as repo"""


@frappe.whitelist()
def get_suggestions(task):
	frappe.has_permission("Dev Task", "read", doc=task, throw=True)
	return frappe.get_all(
		"Dev Commit Suggestion",
		filters={"task": task, "status": "Pending"},
		fields=["name", "short_sha", "commit_summary", "commit_url", "committed_on", "author_name", "score", "reasons"],
		order_by="score desc, committed_on desc",
		limit_page_length=50,
	)


@frappe.whitelist()
def accept_suggestion(name):
	s = frappe.get_doc("Dev Commit Suggestion", name)
	frappe.has_permission("Dev Task", "write", doc=s.task, throw=True)
	if s.status != "Pending":
		frappe.throw(_("This suggestion was already {0}.").format(s.status))

	rec = frappe.db.sql(
		f"""select {ITEM_COLS} from `tabDev Git Commit Item` i
		where i.parent = %s and i.parenttype = 'Dev Git Commit' and i.sha = %s""",
		(s.repo, s.commit_sha),
		as_dict=True,
	)
	if not rec:
		frappe.throw(_("The commit {0} no longer exists in {1}.").format(s.commit_sha[:7], s.repo))

	_apply_links({s.task: [(rec[0], "Suggested", s.score)]})
	# the commit now belongs to this task: close the other open suggestions for it
	frappe.db.set_value(
		"Dev Commit Suggestion",
		{"repo": s.repo, "commit_sha": s.commit_sha, "status": "Pending"},
		"status",
		"Rejected",
		update_modified=False,
	)
	frappe.db.set_value("Dev Commit Suggestion", s.name, "status", "Accepted")
	return True


@frappe.whitelist()
def reject_suggestion(name):
	s = frappe.get_doc("Dev Commit Suggestion", name)
	frappe.has_permission("Dev Task", "write", doc=s.task, throw=True)
	frappe.db.set_value("Dev Commit Suggestion", s.name, "status", "Rejected")
	still_open = frappe.db.exists(
		"Dev Commit Suggestion", {"repo": s.repo, "commit_sha": s.commit_sha, "status": "Pending"}
	)
	if not still_open:
		frappe.db.set_value(
			"Dev Git Commit Item",
			{"parent": s.repo, "parenttype": "Dev Git Commit", "sha": s.commit_sha, "link_status": "Suggested"},
			"link_status",
			"Unlinked",
			update_modified=False,
		)
	return True


@frappe.whitelist()
def get_linkable_commits(task):
	"""Latest commits of the task's project that are not linked to any task yet."""
	frappe.has_permission("Dev Task", "write", doc=task, throw=True)
	project = frappe.db.get_value("Dev Task", task, "project")
	return frappe.db.sql(
		"""select i.sha, i.short_sha, i.summary, i.committed_on
		from `tabDev Git Commit Item` i join `tabDev Git Commit` c on c.name = i.parent
		where i.parenttype = 'Dev Git Commit' and c.project = %s and ifnull(i.task, '') = ''
		order by i.committed_on desc limit 50""",
		project,
		as_dict=True,
	)


@frappe.whitelist()
def link_commits_manually(task, commits):
	"""commits: list of SHAs, short SHAs or commit URLs."""
	frappe.has_permission("Dev Task", "write", doc=task, throw=True)
	tokens = frappe.parse_json(commits) or []
	if isinstance(tokens, str):
		tokens = [tokens]

	prefixes = []
	for t in tokens:
		prefixes += re.findall(r"\b[0-9a-fA-F]{7,40}\b", str(t))
	prefixes = list(dict.fromkeys(p.lower() for p in prefixes))
	if not prefixes:
		frappe.throw(_("Select a commit or paste at least one commit SHA / URL."))

	project = frappe.db.get_value("Dev Task", task, "project")
	entries, missing = [], []
	for p in prefixes:
		rows = frappe.db.sql(
			f"""select {ITEM_COLS} from `tabDev Git Commit Item` i
			join `tabDev Git Commit` c on c.name = i.parent
			where i.parenttype = 'Dev Git Commit' and c.project = %s and i.sha like %s limit 2""",
			(project, p + "%"),
			as_dict=True,
		)
		if len(rows) == 1:
			entries.append((rows[0], "Manual", 100))
		else:
			missing.append(p)

	_done, added = _apply_links({task: entries}) if entries else ({}, 0)
	return {"linked": added, "not_found": missing}