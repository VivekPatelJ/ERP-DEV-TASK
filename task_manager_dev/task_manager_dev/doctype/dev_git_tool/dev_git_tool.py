import os
import re
import subprocess
from datetime import datetime, timedelta
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

import frappe
import requests
from dateutil import parser as dtparser
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, flt, get_datetime, get_system_timezone, now_datetime

from task_manager_dev import linker

MAX_COMMITS_PER_BRANCH = 100  # for GitHub / GitLab API syncs
MAX_IMPORT_COMMITS = 20000  # safety cap for local imports
INLINE_IMPORT_LIMIT = 500  # above this many commits the import runs in a background job
INLINE_RESCAN_LIMIT = 3000  # above this many commits the re-scan runs in a background job

RECORD_SEP = "\x1e"
FIELD_SEP = "\x1f"
LOG_FORMAT = f"--pretty=format:%H{FIELD_SEP}%an{FIELD_SEP}%ae{FIELD_SEP}%aI{FIELD_SEP}%B{RECORD_SEP}"


# ----------------------------------------------------------------------
# Generic helpers
# ----------------------------------------------------------------------
def to_system_datetime(value):
	"""ISO-8601 string (with timezone) -> naive datetime in the site's timezone."""
	if not value:
		return None
	dt = dtparser.isoparse(value) if isinstance(value, str) else value
	if dt.tzinfo:
		dt = dt.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None)
	return dt


def _git(path, *args, check=True, timeout=120):
	"""Run a git command inside `path` and return stdout. Never uses a shell."""
	try:
		res = subprocess.run(
			["git", "-c", f"safe.directory={path}", "-C", path, *args],
			capture_output=True,
			text=True,
			encoding="utf-8",
			errors="replace",
			timeout=timeout,
		)
	except (OSError, subprocess.SubprocessError) as e:
		frappe.throw(_("Could not run git: {0}").format(str(e)))

	if res.returncode != 0:
		if not check:
			return ""
		err = (res.stderr or "").strip()
		if "dubious ownership" in err:
			err += " | " + _("Fix: run `git config --global --add safe.directory {0}` as the user running bench").format(path)
		frappe.throw(_("Git error: {0}").format(err))
	return res.stdout


def normalize_remote(url):
	"""Turn an origin URL (ssh or https, maybe with credentials) into a clean https URL."""
	url = (url or "").strip()
	if not url:
		return None

	m = re.match(r"^(?:ssh://)?[\w.-]+@([\w.-]+)[:/](.+?)(?:\.git)?/?$", url)
	if m:
		return f"https://{m.group(1)}/{m.group(2)}"

	if url.startswith(("http://", "https://")):
		u = urlparse(url)
		host = u.hostname or ""
		if u.port:
			host += f":{u.port}"
		path = u.path.rstrip("/")
		if path.endswith(".git"):
			path = path[:-4]
		return f"{u.scheme}://{host}{path}"  # userinfo (tokens) intentionally dropped

	return None


def normalize_local_path(path):
	r"""
	Accept whatever the user pastes and return the path as THIS server sees it:
	  V:\KB REPO\KB_Hrms\kb_hrms          -> /mnt/v/KB REPO/KB_Hrms/kb_hrms   (WSL / Linux)
	  V:/KB REPO/KB_Hrms/kb_hrms          -> /mnt/v/KB REPO/KB_Hrms/kb_hrms
	  \\wsl$\Ubuntu\home\me\repo          -> /home/me/repo
	  \\wsl.localhost\Ubuntu\home\me\repo -> /home/me/repo
	  "quoted paths" and ~/repo           -> cleaned / expanded
	Paths that are already Linux paths are returned unchanged.
	"""
	path = (path or "").strip().strip('"').strip("'").strip()
	if not path or os.name == "nt":  # server itself runs on Windows: nothing to convert
		return path

	m = re.match(r"^[\\/]{2}wsl(?:\$|\.localhost)[\\/][^\\/]+[\\/]?(.*)$", path, re.I)
	if m:
		return "/" + m.group(1).replace("\\", "/").strip("/")

	m = re.match(r"^([a-zA-Z]):[\\/]*(.*)$", path)
	if m:
		drive = m.group(1).lower()
		rest = m.group(2).replace("\\", "/").strip("/")
		root = f"/mnt/{drive}"
		if not os.path.isdir(root):
			frappe.throw(
				_("Drive {0}: is not mounted on the server. In WSL run: sudo mkdir -p {1} && sudo mount -t drvfs {0}: {1}").format(
					drive.upper(), root
				)
			)
		return f"{root}/{rest}" if rest else root

	return os.path.expanduser(path)


