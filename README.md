# ERP Dev TASK ( Extensive Task Manager made for developers ) 

**A git-integrated task manager for developers, built as a custom Frappe / ERPNext app.**

Commits from your repositories are imported automatically, linked to tasks (by task ID or by keyword matching), reviewed by a reviewer, and finally turned into release notes (Markdown and PDF), optionally polished by a local LLM (Ollama).

> ## BACKEND ONLY
> This repository is the **backend** only: Frappe DocTypes, Python controllers, whitelisted API methods, desk-side JavaScript (form buttons), a workspace dashboard, and the release-notes generator.
>
> **There is no React frontend yet.** Everything is used through the standard Frappe Desk UI (`http://localhost:8000/app`). The planned React frontend (Kanban board, task detail page, Recharts dashboards, `frappe-react-sdk`) is listed in the [Roadmap](#16-roadmap-not-built-yet) and is **not part of this codebase**.

---

## Table of Contents

1. [What it does](#1-what-it-does)
2. [Current status](#2-current-status)
3. [Architecture](#3-architecture)
4. [Environment and requirements](#4-environment-and-requirements)
5. [Install the ERPNext / Frappe bench (WSL)](#5-install-the-erpnext--frappe-bench-wsl)
6. [Install this app](#6-install-this-app)
7. [App file layout](#7-app-file-layout)
8. [DocTypes reference](#8-doctypes-reference)
9. [Importing and syncing commits](#9-importing-and-syncing-commits)
10. [Linking commits to tasks](#10-linking-commits-to-tasks)
11. [Review workflow (Approve / Deny)](#11-review-workflow-approve--deny)
12. [Commit message and branch conventions](#12-commit-message-and-branch-conventions)
13. [Release notes and PDF](#13-release-notes-and-pdf)
14. [Ollama setup (local LLM)](#14-ollama-setup-local-llm)
15. [Workspace dashboard on the Home page](#15-workspace-dashboard-on-the-home-page)
16. [Roadmap (not built yet)](#16-roadmap-not-built-yet)
17. [Troubleshooting](#17-troubleshooting)
18. [Known trade-offs](#18-known-trade-offs)
19. [Security notes](#19-security-notes)
20. [Command cheat sheet](#20-command-cheat-sheet)

---

## 1. What it does

| Capability | Description |
|---|---|
| **Task management** | `Dev Task` DocType with type, status, priority, assignee, sprint, release, story points, start/due dates, estimated and actual hours. |
| **Repository registry** | `Dev Git Tool` holds one record per repository (GitHub, GitLab, or a local folder). |
| **Commit import** | One-click import from a local repo, API sync for GitHub/GitLab, and a webhook for live pushes. |
| **Commit storage** | One `Dev Git Commit` record per repo, with every commit as a child row (newest first). |
| **Hybrid task linking** | Explicit task ID first (`TASK-00012`, `task 12`, `fixes #12`, branch name). If none, keyword scoring against the task subject/description, the commit message and changed file paths. |
| **Confirm or auto-link** | High confidence: linked automatically. Medium confidence: a `Dev Commit Suggestion` is created for the user to **Link** or **Dismiss**. |
| **Multiple commits per task** | A task holds a table of linked commits (`Dev Task Commit`) with SHA, URL, author, date, branch, method and score. |
| **Reviewer flow** | When commits are linked and a reviewer is set, the task becomes *Pending Review* and **Approved** / **Denied** buttons appear. |
| **Release notes** | Done tasks of a release are grouped by type into Markdown, optionally polished by Ollama, and exported as PDF. |
| **Dashboard** | A workspace with number cards, a status chart, and shortcuts. |

---

## 2. Current status

**Built (backend):**
- DocTypes: Dev Task, Dev Project, Dev Sprint, Dev Release, Dev Git Tool, Dev Git Contributor, Dev Git Commit, Dev Git Commit Item, Dev Task Commit, Dev Commit Suggestion
- Local repository import with Windows/WSL path conversion
- GitHub / GitLab API sync and a push webhook (`api.py`)
- Commit-to-task linker (`linker.py`)
- Reviewer approve/deny flow
- Release notes generator with optional Ollama polish and PDF download (`release_notes.py`)
- Workspace, number cards, and a chart

**Not built:** React frontend, Pull Request DocType and PR-driven status changes, Time Log, My Day dashboard, standup generator, notifications. See the [Roadmap](#16-roadmap-not-built-yet).

**Testing note:** the git-reading, storage and linking logic was developed iteratively against a live bench. Treat `bulk_insert` / `bulk_update` paths, background jobs and very large child tables as the areas most worth re-testing after changes.

---

## 3. Architecture

```
 GitHub / GitLab / local folder
          |
          |  import button | Sync button | webhook (push)
          v
   Dev Git Tool  ---- add_commits() ---->  Dev Git Commit (1 per repo)
   (one per repo)                              \__ Dev Git Commit Item (child rows, 1 per commit)
          |                                              |
          |                                      linker.process_commits()
          |                                              |
          |            +-----------------+---------------+------------------+
          |            |                 |                                  |
          |      explicit ID        keyword score >= auto           keyword score >= suggest
          |      (100%)             (and clear lead)                         |
          |            |                 |                                  v
          |            v                 v                         Dev Commit Suggestion
          |         Dev Task  <---- Dev Task Commit rows           (Pending -> Accepted/Rejected)
          |            |
          |            v
          |   reviewer set? -> review_status = Pending Review -> [Approved] -> status Done
          |                                                      [Denied]   -> stays, relink needed
          v
   Dev Release  <-- Done tasks grouped by type --> Markdown --(optional)--> Ollama --> PDF
```

**One code path:** import, Sync, webhook and re-scan all go through `DevGitTool.add_commits()`, which stores rows and then calls `linker.process_commits()`.

---

## 4. Environment and requirements

| Component | Version / detail |
|---|---|
| Frappe | 15.88.0 |
| ERPNext | 15.87.0 |
| Custom app | `task_manager_dev` v0.0.1 (module name **Task Manager Dev**) |
| Database | MariaDB |
| OS | Ubuntu on **WSL2** (Windows 11/10) |
| Python | 3.12 (as used by the bench virtualenv) |
| Bench folder | `~/karan-builders` |
| Site name | `Dev_Tool_Task` |
| LLM (optional) | Ollama inside Ubuntu, models `llama3.2:latest` and `llama3:latest` |
| PDF engine | `wkhtmltopdf` (required for Download PDF) |

Repositories on the Windows `V:` drive appear inside WSL as `/mnt/v`.

---

## 5. Install the ERPNext / Frappe bench (WSL)

> Skip this section if your bench already runs. Follow the official Frappe installation guide for your exact OS version; the steps below are the typical sequence.

### 5.1 Enable WSL2 with systemd

In PowerShell (Administrator):

```powershell
wsl --install -d Ubuntu
```

Inside Ubuntu, enable systemd (needed for services such as MariaDB and Ollama):

```bash
sudo nano /etc/wsl.conf
```

```ini
[boot]
systemd=true
```

Then in PowerShell: `wsl --shutdown`, and reopen Ubuntu.

### 5.2 System packages

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git python3-dev python3-pip python3-venv python3-setuptools \
  redis-server mariadb-server mariadb-client libmariadb-dev pkg-config \
  xvfb libfontconfig wkhtmltopdf curl cron
```

> `wkhtmltopdf` from apt is enough for most cases. If PDF output ever shows an error about "patched Qt", install the patched build from the wkhtmltopdf releases page.

### 5.3 Node.js 18 and Yarn

```bash
curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
sudo apt install -y nodejs
sudo npm install -g yarn
```

### 5.4 MariaDB configuration

Add to `/etc/mysql/mariadb.conf.d/99-frappe.cnf`:

```ini
[mysqld]
character-set-client-handshake = FALSE
character-set-server = utf8mb4
collation-server = utf8mb4_unicode_ci

[mysql]
default-character-set = utf8mb4
```

```bash
sudo systemctl restart mariadb
sudo mysql_secure_installation
```

### 5.5 Install bench and create the bench folder

```bash
pip3 install frappe-bench --break-system-packages   # or use pipx
bench init --frappe-branch version-15 karan-builders
cd karan-builders
```

### 5.6 Get ERPNext and create the site

```bash
bench get-app --branch version-15 erpnext
bench new-site Dev_Tool_Task --admin-password <your-admin-password>
bench --site Dev_Tool_Task install-app erpnext
bench use Dev_Tool_Task
```

### 5.7 Start

```bash
bench start
```

Open `http://localhost:8000` and log in as `Administrator`.

> **Site name tip:** the site name `Dev_Tool_Task` contains underscores. If host-based routing causes problems, use `bench --site Dev_Tool_Task ...` for commands and access via `localhost:8000` after `bench use Dev_Tool_Task`.

---

## 6. Install this app

If the app already exists in `apps/task_manager_dev`, skip to 6.2.

### 6.1 Create the app (first time only)

```bash
cd ~/karan-builders
bench new-app task_manager_dev
# App Title: Task Manager Dev
# Module name: Task Manager Dev
```

### 6.2 Install on the site

```bash
bench --site Dev_Tool_Task install-app task_manager_dev
```

### 6.3 Add the code

Copy the project files into the layout shown in [section 7](#7-app-file-layout). Two rules cause most install errors:

1. `linker.py`, `api.py` and `release_notes.py` go at the **package root**: `apps/task_manager_dev/task_manager_dev/`.
2. Every DocType folder needs an empty `__init__.py`.

### 6.4 Migrate

```bash
cd ~/karan-builders
bench --site Dev_Tool_Task migrate
bench --site Dev_Tool_Task clear-cache
bench restart
```

Hard-refresh the browser (Ctrl+Shift+R).

### 6.5 Roles

The DocType permissions use `System Manager`, `Projects Manager` and `Projects User`. These roles exist when ERPNext (Projects module) is installed. Give your users the appropriate role under **User > Roles**.

### 6.6 Background workers

Large imports (over 500 commits) and re-scans (over 3000 unlinked commits) run as background jobs on the `long` queue. **A worker must be running**: `bench start` runs one automatically. In production, supervisor does.

---

## 7. App file layout

```
~/karan-builders/apps/task_manager_dev/
└── task_manager_dev/                      <- Python package root
    ├── __init__.py
    ├── hooks.py
    ├── modules.txt                        <- must contain: Task Manager Dev
    ├── api.py                             <- webhook + import_local_repo
    ├── linker.py                          <- commit-to-task linking logic
    ├── release_notes.py                   <- Markdown build, Ollama polish, PDF
    └── task_manager_dev/                  <- module folder
        ├── doctype/
        │   ├── dev_task/                  (.json .py .js)
        │   ├── dev_task_commit/           (.json .py)            child table
        │   ├── dev_project/
        │   ├── dev_sprint/
        │   ├── dev_release/               (.json .py .js)
        │   ├── dev_git_tool/              (.json .py .js)
        │   ├── dev_git_contributor/       (.json .py)            child table
        │   ├── dev_git_commit/            (.json .py)
        │   ├── dev_git_commit_item/       (.json .py)            child table
        │   └── dev_commit_suggestion/     (.json .py .js)
        ├── workspace/task_manager_dev/task_manager_dev.json
        ├── number_card/                   open_tasks, in_progress_tasks, pending_review, pending_suggestions
        └── dashboard_chart/tasks_by_status/tasks_by_status.json
```

Each DocType folder also contains an empty `__init__.py`.

`dev_git_tool.js` has a constant at the top that must equal the package name:

```js
const APP = "task_manager_dev";
```

---

## 8. DocTypes reference

| DocType | Type | Purpose |
|---|---|---|
| **Dev Task** | Master | The task. Autoname `TASK-{#####}` (e.g. `TASK-00012`). Status: Backlog / To Do / In Progress / In Review / Blocked / Done / Cancelled. Type: Feature / Bug / Hotfix / Chore / Refactor / Docs / Test. Includes reviewer, review status, linked commits table, suggested commits panel, git summary fields and a release note summary. |
| **Dev Task Commit** | Child of Dev Task | One row per linked commit: short SHA, full SHA, summary, URL, author, date, branch, repo, link method (Explicit / Auto / Suggested / Manual) and match score. |
| **Dev Project** | Master | Project a task and repo belong to. Auto-created from a repo name on import. |
| **Dev Sprint** | Master | Sprint. |
| **Dev Release** | Master | Release (version). Holds the generated `release_notes`. |
| **Dev Git Tool** | Master | One record per repository: provider (GitHub / GitLab / Local Repository), URL, local path, default branch, access token and webhook secret (Password fields), project link, linking thresholds and a Contributors table. |
| **Dev Git Contributor** | Child of Dev Git Tool | Name, email, commit count. |
| **Dev Git Commit** | Master | **One record per repo** (named by repo). Totals, first/last commit dates, and the `commits` child table. |
| **Dev Git Commit Item** | Child of Dev Git Commit | One row per commit: date, branch, SHA, summary, author, URL, message, changed files, `task`, `link_status` (Unlinked / Linked / Suggested), `link_method`, `match_score`. |
| **Dev Commit Suggestion** | Master | A proposed commit-to-task match awaiting confirmation. Status: Pending / Accepted / Rejected. Rejected pairs are never suggested or auto-linked again. |

### Important fields on Dev Task

| Field | Notes |
|---|---|
| `reviewer` | Must differ from the assignee. If set, the task can only reach Done through **Approved**. |
| `review_status` | Not Requested / Pending Review / Approved / Denied (read-only, system managed). |
| `task_commits` | Linked commits table. Deleting a row **unlinks** that commit permanently (it will not auto-link again). |
| `branch_name` | Used as a matching signal (same branch as a commit). |
| `commit_count`, `last_commit_sha`, `last_commit_on` | Maintained automatically. |
| `release` | Link to Dev Release; drives release notes. |
| `release_note` | Optional user-friendly line; falls back to the subject. |

---

## 9. Importing and syncing commits

### 9.1 Import from a local repository (recommended start)

1. Open **Dev Git Tool > New**.
2. Click **Import from Local Repo**.
3. Paste the folder path, in any format:
   - `V:\KB REPO\KB_P-M` (converted to `/mnt/v/KB REPO/KB_P-M`)
   - `V:/KB REPO/KB_P-M`
   - `\\wsl$\Ubuntu\home\me\repo` or `\\wsl.localhost\Ubuntu\home\me\repo`
   - `/home/me/repo`
   - quoted paths are cleaned.
4. Click **Fetch & Import**.

It reads the repo name, clean remote URL (credentials stripped), default branch, branches, contributors and commits. It fills the form, creates a **Dev Project** named after the repo, and stores the commits.

- Up to **500 commits** are imported immediately. More than that runs as a background job (`long` queue) and the form refreshes when it finishes.
- Safety cap: **20,000 commits** (`MAX_IMPORT_COMMITS`).
- If the drive is not mounted, the error message shows the exact `mount` command.
- Git is called with `-c safe.directory=<path>`, so "dubious ownership" errors are avoided.

**Default branch detection order:** `origin/HEAD`, then `main`, then `master`, then the checked-out branch.

**Branch labels:** the default branch is read first. Each later branch only contributes commits not already on an earlier one, so each commit is stored once, labelled with the first branch that contains it.

### 9.2 Sync Commits button

- **GitHub / GitLab:** uses the API and an optional access token (needed for private repos and rate limits). First sync covers `sync_days` days (default 30); later syncs use `last_synced_on` minus a 1-day safety margin.
- **Local Repository:** re-reads `git log`.

Commits already stored (by SHA) are skipped.

### 9.3 Webhook (live updates)

Endpoint:

```
http://<your-host>:8000/api/method/task_manager_dev.api.git_webhook?repo=<Dev Git Tool name>
```

- GitHub: Content type `application/json`, event **push** only, secret = the Webhook Secret on the Dev Git Tool. The HMAC signature is verified.
- GitLab: set the same secret as the secret token.
- Only pushes to branches are handled.
- The URL must be reachable from the internet (use a tunnel such as ngrok or Cloudflare Tunnel for local development). A local repo needs no webhook.
- To enable file-path matching for webhook commits, pass changed files into `add_commits` as `"files": added + modified + removed` for each commit.

---

## 10. Linking commits to tasks

The linker (`linker.py`) runs automatically after every import, sync and webhook, and on demand via **Dev Git Tool > Tasks > Auto-Link Commits to Tasks** (back-fills existing, unlinked commits).

### 10.1 Step 1: explicit task ID

Matched in the commit message **and the branch name**:

| Text | Resolves to |
|---|---|
| `TASK-00012`, `TASK-12`, `TASK_12`, `task 12`, `task 0012` | `TASK-00012` |
| `fixes #12`, `closes #12`, `resolves #12`, `refs #12` | `TASK-00012` **only if** that task belongs to the same project as the repo |

The `#12` form is treated as "weak" because it might be a GitHub issue number. Explicit links score 100.

A task never receives the same SHA twice. One commit can link to several tasks if it names several IDs.

### 10.2 Step 2: keyword scoring (only if no ID was found)

Keywords are extracted from the task **subject** (weighted highest) and **description**, with stop-words removed, camelCase split and light stemming. They are compared with the commit message and changed file paths.

| Signal | Effect |
|---|---|
| Subject keywords matched | up to +60 (proportional), +10 if matched in the message itself |
| Description keywords matched | up to +25 |
| Commit on the task's `branch_name` | +35 |
| Commit author email equals task assignee | +10 |
| Commit date within task start/due window | +5 |
| Commit date clearly outside the window | -15 |

A commit needs at least 2 keyword hits or a branch match to be scored at all. Score is capped at 100.

### 10.3 Step 3: decision

| Score | Result |
|---|---|
| **>= Auto-Link Threshold (default 75)** and leads the runner-up by at least 15 | **Linked automatically** |
| **>= Suggest Threshold (default 35)** but below the auto bar, or too close to a runner-up | Up to 3 **Dev Commit Suggestions** (Pending) |
| Below the suggest threshold | Left unlinked |

Thresholds are editable per repository on **Dev Git Tool > Task Linking**. Keyword matching can be switched off there; explicit IDs always work.

### 10.4 Confirming suggestions

- On the **Dev Task** form, the *Suggested Commits* panel lists candidates with score and reasons. Click **Link** or **Dismiss**.
- Or use **Dev Git Tool > Tasks > Review Suggestions**, or the **Dev Commit Suggestion** form buttons.
- Linking one suggestion closes the other pending suggestions for the same commit.
- Dismissing records a rejection, so that pair is never suggested again.

### 10.5 Manual linking

On a Dev Task: **Git > Link Commit**. Pick from recent unlinked commits of the project, or paste SHAs (full or 7+ characters) or commit URLs, one per line.

### 10.6 Side effects of linking

- Task status moves from **Backlog / To Do** to **In Progress** (never backwards).
- Commit count, last commit SHA and date are recalculated.
- If a reviewer is set, `review_status` becomes **Pending Review**.

---

## 11. Review workflow (Approve / Deny)

1. Set a **Reviewer** on the task (must not be the assignee).
2. When a commit is linked, `review_status` becomes **Pending Review**. A banner shows who must review.
3. The reviewer (or a Projects Manager / System Manager) sees two buttons:
   - **Approved**: task becomes **Done**, `completed_on` is set, reviewer and time are recorded.
   - **Denied**: a reason is **required**. The task keeps its current status. The reviewer sees the reason in the banner.
4. After a denial, the developer links another commit. That puts the task back to **Pending Review**.

Rules:
- A task **with a reviewer** cannot be set to Done by editing the status field (including Kanban drags later). Only **Approved** completes it. To remove this restriction, delete the `_guard_completion()` call in `dev_task.py`.
- Review decisions are also logged as a comment on the task.
- Tasks without a reviewer behave normally.

Not yet implemented: a notification or ToDo sent to the reviewer when a task becomes Pending Review.

---

## 12. Commit message and branch conventions

**Ideal commit:**

```
TASK-00012: fix login redirect loop
```

or with a conventional-commit prefix:

```
fix(auth): stop redirect loop after login

TASK-00012
Redirect used the stale session cookie when the token expired.
```

**Rules**

1. Put the task ID in the first line. It is the only thing that gives a 100% link.
2. Imperative summary, under about 70 characters.
3. Use the task's own words. Without an ID, matching depends on shared keywords.
4. Optional type prefix: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`.
5. One task per commit where possible. For two tasks: `TASK-00012 TASK-00015: ...`.
6. Use the body for the why, not the what.

**Branch names:** `feature/TASK-00012-login-fix`. The branch name is read by the linker.

**Avoid:** `wip`, `update`, `changes`, `final`, `fixes #12` (unless task 12 is in the repo's project).

---

## 13. Release notes and PDF

Implemented in `release_notes.py`; buttons live in `dev_release.js`.

### 13.1 Flow

```
Done tasks (release = this Dev Release)
   -> Markdown, grouped by type
   -> (optional) Ollama polishes the wording
   -> saved to Dev Release.release_notes
   -> Download PDF (Frappe's wkhtmltopdf engine)
```

Grouping: Feature = New Features, Bug = Bug Fixes, Hotfix = Hotfixes, Refactor = Improvements, Chore = Maintenance, Docs = Documentation, Test = Tests. Each line uses `release_note` if set, otherwise the task subject, followed by the task ID.

### 13.2 Using it

1. Set the **Release** field on tasks, and get them to **Done** (via Approve if they have a reviewer).
2. Open the **Dev Release** and click **Generate Release Notes**.
3. Optionally tick **Polish wording with Ollama (AI)**.
4. Click **Download PDF**.

### 13.3 Hallucination guard

The model only rephrases. If its output drops any task ID, the code discards it and keeps the plain notes, and shows an orange message: *"The model dropped some tasks, so plain notes were used."* If Ollama is unreachable the same fallback applies.

### 13.4 Assumption

The `Dev Release` DocType must have a long-text field named `release_notes`. If your field has a different name, change it in `release_notes.py` (`generate_notes` and `download_pdf`).

### 13.5 PDF requirement

```bash
wkhtmltopdf --version
```

If this errors, install it (`sudo apt install wkhtmltopdf`) and restart bench.

---

## 14. Ollama setup (local LLM)

**Current setup:** Ollama is installed **inside Ubuntu (WSL)**, not on Windows. Bench and Ollama share the same machine, so Frappe talks to `http://localhost:11434`.

Installed models:

| Model | Size | Notes |
|---|---|---|
| `llama3.2:latest` | 3.2B, ~2 GB | Default. Faster, enough for rephrasing. |
| `llama3:latest` | 8B, ~4.7 GB | Better wording, slower on CPU. |

### 14.1 Install

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

This creates a systemd service (`ollama.service`) that starts the server on `127.0.0.1:11434`.

### 14.2 Verify

```bash
curl -m 5 http://localhost:11434/api/tags
```

You should get JSON listing your models.

### 14.3 Pull models

```bash
ollama pull llama3.2
ollama list
```

### 14.4 Point Frappe at it

```bash
cd ~/karan-builders
bench --site Dev_Tool_Task set-config ollama_url "http://localhost:11434"
bench --site Dev_Tool_Task set-config ollama_model "llama3.2:latest"
bench restart
```

Use the exact model name from `ollama list`. Both config keys are optional: the defaults are `http://localhost:11434` and `llama3.2`.

### 14.5 Starting and stopping

```bash
sudo systemctl start ollama
sudo systemctl status ollama --no-pager
sudo systemctl stop ollama
```

If systemd is not available:

```bash
nohup ollama serve > ~/ollama.log 2>&1 &
```

### 14.6 Problems we hit and the fixes

| Symptom | Cause | Fix |
|---|---|---|
| `ConnectTimeout` to `172.18.x.x:11434` | Ollama is **not** on Windows, so nothing listens there | Use `http://localhost:11434` |
| `curl: (7) Failed to connect` on localhost | Service not running | `sudo systemctl start ollama` |
| `status=203/EXEC`, `Permission denied` on `/usr/local/bin/ollama` | Broken or interrupted install | `sudo systemctl stop ollama; sudo rm -f /usr/local/bin/ollama; curl -fsSL https://ollama.com/install.sh \| sh` |
| First request very slow | Model loading into memory | Wait up to a minute; the code allows 240 s |

> **If you ever move Ollama to Windows:** set `OLLAMA_HOST=0.0.0.0`, allow TCP 11434 in the Windows firewall, and set `ollama_url` to the WSL gateway IP (`ip route | awk '/default/ {print $3}'`). That IP can change after restarts, which is why running Ollama inside WSL is recommended.

---

## 15. Workspace dashboard on the Home page

The workspace **Task Manager Dev** appears under **PUBLIC** in the left sidebar after `migrate`.

It contains:
- Number cards: **Open Tasks**, **In Progress Tasks**, **Pending Review**, **Pending Suggestions**
- A donut chart: **Tasks by Status**
- Shortcuts: Dev Task, Dev Git Tool, Dev Commit Suggestion, Dev Release
- Link cards for Planning and Git masters

If it does not show:
- `modules.txt` must contain `Task Manager Dev`.
- The file must be exactly `workspace/task_manager_dev/task_manager_dev.json`.
- Run `bench --site Dev_Tool_Task migrate && bench --site Dev_Tool_Task clear-cache`, then hard-refresh.

> Edits made in the UI are stored in the database only. A later `migrate` with a changed JSON file overwrites them. Treat the JSON files as the source of truth.

---

## 16. Roadmap (not built yet)

| # | Item | Notes |
|---|---|---|
| 1 | **Pull Request DocType** and PR-driven status | PR opened = In Review, PR merged = Done (set in code, bypassing Workflow). |
| 2 | **Reviewer notification** | ToDo / email when a task becomes Pending Review. |
| 3 | **Time Log** DocType | Feeds `actual_time`; estimate from commit timestamps plus manual timer; weekly summaries. |
| 4 | **My Day dashboard** | My tasks by priority/due date, my commits and PRs from today and yesterday. |
| 5 | **Standup generator** | Yesterday / Today / Blockers from commits and task changes. |
| 6 | **Branch helper** | "Create branch" button generating `feature/TASK-00012-slug`. |
| 7 | **Notifications** | Failed CI, PRs awaiting review, blocked or overdue tasks. |
| 8 | **React frontend** | Kanban board (dnd-kit), task detail with commit timeline, Recharts dashboards, `frappe-react-sdk`. **Not started.** |
| 9 | **Frappe Workflow** for manual status moves | Optional; git-driven changes bypass it. |

---

## 17. Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `ImportError: cannot import name 'linker' from 'task_manager_dev'` | `linker.py` is not at the package root | Move it to `apps/task_manager_dev/task_manager_dev/linker.py`. Test: `./env/bin/python -c "from task_manager_dev import linker; print('ok')"` |
| `Unknown column 'parent' in 'WHERE'` | A child table was created without `parent`, `parenttype`, `parentfield` | See the rebuild recipe below |
| Link field points to a missing DocType | Dev Project / Dev Sprint / Dev Release not created yet | All three must exist before migrate |
| Switching Dev Git Commit from per-commit to per-repo fails | Old rows exist | `frappe.db.delete("Dev Git Commit")` in console, then migrate |
| `Drive V: is not mounted` | WSL has no `/mnt/v` | `sudo mkdir -p /mnt/v && sudo mount -t drvfs V: /mnt/v` |
| `dubious ownership` | Git safe.directory | Already handled with `-c safe.directory`; otherwise `git config --global --add safe.directory <path>` |
| Import of a large repo seems stuck | Needs a worker on the `long` queue | Make sure `bench start` is running |
| Task cannot be set to Done | It has a reviewer | Use **Approved**, or remove the reviewer |
| Download PDF fails | `wkhtmltopdf` missing | `sudo apt install wkhtmltopdf` |
| Ollama "not reachable" | See [section 14.6](#146-problems-we-hit-and-the-fixes) | |

### Rebuild a child table that lost its `parent` column

```bash
bench --site Dev_Tool_Task console
```

```python
frappe.db.sql_ddl("drop table if exists `tabDev Task Commit`")
frappe.reload_doc("task_manager_dev", "doctype", "dev_task_commit", force=True)
frappe.db.commit()
frappe.db.sql("show columns from `tabDev Task Commit`")   # must list parent, parentfield, parenttype
```

Do the same for `Dev Git Commit Item` / `dev_git_commit_item` or `Dev Git Contributor` / `dev_git_contributor` if needed. **Dropping `tabDev Git Commit Item` deletes stored commits**, so re-run the import afterwards. `reload-doc` requires `--force` when used from the CLI.

---

## 18. Known trade-offs

- Child tables with thousands of rows make the **Dev Git Commit** form slow to open. Lower `MAX_IMPORT_COMMITS` in `dev_git_tool.py` if it becomes a problem.
- There are no per-commit list filters. Use **Report View** on Dev Git Commit with child-table columns.
- Later queries (dashboards, reports) must read `tabDev Git Commit Item` where `parent` is the repo record, not a commit list.
- With one parent record per repo, simultaneous webhook pushes could contend for the same document. Webhook processing should run in a background job if you see lock waits.
- GitHub/GitLab API syncs only provide the commit message for matching. File-path matching works for local imports (read from git) and for webhook commits if you pass `files`.
- Keyword matching is heuristic. Raise the auto-link threshold if you see wrong auto-links, or disable keyword matching and rely on IDs plus suggestions.

---

## 19. Security notes

- Access tokens and webhook secrets are **Password** fields (encrypted, not shown in plain text).
- Webhook requests are verified (GitHub HMAC signature of the raw body, or GitLab token). Never expose the endpoint without a secret.
- Setting a **local repository path** requires the **System Manager** role, because it lets the server read a folder.
- Git is always invoked without a shell, with arguments passed as a list.
- Remote URLs have embedded credentials stripped before storing.
- Review actions re-load the task on the server and check the user, so browser-supplied values are not trusted.
- Ollama runs locally; no commit or task data leaves your machine.

---

## 20. Command cheat sheet

```bash
# Run
cd ~/karan-builders
bench start

# After changing DocType JSON / adding files
bench --site Dev_Tool_Task migrate
bench --site Dev_Tool_Task clear-cache
bench restart

# Console
bench --site Dev_Tool_Task console

# Config for Ollama
bench --site Dev_Tool_Task set-config ollama_url "http://localhost:11434"
bench --site Dev_Tool_Task set-config ollama_model "llama3.2:latest"

# Ollama
sudo systemctl start ollama
ollama list
curl -m 5 http://localhost:11434/api/tags

# Sanity checks
./env/bin/python -c "from task_manager_dev import linker; print('ok')"
wkhtmltopdf --version
```

---

**Reminder:** this is a **backend-only** project. All screens are Frappe Desk forms, lists, reports and the workspace. A React frontend is planned but not part of this repository yet.
