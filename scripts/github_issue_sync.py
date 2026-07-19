#!/usr/bin/env python3
"""Sync tasks from docs/markdown/DELIVERABLE_1_SYSTEM_DESIGN.md to GitHub Issues.

Parses the 6-column task tables (| **P1-T01** | Name | Description |
`[DEV-1]` | Deps | Days |) and creates one GitHub Issue per task with
labels, milestone, and assignee.

Usage:
    python github_issue_sync.py                 # interactive (test-one, then all)
    python github_issue_sync.py --dry-run       # parse + print, create nothing
    python github_issue_sync.py --phase 1       # only Phase 1 tasks
    python github_issue_sync.py --limit 5       # only the first N tasks
    python github_issue_sync.py --yes           # skip prompts (use with care)
    python github_issue_sync.py --report        # regenerate docs/markdown/PROGRESS.md from live issue state

Requirements: `gh` CLI authenticated against the repository.
Safe to re-run: tasks whose "[Px-Tyy]" title prefix already exists are skipped.
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

DOC_PATH = Path(__file__).resolve().parent.parent / "docs" / "markdown" / "DELIVERABLE_1_SYSTEM_DESIGN.md"
PROGRESS_PATH = Path(__file__).resolve().parent.parent / "docs" / "markdown" / "PROGRESS.md"

# ==========================================
# 1. MAP DEVELOPERS TO GITHUB USERNAMES
# ==========================================
GITHUB_USERNAMES = {
    "DEV-1": "omaressam7704",
    "DEV-2": "aibrahimhlms",
    "DEV-3": "bakr33934-svg",
}

# Label taxonomy — matches DELIVERABLE_2 §5.2
PHASE_LABELS = {
    "1": ("phase:1-workspace", "0052CC"),
    "2": ("phase:2-saas", "0065FF"),
    "3": ("phase:3-erp", "0078D7"),
    "4": ("phase:4-dashboards", "008CBA"),
    "5": ("phase:5-ai", "00A0DC"),
    "6": ("phase:6-portal", "00B4D8"),
    "7": ("phase:7-mobile", "48CAE4"),
    "8": ("phase:8-platform", "90E0EF"),
    "9": ("phase:9-marketplace", "ADE8F4"),
    "10": ("phase:10-enterprise", "CAF0F8"),
}
DEV_LABELS = {
    "DEV-1": ("dev:DEV-1", "2EA043"),
    "DEV-2": ("dev:DEV-2", "3FB950"),
    "DEV-3": ("dev:DEV-3", "56D364"),
}
MILESTONES = {
    "1": "Phase 1: Customer Workspace",
    "2": "Phase 2: SaaS Automation",
    "3": "Phase 3: ERP + UAE Localization",
    "4": "Phase 4: Executive Dashboards",
    "5": "Phase 5: AI Platform",
    "6": "Phase 6: Customer Portal",
    "7": "Phase 7: Mobile Application",
    "8": "Phase 8: Platform Services",
    "9": "Phase 9: Marketplace (Deferred)",
    "10": "Phase 10: Enterprise Readiness",
}

ROW_PATTERN = re.compile(
    r"\|\s*\*\*(P\d+\-T\d+)\*\*\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|"
    r"\s*`?\[(DEV-\d)\]`?\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|"
)


def run_gh(args: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["gh"] + args, capture_output=capture, text=True)


_ASSIGNABLE_CACHE: set[str] | None = None


def assignable_users() -> set[str]:
    """GitHub usernames that can actually be assigned issues on this repo.

    A username that maps a dev but is NOT a repo collaborator cannot be
    assigned — passing --assignee for them makes `gh issue create` fail.
    We query once and only assign users who are genuinely assignable; the
    dev:DEV-N label still records ownership either way.
    """
    global _ASSIGNABLE_CACHE
    if _ASSIGNABLE_CACHE is None:
        result = run_gh(["api", "repos/{owner}/{repo}/assignees", "--paginate",
                         "--jq", ".[].login"])
        if result.returncode != 0:
            print("⚠️ Could not fetch assignable users — assignments will be skipped.")
            _ASSIGNABLE_CACHE = set()
        else:
            _ASSIGNABLE_CACHE = {u.strip() for u in result.stdout.splitlines() if u.strip()}
    return _ASSIGNABLE_CACHE


def parse_markdown_tasks() -> list[dict]:
    content = DOC_PATH.read_text(encoding="utf-8")
    tasks = []
    for match in ROW_PATTERN.finditer(content):
        task_id, name, description, assignee, deps, days = (g.strip() for g in match.groups())
        phase = re.search(r"P(\d+)", task_id).group(1)
        tasks.append({
            "id": task_id,
            "name": name,
            # <br> keeps rows single-line in the table; real newlines in the issue
            "description": description.replace("<br>", "\n"),
            "assignee": assignee,
            "dependencies": deps,
            "days": days,
            "phase": phase,
        })
    return tasks


def ensure_labels() -> None:
    """Create the full label taxonomy (idempotent via --force)."""
    print("Ensuring labels exist...")
    for name, color in list(PHASE_LABELS.values()) + list(DEV_LABELS.values()):
        run_gh(["label", "create", name, "--color", color, "--force"])


def ensure_milestones() -> dict[str, str]:
    """Create per-phase milestones if missing. Returns phase -> milestone title."""
    print("Ensuring milestones exist...")
    result = run_gh(["api", "repos/{owner}/{repo}/milestones?state=all&per_page=100"])
    existing = set()
    if result.returncode == 0 and result.stdout.strip():
        existing = {m["title"] for m in json.loads(result.stdout)}
    for title in MILESTONES.values():
        if title not in existing:
            create = run_gh(["api", "repos/{owner}/{repo}/milestones",
                             "-f", f"title={title}"])
            if create.returncode != 0:
                print(f"  ⚠️ could not create milestone '{title}': {create.stderr.strip()}")
    return MILESTONES


def existing_issue_prefixes() -> set[str]:
    """Return the set of '[Px-Tyy]' prefixes already present in issue titles."""
    result = run_gh(["issue", "list", "--state", "all", "--limit", "1000",
                     "--json", "title"])
    if result.returncode != 0:
        print("⚠️ Could not list existing issues — duplicate protection disabled.")
        return set()
    prefixes = set()
    for issue in json.loads(result.stdout or "[]"):
        m = re.match(r"\[(P\d+-T\d+)\]", issue["title"])
        if m:
            prefixes.add(m.group(1))
    return prefixes


def build_body(task: dict) -> str:
    return f"""## Task: [{task['id']}] {task['name']}