def list_refs(top, default_branch):
	"""[(display_name, full_ref)] - local branches first, then remote-only ones, default branch first."""
	out = _git(top, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes/origin").splitlines()
	refs = {}
	for ref in out:
		if ref.startswith("refs/heads/"):
			refs[ref[len("refs/heads/") :]] = ref
	for ref in out:
		if ref.startswith("refs/remotes/origin/"):
			name = ref[len("refs/remotes/origin/") :]
			if name != "HEAD":
				refs.setdefault(name, ref)

	ordered = sorted(refs.items(), key=lambda kv: (kv[0] != default_branch, kv[0]))
	if not ordered and _git(top, "rev-parse", "--verify", "HEAD", check=False).strip():
		ordered = [(default_branch or "HEAD", "HEAD")]  # detached HEAD, no branches
	return ordered


def inspect_local_repo(path):
	"""Read everything useful about a local git repository (read-only, no DB writes)."""
	path = normalize_local_path(path)
	if not path or not os.path.isabs(path):
		frappe.throw(_("Please enter the absolute path of the repository folder."))
	if not os.path.isdir(path):
		frappe.throw(_("Folder not found on the server: {0}").format(path))

	top = _git(path, "rev-parse", "--show-toplevel").strip()
	remote_url = normalize_remote(_git(top, "config", "--get", "remote.origin.url", check=False))

	repo_name = os.path.basename(top.rstrip("/"))
	if remote_url:
		repo_name = remote_url.rstrip("/").split("/")[-1] or repo_name

	head = _git(top, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False).strip()
	default_branch = head.split("/", 1)[1] if head.startswith("origin/") else ""
	if not default_branch:
		# no origin/HEAD: prefer main/master if they exist, else whatever is checked out
		local = _git(top, "for-each-ref", "--format=%(refname:short)", "refs/heads").split()
		default_branch = next((b for b in ("main", "master") if b in local), "")
	if not default_branch:
		default_branch = _git(top, "symbolic-ref", "--short", "HEAD", check=False).strip() or "main"

	refs = list_refs(top, default_branch)

	contributors = {}
	for line in _git(top, "shortlog", "-sne", "--all", check=False).splitlines():
		m = re.match(r"\s*(\d+)\s+(.*?)\s*<(.*)>\s*$", line)
		if not m:
			continue
		count, name, email = int(m.group(1)), m.group(2), m.group(3).lower()
		if email in contributors:
			contributors[email]["commits"] += count
		else:
			contributors[email] = {"name": name, "email": email, "commits": count}

	count_out = _git(top, "rev-list", "--all", "--count", check=False).strip()
	roots = _git(top, "log", "--all", "--max-parents=0", "--format=%aI", check=False).split()

	return {
		"top": top,
		"repo_name": repo_name,
		"remote_url": remote_url,
		"default_branch": default_branch,
		"branches": [name for name, _ref in refs],
		"contributors": sorted(contributors.values(), key=lambda c: -c["commits"]),
		"commit_count": int(count_out or 0),
		"first_commit_on": min((to_system_datetime(d) for d in roots), default=None),
	}


def ensure_project(name, start=None):
	"""Create a Dev Project named after the repo if it does not exist yet."""
	if not frappe.db.exists("Dev Project", name):
		frappe.get_doc(
			{
				"doctype": "Dev Project",
				"project_name": name,
				"status": "Active",
				"start_date": start.date() if start else None,
				"description": _("Auto-created from the git repository {0}").format(name),
			}
		).insert(ignore_permissions=True)
	return name


# ----------------------------------------------------------------------
# DocType controller
# ----------------------------------------------------------------------
class DevGitTool(Document):
	# ------------------------------------------------------------------
	# Validation
	# ------------------------------------------------------------------
	def validate(self):
		auto, suggest = flt(self.auto_link_threshold), flt(self.suggest_threshold)
		if auto and suggest and suggest > auto:
			frappe.throw(_("Suggest Threshold cannot be higher than Auto-Link Threshold."))

		if self.provider == "Local Repository":
			self.local_path = normalize_local_path(self.local_path)
			if (self.is_new() or self.has_value_changed("local_path")) and "System Manager" not in frappe.get_roles():
				frappe.throw(_("Only a System Manager can configure a local repository path."))
			if not os.path.exists(os.path.join(self.local_path or "", ".git")):
				frappe.throw(_("{0} is not a git repository (no .git found).").format(self.local_path))
		else:
			self._slug()

	def _slug(self):
		"""owner/repo (GitHub) or group/subgroup/project (GitLab) taken from the repo URL."""
		path = urlparse(self.repo_url or "").path.strip("/")
		if path.endswith(".git"):
			path = path[:-4]
		if "/" not in path:
			frappe.throw(_("Repository URL must look like https://github.com/owner/repo"))
		return path

	# ------------------------------------------------------------------
	# One-click import from a local repository
	# ------------------------------------------------------------------
	def import_from_local(self, path):
		"""Fill this record from a local repo, create its project and import all commits."""
		meta = inspect_local_repo(path)

		if self.is_new():
			existing = frappe.db.exists("Dev Git Tool", meta["repo_name"])
			if existing:  # same repo already registered -> refresh that record instead
				return frappe.get_doc("Dev Git Tool", existing).import_from_local(path)
			self.repo_name = meta["repo_name"]

		self.flags.ignore_permissions = True
		self.provider = "Local Repository"
		self.local_path = meta["top"]
		self.default_branch = meta["default_branch"]
		self.enabled = 1
		if meta["remote_url"]:
			self.repo_url = meta["remote_url"]
		self.project = self.project or ensure_project(self.repo_name, meta["first_commit_on"])
		self.branches_summary = ", ".join(meta["branches"])[:1000]
		self.total_branches = len(meta["branches"])
		self.first_commit_on = meta["first_commit_on"]

		self.set("contributors", [])
		for c in meta["contributors"]:
			self.append(
				"contributors",
				{"contributor_name": c["name"], "email": c["email"], "commits": c["commits"]},
			)
		self.save()

		result = {
			"name": self.name,
			"project": self.project,
			"commit_count": meta["commit_count"],
			"contributors": len(meta["contributors"]),
			"branches": len(meta["branches"]),
			"queued": False,
			"added": 0,
		}

		if meta["commit_count"] > INLINE_IMPORT_LIMIT:
			# needs `bench start` / a running worker
			frappe.enqueue_doc(
				"Dev Git Tool",
				self.name,
				"import_all_commits",
				queue="long",
				timeout=3600,
				enqueue_after_commit=True,
				notify_user=frappe.session.user,
			)
			result["queued"] = True
		else:
			result["added"] = self.import_all_commits()

		return result

	def import_all_commits(self, notify_user=None):
		"""Store every commit of the local repo (skips ones already stored)."""
		added = self.add_commits(list(self._fetch_local(None, MAX_IMPORT_COMMITS)))
		if notify_user:
			frappe.publish_realtime("dev_git_import_done", {"name": self.name, "added": added}, user=notify_user)
		return added

	# ------------------------------------------------------------------
	# Incremental sync (button on the form)
	# ------------------------------------------------------------------
	@frappe.whitelist()
	def sync_commits(self):
		"""Pull new commits from every branch and store them. Returns number added."""
		if not self.enabled:
			frappe.throw(_("This repository is disabled."))

		fetchers = {
			"GitHub": self._fetch_github,
			"GitLab": self._fetch_gitlab,
			"Local Repository": lambda since: self._fetch_local(since, MAX_IMPORT_COMMITS),
		}
		return self.add_commits(list(fetchers[self.provider](self._since())))

	# ------------------------------------------------------------------
	# Re-scan stored commits for task links (backfill / after tasks changed)
	# ------------------------------------------------------------------
	@frappe.whitelist()
	def rescan_links(self):
		count = linker.unlinked_count(self.name)
		if count > INLINE_RESCAN_LIMIT:
			frappe.enqueue_doc(
				"Dev Git Tool",
				self.name,
				"run_rescan",
				queue="long",
				timeout=3600,
				enqueue_after_commit=True,
				notify_user=frappe.session.user,
			)
			return {"queued": True, "count": count}
		return self.run_rescan()

	def run_rescan(self, notify_user=None):
		stats = linker.rescan(self)
		if notify_user:
			frappe.publish_realtime("dev_git_rescan_done", {"name": self.name, **stats}, user=notify_user)
		return stats

	# ------------------------------------------------------------------
	# Commit storage: ONE Dev Git Commit record per repo, commits are child rows
	# ------------------------------------------------------------------
	def _commit_log(self):
		"""The single Dev Git Commit record holding this repo's commits (created on first use)."""
		if not frappe.db.exists("Dev Git Commit", self.name):
			frappe.get_doc(
				{"doctype": "Dev Git Commit", "repo": self.name, "project": self.project}
			).insert(ignore_permissions=True)
		return self.name

	def add_commits(self, pairs):
		"""
		Store [(branch, commit_dict), ...] as child rows of this repo's Dev Git Commit record,
		then link them to tasks. commit_dict may carry an optional "files" (list or newline text).
		Skips commits already stored. Newest commits are shown first. Returns number added.
		Used by sync, local import and the webhook.
		"""
		log = self._commit_log()
		existing = set(
			frappe.db.sql_list(
				"select sha from `tabDev Git Commit Item` where parent = %s and parenttype = 'Dev Git Commit'",
				log,
			)
		)
		fresh_table = not existing

		prepared, seen = [], set()
		for branch, c in pairs:
			sha = c["sha"]
			if sha in existing or sha in seen:
				continue
			seen.add(sha)
			prepared.append((branch, c, to_system_datetime(c.get("committed_on"))))

		records = []
		if prepared:
			prepared.sort(key=lambda r: r[2] or datetime.min, reverse=True)  # newest first (stable)

			fields = [
				"name", "owner", "creation", "modified", "modified_by", "docstatus", "idx",
				"parent", "parentfield", "parenttype",
				"sha", "short_sha", "summary", "message", "author_name", "author_email",
				"branch", "committed_on", "commit_url", "files_changed", "link_status",
			]  # fmt: skip
			now, user = now_datetime(), frappe.session.user
			values = []
			for i, (branch, c, dt) in enumerate(prepared, start=1):
				message = c.get("message") or ""
				url = self._commit_url(c["sha"])
				url = url if url and len(url) <= 140 else None
				summary = message.strip().split("\n")[0][:140]
				files = c.get("files") or ""
				if isinstance(files, (list, tuple)):
					files = "\n".join(files)
				files = files[: linker.MAX_FILES_CHARS]
				name = frappe.generate_hash(length=10)

				values.append(
					(
						name, user, now, now, user, 0,
						i if fresh_table else 0,  # real order is set by _renumber() when rows already exist
						log, "commits", "Dev Git Commit",
						c["sha"], c["sha"][:7], summary, message,
						(c.get("author_name") or "")[:140], (c.get("author_email") or "")[:140],
						(branch or "")[:140], dt, url, files, "Unlinked",
					)
				)  # fmt: skip
				records.append(
					{
						"name": name,
						"sha": c["sha"],
						"short_sha": c["sha"][:7],
						"summary": summary,
						"message": message,
						"branch": branch or "",
						"author_name": c.get("author_name") or "",
						"author_email": c.get("author_email") or "",
						"committed_on": dt,
						"commit_url": url,
						"files": files,
						"repo": self.name,
					}
				)
			frappe.db.bulk_insert("Dev Git Commit Item", fields=fields, values=values)
			if not fresh_table:
				self._renumber(log)

		self.update_stats(synced=True)

		if records:
			try:
				linker.process_commits(self, records)
			except Exception:
				frappe.log_error(title="Dev Git: task linking failed")
		return len(prepared)

	def _renumber(self, log):
		"""Keep the child rows ordered newest first after new commits were added."""
		rows = frappe.db.sql(
			"select name, idx from `tabDev Git Commit Item` "
			"where parent = %s and parenttype = 'Dev Git Commit' "
			"order by committed_on desc, name",
			log,
		)
		updates = {name: {"idx": i} for i, (name, idx) in enumerate(rows, start=1) if idx != i}
		if not updates:
			return
		try:
			frappe.db.bulk_update("Dev Git Commit Item", updates, update_modified=False)
		except Exception:
			frappe.log_error(title="Dev Git Commit: could not re-order rows")

	def update_stats(self, synced=False):
		total, first, last = 0, None, None
		if frappe.db.exists("Dev Git Commit", self.name):
			total, first, last = frappe.db.sql(
				"select count(*), min(committed_on), max(committed_on) from `tabDev Git Commit Item` "
				"where parent = %s and parenttype = 'Dev Git Commit'",
				self.name,
			)[0]
			frappe.db.set_value(
				"Dev Git Commit",
				self.name,
				{"total_commits": total, "first_commit_on": first, "last_commit_on": last},
			)

		values = {"total_commits": total, "first_commit_on": first, "last_commit_on": last}
		if synced:
			values["last_synced_on"] = now_datetime()
		self.db_set(values)

	# ------------------------------------------------------------------
	# Helpers
	# ------------------------------------------------------------------
	def _since(self):
		if self.last_synced_on:
			base = get_datetime(self.last_synced_on) - timedelta(days=1)  # 1 day safety margin
		else:
			base = add_days(now_datetime(), -(self.sync_days or 30))
		return get_datetime(base).strftime("%Y-%m-%dT%H:%M:%SZ")

	def _order_branches(self, names):
		"""Default branch first, so shared commits are labelled with it."""
		default = self.default_branch or "main"
		return sorted(set(names), key=lambda n: (n != default, n))

	def _commit_url(self, sha):
		base = (self.repo_url or "").rstrip("/")
		if base.endswith(".git"):
			base = base[:-4]
		if not base:
			return None
		host = urlparse(base).hostname or ""
		if self.provider == "GitLab" or "gitlab" in host:
			return f"{base}/-/commit/{sha}"
		return f"{base}/commit/{sha}"

	def _get(self, url, headers, params=None):
		try:
			r = requests.get(url, headers=headers, params=params, timeout=30)
			r.raise_for_status()
			return r.json()
		except requests.RequestException as e:
			frappe.throw(_("Git API request failed: {0}").format(str(e)))

	# ------------------------------------------------------------------
	# Fetchers - each yields (branch, normalized_commit_dict)
	# ------------------------------------------------------------------
	def _fetch_github(self, since):
		base = f"https://api.github.com/repos/{self._slug()}"
		headers = {"Accept": "application/vnd.github+json"}
		token = self.get_password("access_token", raise_exception=False)
		if token:
			headers["Authorization"] = f"Bearer {token}"

		branches = [b["name"] for b in self._get(f"{base}/branches", headers, {"per_page": 100})]
		for branch in self._order_branches(branches):
			params = {"sha": branch, "since": since, "per_page": MAX_COMMITS_PER_BRANCH}
			for c in self._get(f"{base}/commits", headers, params):
				info = c["commit"]
				yield branch, {
					"sha": c["sha"],
					"message": info["message"],
					"author_name": info["author"]["name"],
					"author_email": info["author"]["email"],
					"committed_on": info["author"]["date"],
				}

	def _fetch_gitlab(self, since):
		u = urlparse(self.repo_url)
		base = f"{u.scheme}://{u.netloc}/api/v4/projects/{quote(self._slug(), safe='')}"
		headers = {}
		token = self.get_password("access_token", raise_exception=False)
		if token:
			headers["PRIVATE-TOKEN"] = token

		branches = [b["name"] for b in self._get(f"{base}/repository/branches", headers, {"per_page": 100})]
		for branch in self._order_branches(branches):
			params = {"ref_name": branch, "since": since, "per_page": MAX_COMMITS_PER_BRANCH}
			for c in self._get(f"{base}/repository/commits", headers, params):
				yield branch, {
					"sha": c["id"],
					"message": c["message"],
					"author_name": c["author_name"],
					"author_email": c["author_email"],
					"committed_on": c["authored_date"],
				}

	def _fetch_local(self, since=None, limit=None):
		"""
		Walk branches (default first). Each later branch only yields commits that are
		NOT already reachable from the earlier ones (`--not`), so every commit is
		read once and labelled with the first branch that contains it.
		"""
		top = self.local_path
		remaining = limit
		processed = []

		for display, ref in list_refs(top, self.default_branch or "main"):
			args = ["log", ref]
			if processed:
				args += ["--not", *processed]
			if since:
				args.append(f"--since={since}")
			if remaining is not None:
				args += ["-n", str(remaining)]
			args += [LOG_FORMAT, "--"]

			for rec in _git(top, *args, timeout=600).split(RECORD_SEP):
				rec = rec.strip()
				if not rec:
					continue
				sha, name, email, date, message = rec.split(FIELD_SEP, 4)
				yield display, {
					"sha": sha,
					"message": message.strip(),
					"author_name": name,
					"author_email": email,
					"committed_on": date,
				}
				if remaining is not None:
					remaining -= 1

			processed.append(ref)
			if remaining is not None and remaining <= 0:
				return