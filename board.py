#!/usr/bin/env python3
"""A board of the open pull requests across the Omarchy M repos: one row per PR and its single blocking item.

    board.py --out docs --url https://.../           live, needs the gh CLI (GITHUB_TOKEN or a login)
    board.py --fixture data.json --out /tmp/board    from saved data, no network

Writes index.html (the page), board.json (the rows) and summary.txt (the once-a-day text).
The maclab qualification state is "unknown": maclab's job records are not public.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence

REPOS = (
    "omacom/linux-aurora", "omacom/mesa", "omacom/omarchy-mac-installer", "omacom/omarchy-mac-pkgs", "chriskearney/maclab",
    "aurora-silicon/aurora-installer",
)
CAN_MERGE = "None: can merge now"
RC_LABEL = re.compile(r"^(rc[- ]?blocker|release[- ]?blocker|blocker|rc)$", re.IGNORECASE)
FAILED = {"FAILURE", "TIMED_OUT", "STARTUP_FAILURE"}
APPROVAL_GATE = "approve"  # a check some repos use to say "no approving review yet"
MAX_LISTED = 12
MAINTAINER_KINDS = ("Needs review", "Workflow run needs approval", "Branch rules")  # what a maintainer or reviewer must do
PAGE_SIZE, SMALL_PAGE_SIZE = 20, 5  # a big repo's check lists can make GitHub answer 502

QUERY = """
query($owner: String!, $name: String!, $after: String, $size: Int!) {
  repository(owner: $owner, name: $name) {
    isPrivate
    pullRequests(states: OPEN, first: $size, after: $after, orderBy: {field: CREATED_AT, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title url isDraft mergeable mergeStateStatus reviewDecision updatedAt baseRefName
        author { login }
        labels(first: 10) { nodes { name } }
        reviews(last: 30) { nodes { state author { login } } }
        commits(last: 1) { nodes { commit { statusCheckRollup { state contexts(first: 40) { nodes {
          __typename ... on CheckRun { name conclusion status } ... on StatusContext { context state } } } } } } }
      }
    }
  }
}
"""


# -- one PR -> one row ----------------------------------------------------------------------

def _contexts(node: dict) -> list[dict]:
    commits = (node.get("commits") or {}).get("nodes") or []
    rollup = (commits[0].get("commit") or {}).get("statusCheckRollup") if commits else None
    return ((rollup or {}).get("contexts") or {}).get("nodes") or []


def _checks(node: dict) -> tuple[str, list[str], bool]:
    """(checks text, names of failing checks, whether a workflow run waits for a maintainer)."""
    contexts = _contexts(node)
    if not contexts:
        return "none", [], False
    failing, approval, running = [], False, False
    for c in contexts:
        name = c.get("name") or c.get("context") or "unnamed"
        if name == APPROVAL_GATE:
            continue
        if c.get("__typename") == "StatusContext":
            failing += [name] if c.get("state") in ("FAILURE", "ERROR") else []
            running = running or c.get("state") == "PENDING"
        elif c.get("conclusion") in FAILED:
            failing.append(name)
        elif c.get("conclusion") == "ACTION_REQUIRED":
            approval = True
        elif c.get("status") != "COMPLETED":
            running = True
    if failing:
        return "failing: " + ", ".join(failing), failing, approval
    if approval:
        return "needs a maintainer to approve the run", [], True
    return ("running", [], False) if running else ("passing", [], False)


def _reviews(node: dict) -> tuple[list[str], list[str]]:
    """Approvers and change-requesters, by each reviewer's latest decisive review."""
    latest: dict[str, str] = {}
    for review in (node.get("reviews") or {}).get("nodes") or []:
        who = (review.get("author") or {}).get("login")
        if who and review.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest[who] = review["state"]
    return ([w for w, s in latest.items() if s == "APPROVED"], [w for w, s in latest.items() if s == "CHANGES_REQUESTED"])


def _approvals_text(decision: str | None, approved: list[str], changes: list[str]) -> str:
    parts = [f"approved by {len(approved)}"] if approved else []
    if changes:
        parts.append("changes requested")
    if parts:
        return ", ".join(parts)
    return "none yet" if decision == "REVIEW_REQUIRED" else "not required"


def _blocker(node: dict, checks: str, failing: list[str], needs_run_approval: bool, changes: list[str], gate_failing: bool = False) -> tuple[str, str]:
    """(kind, text) of the first thing in the way."""
    state, merge = node.get("mergeStateStatus"), node.get("mergeable")
    decision = node.get("reviewDecision")
    if node.get("isDraft"):
        return "Draft", "Draft"
    if merge == "CONFLICTING" or state == "DIRTY":
        return "Merge conflicts", "Merge conflicts with the base branch"
    if decision == "CHANGES_REQUESTED" or changes:
        return "Changes requested", "Changes requested by " + (", ".join(changes) if changes else "a reviewer")
    if failing:
        return "Failing check", "Failing check: " + ", ".join(failing)
    if needs_run_approval:
        return "Workflow run needs approval", "A maintainer must approve the workflow run"
    if decision == "REVIEW_REQUIRED" or gate_failing:
        return "Needs review", "Needs an approving review"
    if checks == "running":
        return "Checks running", "Checks still running"
    if state == "BEHIND":
        return "Branch behind", "Branch is behind the base branch"
    if merge == "UNKNOWN" or state == "UNKNOWN":
        return "Mergeability unknown", "GitHub has not computed mergeability yet"
    if state == "BLOCKED":
        return "Branch rules", "Blocked by branch rules (a required check or review is missing)"
    return "None", CAN_MERGE