**Phase**: {task['phase']}
**Role Assigned**: {task['assignee']}
**Dependencies**: {task['dependencies']}
**Estimated Days**: {task['days']}

### Description
{task['description']}

### OCA Check (Rule 2)
- [ ] Searched OCA repositories for an existing Odoo 19 solution
- [ ] Decision recorded: use OCA module / build custom (with justification)

### Definition of Done
- [ ] Acceptance criteria in the description met, with evidence
- [ ] Code follows Odoo 19 conventions (`<list>` views, no `attrs=`) — Rule 6
- [ ] No Odoo core files modified — Rule 1
- [ ] Two-layer separation respected — Rule 3
- [ ] Security: UI restrictions mirrored at ORM/RPC where applicable — Rule 7
- [ ] Unit tests added/updated; E2E journeys updated where relevant
- [ ] CI green; PR approved by 1 reviewer; docs updated
"""


def create_github_issue(task: dict, dry_run: bool = False) -> bool:
    title = f"[{task['id']}] {task['name']}"
    labels = f"{PHASE_LABELS[task['phase']][0]},{DEV_LABELS[task['assignee']][0]}"
    milestone = MILESTONES[task["phase"]]

    if dry_run:
        print(f"  [dry-run] {title}  ({labels}; milestone: {milestone})")
        return True

    cmd = ["issue", "create", "--title", title, "--body", build_body(task),
           "--label", labels, "--milestone", milestone]
    github_user = GITHUB_USERNAMES.get(task["assignee"], "")
    assign_note = ""
    if github_user and github_user in assignable_users():
        cmd += ["--assignee", github_user]
    elif github_user:
        assign_note = f" (unassigned — {github_user} is not a repo collaborator; dev:{task['assignee']} label set)"

    print(f"\nCreating issue: {title}")
    result = run_gh(cmd, capture=False)
    if result.returncode == 0:
        print(f"✅ Created successfully!{assign_note}")
        return True
    print(f"❌ Failed to create: {title}")
    return False


def fetch_issue_states() -> dict[str, list[dict]]:
    """Map each '[Px-Tyy]' task ID to the GitHub issue(s) carrying it.

    One `gh` call (state=all). A task may map to zero issues (not synced yet)
    or more than one (duplicate) — the report surfaces both.
    """
    result = run_gh(["issue", "list", "--state", "all", "--limit", "1000",
                     "--json", "number,title,state,closedAt,url"])
    by_task: dict[str, list[dict]] = {}
    if result.returncode != 0:
        print("⚠️ Could not list issues — cannot build PROGRESS.md.")
        return by_task
    for issue in json.loads(result.stdout or "[]"):
        m = re.match(r"\[(P\d+-T\d+)\]", issue["title"])
        if m:
            by_task.setdefault(m.group(1), []).append(issue)
    return by_task


def _cell(text: str) -> str:
    """Escape pipes so a value never breaks the markdown table."""
    return text.replace("|", "\\|")


def _task_status(issues: list[dict]) -> tuple[str, str, str]:
    """Return (status_label, issue_cell, closed_date) for one task's issue(s)."""
    if not issues:
        return "⚪ no issue", "—", ""
    closed = [i for i in issues if i["state"] == "CLOSED"]
    chosen = min(closed or issues, key=lambda i: i["number"])
    dup = f" (+{len(issues) - 1} dup)" if len(issues) > 1 else ""
    cell = f"[#{chosen['number']}]({chosen['url']}){dup}"
    if closed:
        return "✅ done", cell, (chosen.get("closedAt") or "")[:10]
    return "🔨 open", cell, ""