def _gate_failing(node: dict) -> bool:
    return any((c.get("name") or c.get("context")) == APPROVAL_GATE and (c.get("conclusion") in FAILED or c.get("state") in ("FAILURE", "ERROR"))
               for c in _contexts(node))


def classify(repo: str, node: dict) -> dict:
    checks, failing, needs_run_approval = _checks(node)
    approved, changes = _reviews(node)
    kind, blocker = _blocker(node, checks, failing, needs_run_approval, changes, _gate_failing(node))
    labels = [(label or {}).get("name", "") for label in (node.get("labels") or {}).get("nodes") or []]
    return {
        "repo": repo, "number": node["number"], "title": node.get("title") or "", "url": node.get("url") or f"https://github.com/{repo}/pull/{node['number']}",
        "owner": (node.get("author") or {}).get("login") or "unknown",
        "merge": {"MERGEABLE": "mergeable", "CONFLICTING": "conflicts"}.get(node.get("mergeable") or "", "unknown"),
        "checks": checks, "approvals": _approvals_text(node.get("reviewDecision"), approved, changes),
        "maclab": "unknown", "kind": kind, "blocker": blocker, "can_merge": blocker == CAN_MERGE,
        "rc_blocker": "yes" if any(RC_LABEL.match(name) for name in labels) else "no",
        "updated": (node.get("updatedAt") or "")[:10],
    }


def rows_from(data: dict) -> list[dict]:
    rows = [classify(repo, node) for repo, nodes in data.items() for node in nodes or []]
    return sorted(rows, key=lambda r: (not r["can_merge"], r["repo"], r["number"]))


# -- output ---------------------------------------------------------------------------------

COLUMNS = (("PR", None), ("Title", "title"), ("Owner", "owner"), ("Merge", "merge"), ("Checks", "checks"), ("Approvals", "approvals"),
           ("Maclab", "maclab"), ("Blocking item", "blocker"), ("RC blocker", "rc_blocker"), ("Updated", "updated"))


def _table(rows: list[dict]) -> str:
    head = "".join(f"<th>{name}</th>" for name, _ in COLUMNS)
    body = []
    for r in rows:
        cells = [f'<td><a href="{html.escape(r["url"], quote=True)}">{html.escape(r["repo"])}#{r["number"]}</a></td>']
        cells += [f"<td>{html.escape(str(r[key]))}</td>" for _, key in COLUMNS[1:]]
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def render_html(rows: list[dict], generated: str, unreadable: Sequence[str] = ()) -> str:
    ready = [r for r in rows if r["can_merge"]]
    rest = [r for r in rows if not r["can_merge"]]
    parts = [
        "<!doctype html><meta charset=utf-8><title>Omarchy M pull requests</title>",
        "<style>body{font:14px system-ui;margin:2rem}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px 8px;text-align:left;vertical-align:top}th{background:#eee}</style>",
        "<h1>Omarchy M pull requests</h1>",
        f"<p>{len(rows)} open. Refreshed hourly. Last refresh: {html.escape(generated)}.</p>",
    ]
    if not rows:
        parts.append("<p>No open pull requests were read.</p>")
    else:
        parts += [f"<h2>Can merge now ({len(ready)})</h2>", _table(ready) if ready else "<p>None.</p>",
                  f"<h2>Everything else ({len(rest)})</h2>", _table(rest) if rest else "<p>None.</p>"]
    if unreadable:
        parts.append("<p>Not read this time: " + ", ".join(html.escape(r) for r in unreadable) + ".</p>")
    parts.append("<p>How to read it. Blocking item: the first thing in the way, in this order: draft, conflicts, requested changes, failing check, "
                 "workflow run waiting for a maintainer, missing review, running checks, branch behind, branch rules. RC blocker: yes when the PR carries a "
                 "label named rc, rc-blocker, release blocker or blocker. Can merge now: GitHub reports the PR clean, with no conflicts and every required check "
                 "and review satisfied; a repo that requires no review shows approvals as not required. Maclab: unknown, because maclab's job records are not public.</p>")
    return "\n".join(parts) + "\n"


def short(repo: str) -> str:
    """A short repo name for a short message: omacom/omarchy-mac-installer -> installer, aurora-silicon/aurora-installer -> silicon-aurora-installer."""
    owner, name = repo.split("/", 1)
    return ("silicon-" if owner == "aurora-silicon" else "") + name.replace("omarchy-mac-", "")


def ref(r: dict) -> str:
    return f"{short(r['repo'])} {r['number']}"