def generate_progress_report() -> None:
    """Write docs/markdown/PROGRESS.md — a git-tracked mirror of issue state.

    Source of tasks: DELIVERABLE_1_SYSTEM_DESIGN.md. Source of status: GitHub.
    Deterministic given the same tasks + issue states (only the sync date and
    real state changes move), so `git diff` after a re-run shows real progress.
    """
    tasks = parse_markdown_tasks()
    by_task = fetch_issue_states()
    phases = sorted({t["phase"] for t in tasks}, key=int)

    def phase_name(ph: str) -> str:
        return MILESTONES[ph].split(": ", 1)[1]

    out: list[str] = [
        "# NCollection ERP — Progress Tracker",
        "",
        "> **Second source of truth for \"what's done.\"** Mirrors GitHub issue state",
        "> so progress survives even if GitHub is unavailable. **Generated — do not",
        "> hand-edit.** Regenerate after merges/closes:",
        ">",
        "> ```bash",
        "> python scripts/github_issue_sync.py --report",
        "> ```",
        ">",
        "> Tasks: `DELIVERABLE_1_SYSTEM_DESIGN.md` · Status: GitHub issues ·"
        f" Last synced: {date.today().isoformat()}",
        "",
        "## Scoreboard",
        "",
        "| Phase | Done | Total | % |",
        "|---|---|---|---|",
    ]

    grand_done = grand_total = 0
    for ph in phases:
        pts = [t for t in tasks if t["phase"] == ph]
        done = sum(1 for t in pts
                   if any(i["state"] == "CLOSED" for i in by_task.get(t["id"], [])))
        grand_done += done
        grand_total += len(pts)
        pct = round(100 * done / len(pts)) if pts else 0
        out.append(f"| Phase {ph} — {phase_name(ph)} | {done} | {len(pts)} | {pct}% |")
    gpct = round(100 * grand_done / grand_total) if grand_total else 0
    out.append(f"| **Total** | **{grand_done}** | **{grand_total}** | **{gpct}%** |")
    out.append("")

    for ph in phases:
        pts = [t for t in tasks if t["phase"] == ph]
        out.append(f"## Phase {ph} — {phase_name(ph)}")
        out.append("")
        out.append("| Task | Name | Dev | Deps | Issue | Status | Closed |")
        out.append("|---|---|---|---|---|---|---|")
        for t in pts:
            status, cell, closed = _task_status(by_task.get(t["id"], []))
            deps = _cell(t["dependencies"] or "None")
            out.append(f"| {t['id']} | {_cell(t['name'])} | {t['assignee']} | "
                       f"{deps} | {cell} | {status} | {closed} |")
        out.append("")

    PROGRESS_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"✅ Wrote {PROGRESS_PATH.name} — {grand_done}/{grand_total} tasks done.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="parse and print only")
    parser.add_argument("--phase", type=str, help="only tasks of this phase number")
    parser.add_argument("--limit", type=int, help="only the first N tasks")
    parser.add_argument("--yes", action="store_true", help="skip confirmation prompts")
    parser.add_argument("--report", action="store_true",
                        help="regenerate docs/markdown/PROGRESS.md from issue state and exit")
    args = parser.parse_args()

    if args.report:
        generate_progress_report()
        return

    print(f"Parsing tasks from {DOC_PATH.name} ...")
    tasks = parse_markdown_tasks()
    if args.phase:
        tasks = [t for t in tasks if t["phase"] == args.phase]
    if args.limit:
        tasks = tasks[: args.limit]

    print(f"Found {len(tasks)} tasks.")
    if not tasks:
        print("No tasks found. Check the markdown table formatting.")
        sys.exit(1)

    if args.dry_run:
        for task in tasks:
            create_github_issue(task, dry_run=True)
        total_days = sum(int(t["days"]) for t in tasks if t["days"].isdigit())
        print(f"\n[dry-run] {len(tasks)} tasks, {total_days} dev-days. Nothing created.")
        return

    ensure_labels()
    ensure_milestones()

    skip = existing_issue_prefixes()
    pending = [t for t in tasks if t["id"] not in skip]
    if len(pending) < len(tasks):
        print(f"Skipping {len(tasks) - len(pending)} tasks that already have issues.")
    if not pending:
        print("Everything is already synced. ✅")
        return

    if not args.yes:
        confirm_test = input(
            f"Run a TEST by creating ONLY the first issue ({pending[0]['id']})? (y/n): ")
        if confirm_test.lower() == "y":
            create_github_issue(pending[0])
            print("\nTest complete! Check GitHub, then re-run (existing issues are skipped).")
            return
        confirm_all = input(f"\nReady to create ALL {len(pending)} issues. Proceed? (y/n): ")
        if confirm_all.lower() != "y":
            print("Aborted.")
            return

    created = sum(create_github_issue(t) for t in pending)
    print(f"\nDone: {created}/{len(pending)} issues created.")


if __name__ == "__main__":
    main()