def _listed(rows: list[dict]) -> str:
    """The first MAX_LISTED PRs, one repo name per run of numbers: linux-aurora 110, 146; mesa 5."""
    groups: dict[str, list[str]] = {}
    for r in rows[:MAX_LISTED]:
        groups.setdefault(short(r["repo"]), []).append(str(r["number"]))
    text = "; ".join(f"{name} {', '.join(numbers)}" for name, numbers in groups.items())
    return text + (f", and {len(rows) - MAX_LISTED} more" if len(rows) > MAX_LISTED else "")


def render_summary(rows: list[dict], url: str) -> str:
    """The once-a-day text: what can merge, what waits on a maintainer (by PR), what waits on its author (by count)."""
    if not rows:
        return f"[Joshua's agent] No open pull requests were read on the Omarchy M board. Board: {url}\n"
    ready = [r for r in rows if r["can_merge"]]
    stuck = [r for r in rows if not r["can_merge"]]
    lines = [f"[Joshua's agent] There are {len(rows)} open pull requests on the Omarchy M board. {len(ready)} can merge now. {len(stuck)} are stuck.",
             "Can merge now: " + (_listed(ready) if ready else "none") + "."]
    for kind in MAINTAINER_KINDS:
        waiting = [r for r in stuck if r["kind"] == kind]
        if waiting:
            lines.append(f"{kind} ({len(waiting)}): {_listed(waiting)}.")
    others = Counter(r["kind"] for r in stuck if r["kind"] not in MAINTAINER_KINDS).most_common()
    if others:
        lines.append("Waiting on the author or a run: " + ", ".join(f"{kind} {count}" for kind, count in others) + ".")
    lines.append(f"Each PR and what it needs: {url}")
    return "\n".join(lines) + "\n"


# -- reading GitHub -------------------------------------------------------------------------

def gh_graphql(owner: str, name: str, after: str | None, size: int) -> dict:
    args = ["gh", "api", "graphql", "-f", f"query={QUERY}", "-F", f"owner={owner}", "-F", f"name={name}", "-F", f"size={size}"]
    if after:
        args += ["-F", f"after={after}"]
    out = subprocess.run(args, capture_output=True, text=True, timeout=120, check=True).stdout
    return json.loads(out)


def fetch(repos, run: Callable[[str, str, str | None, int], dict] = gh_graphql) -> dict:
    """{repo: [PR nodes]}, or None for a repo that could not be read (so the page can say so)."""
    data: dict = {}
    for repo in repos:
        owner, name = repo.split("/", 1)
        try:
            data[repo] = _read_repo(owner, name, run)
        except PrivateRepo:
            print("skipped a repo that is not public", file=sys.stderr)
    return data


class PrivateRepo(Exception):
    """The board is public: a private repo is never read into it, and never named on it."""


def _read_repo(owner: str, name: str, run: Callable[[str, str, str | None, int], dict]) -> list | None:
    for size in (PAGE_SIZE, SMALL_PAGE_SIZE):
        nodes, after = [], None
        try:
            while True:
                repository = run(owner, name, after, size)["data"]["repository"]
                if repository.get("isPrivate") is not False:  # private, or not stated: refuse
                    raise PrivateRepo
                page = repository["pullRequests"]
                nodes += page["nodes"]
                if not page["pageInfo"]["hasNextPage"]:
                    return nodes
                after = page["pageInfo"]["endCursor"]
        except (subprocess.SubprocessError, KeyError, TypeError, ValueError):
            continue
    return None


def _same_as_saved(out: str, rows: list[dict], unreadable: list[str]) -> bool:
    try:
        with open(os.path.join(out, "board.json"), encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return False
    return saved.get("rows") == rows and saved.get("unreadable") == unreadable


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--fixture", help="read this saved data (repo -> PR nodes) instead of GitHub")
    ap.add_argument("--save", help="also save the data read from GitHub to this file")
    ap.add_argument("--out", required=True)
    ap.add_argument("--url", default="")
    ap.add_argument("--now", default=None)
    ap.add_argument("--only-if-changed", action="store_true", help="write nothing when the rows and the unreadable repos match the board.json already in --out")
    ap.add_argument("--repos", default=",".join(REPOS))
    args = ap.parse_args(argv)
    if args.fixture:
        with open(args.fixture, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = fetch([r for r in args.repos.split(",") if r])
        if args.save:
            with open(args.save, "w", encoding="utf-8") as f:
                json.dump(data, f)
    rows = rows_from(data)
    unreadable = sorted(repo for repo, nodes in data.items() if nodes is None)
    generated = args.now or time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    if args.only_if_changed and _same_as_saved(args.out, rows, unreadable):
        return 0
    os.makedirs(args.out, exist_ok=True)
    outputs = {"index.html": render_html(rows, generated, unreadable), "summary.txt": render_summary(rows, args.url),
               "board.json": json.dumps({"generated": generated, "rows": rows, "unreadable": unreadable}, indent=1) + "\n"}
    for name, text in outputs.items():
        with open(os.path.join(args.out, name), "w", encoding="utf-8") as f:
            f.write(text)
    return 1 if rows == [] and unreadable else 0


if __name__ == "__main__":
    sys.exit(main())
