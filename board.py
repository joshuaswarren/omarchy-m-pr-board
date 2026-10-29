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
OUTPUT_FILES = ("index.html", "summary.txt", "daily.txt", "board.json")
MAX_DAILY_STUCK = 20  # stuck team PRs named one by one in the daily text
GROUPS = (("team", "Omarchy M team"), ("contributor", "Existing contributors"), ("other", "Everyone else"))
MAX_MERGED_PAGES = 60  # 100 per page: more than 6000 merged PRs in one repo is not read to the end
TEAM_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "team.json")
MAINTAINER_KINDS = ("Needs review", "Workflow run needs approval", "Branch rules")  # what a maintainer or reviewer must do
PAGE_SIZE, SMALL_PAGE_SIZE = 10, 3  # a big repo's check lists can make GitHub answer 502 or 'something went wrong'

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
        reviews(last: 20) { nodes { state author { login } } }
      }
    }
  }
}
"""

# The checks come in a second query over the same page: GitHub answers "something went wrong" for one big repo
# when they are in the same query as the rest (measured in the workflow), though each part alone works.
CHECKS_QUERY = """
query($owner: String!, $name: String!, $after: String, $size: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: OPEN, first: $size, after: $after, orderBy: {field: CREATED_AT, direction: ASC}) {
      nodes {
        number
        commits(last: 1) { nodes { commit { statusCheckRollup { state contexts(first: 30) { nodes {
          __typename ... on CheckRun { name conclusion status } ... on StatusContext { context state } } } } } } }
      }
    }
  }
}
"""


# -- one PR -> one row ----------------------------------------------------------------------

MERGED_QUERY = """
query($owner: String!, $name: String!, $after: String, $size: Int!) {
  repository(owner: $owner, name: $name) {
    isPrivate
    pullRequests(states: MERGED, first: $size, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes { author { login } }
    }
  }
}
"""


NUMBERS_QUERY = """
query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    isPrivate
    pullRequests(states: OPEN, first: 100, after: $after, orderBy: {field: CREATED_AT, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes { number }
    }
  }
}
"""

# One PR with everything: the last resort when a whole page will not come, so that one bad PR costs one row, not the repo.
SINGLE_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    isPrivate
    pullRequest(number: $number) {
      number title url isDraft mergeable mergeStateStatus reviewDecision updatedAt baseRefName
      author { login }
      labels(first: 10) { nodes { name } }
      reviews(last: 20) { nodes { state author { login } } }
      commits(last: 1) { nodes { commit { statusCheckRollup { state contexts(first: 30) { nodes {
        __typename ... on CheckRun { name conclusion status } ... on StatusContext { context state } } } } } } }
    }
  }
}
"""


# The same without mergeable and mergeStateStatus, which GitHub works out on demand: the four PRs the full query could not return in the
# workflow were all freshly updated, and a PR whose merge state is being recomputed is the likely cause.
SINGLE_LIGHT_QUERY = SINGLE_QUERY.replace(" mergeable mergeStateStatus", "")

LAST_RESORT_SECONDS = 600  # the whole one-by-one read of one repo; the PRs not reached by then come back as Unread


def load_team(path: str = TEAM_FILE) -> set[str]:
    """The lower-case GitHub logins of the Omarchy M team: a plain JSON list."""
    with open(path, encoding="utf-8") as f:
        return {login.lower() for login in json.load(f)}


def _contexts(node: dict) -> list[dict]:
    commits = (node.get("commits") or {}).get("nodes") or []
    rollup = (commits[0].get("commit") or {}).get("statusCheckRollup") if commits else None
    return ((rollup or {}).get("contexts") or {}).get("nodes") or []


def _checks(node: dict) -> tuple[str, list[str], bool]:
    """(checks text, names of failing checks, whether a workflow run waits for a maintainer)."""
    contexts = _contexts(node)
    if not contexts:
        return "none", [], False
    failing, approval, running, gate_failed = [], False, False, False
    for c in contexts:
        name = c.get("name") or c.get("context") or "unnamed"
        if name == APPROVAL_GATE:
            gate_failed = gate_failed or c.get("conclusion") in FAILED or c.get("state") in ("FAILURE", "ERROR")
            continue
        if c.get("__typename") == "StatusContext":
            failing += [name] if c.get("state") in ("FAILURE", "ERROR") else []
            running = running or c.get("state") == "PENDING"
        elif c.get("conclusion") in FAILED:
            failing += [name] if name not in failing else []
        elif c.get("conclusion") == "ACTION_REQUIRED":
            approval = True
        elif c.get("status") != "COMPLETED":
            running = True
    if failing:
        return "failing: " + ", ".join(failing), failing, approval
    if approval:
        return "needs a maintainer to approve the run", [], True
    if running:
        return "running", [], False
    return ("passing (approval check failing)" if gate_failed else "passing"), [], False


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


def _unread_row(repo: str, node: dict) -> dict:
    return {"group": "other", "repo": repo, "number": node["number"], "title": "(details unavailable)", "url": f"https://github.com/{repo}/pull/{node['number']}",
            "owner": "unknown", "merge": "unknown", "checks": "unknown", "approvals": "unknown", "maclab": "unknown", "kind": "Unread",
            "blocker": "GitHub would not return this PR's details", "can_merge": False, "rc_blocker": "no", "updated": ""}


def classify(repo: str, node: dict, team: set | None = None, merged: dict | None = None) -> dict:
    if node.get("_unread"):
        return _unread_row(repo, node)
    checks, failing, needs_run_approval = _checks(node)
    approved, changes = _reviews(node)
    kind, blocker = _blocker(node, checks, failing, needs_run_approval, changes, _gate_failing(node))
    labels = [(label or {}).get("name", "") for label in (node.get("labels") or {}).get("nodes") or []]
    login = ((node.get("author") or {}).get("login") or "").lower()
    group = "team" if login and login in (team or ()) else "contributor" if login and login in ((merged or {}).get(repo) or ()) else "other"
    return {
        "group": group, "repo": repo, "number": node["number"], "title": node.get("title") or "", "url": node.get("url") or f"https://github.com/{repo}/pull/{node['number']}",
        "owner": (node.get("author") or {}).get("login") or "unknown",
        "merge": {"MERGEABLE": "mergeable", "CONFLICTING": "conflicts"}.get(node.get("mergeable") or "", "unknown"),
        "checks": checks, "approvals": _approvals_text(node.get("reviewDecision"), approved, changes),
        "maclab": "unknown", "kind": kind, "blocker": blocker, "can_merge": blocker == CAN_MERGE,
        "rc_blocker": "yes" if any(RC_LABEL.match(name) for name in labels) else "no",
        "updated": (node.get("updatedAt") or "")[:10],
    }


def rows_from(data: dict, team: set | None = None, merged: dict | None = None) -> list[dict]:
    rows = [classify(repo, node, team, merged) for repo, nodes in data.items() for node in nodes or []]
    order = [key for key, _ in GROUPS]
    return sorted(rows, key=lambda r: (order.index(r["group"]), not r["can_merge"], r["repo"], r["number"]))


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
    parts = [
        "<!doctype html><meta charset=utf-8><title>Omarchy M pull requests</title>",
        "<style>body{font:14px system-ui;margin:2rem}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px 8px;text-align:left;vertical-align:top}th{background:#eee}</style>",
        "<h1>Omarchy M pull requests</h1>",
        f"<p>{len(rows)} open. Checked hourly. Last change: {html.escape(generated)}.</p>",
    ]
    if not rows:
        parts.append("<p>No open pull requests were read.</p>")
    else:
        for key, title in GROUPS:
            part = [r for r in rows if r["group"] == key]
            ready = sum(r["can_merge"] for r in part)
            parts += [f"<h2>{title} ({len(part)} open, {ready} can merge now)</h2>", _table(part) if part else "<p>None.</p>"]
    if unreadable:
        parts.append("<p>Not read this time: " + ", ".join(html.escape(r) for r in unreadable) + ".</p>")
    parts.append("<p>How to read it. Blocking item: the first thing in the way, in this order: draft, conflicts, requested changes, failing check, "
                 "workflow run waiting for a maintainer, missing review, running checks, branch behind, branch rules. RC blocker: yes when the PR carries a "
                 "label named rc, rc-blocker, release blocker or blocker. Can merge now: GitHub reports the PR clean, with no conflicts and every required check "
                 "and review satisfied; a repo that requires no review shows approvals as not required. Maclab: unknown, because maclab's job records are not public. "
                 "Sections: the Omarchy M team, then people with an earlier merged PR in the same repo, then everyone else.</p>")
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
    others = Counter(r["kind"] for r in stuck if r["kind"] not in MAINTAINER_KINDS and r["kind"] != "Unread").most_common()
    if others:
        lines.append("Waiting on the author or a run: " + ", ".join(f"{kind} {count}" for kind, count in others) + ".")
    unread = [r for r in stuck if r["kind"] == "Unread"]
    if unread:
        lines.append(f"GitHub would not return the details of {len(unread)}: {_listed(unread)}.")
    counts = {key: sum(r["group"] == key for r in rows) for key, _ in GROUPS}
    lines.append(f"Team {counts['team']}, existing contributors {counts['contributor']}, everyone else {counts['other']}.")
    lines.append(f"Each PR and what it needs: {url}")
    return "\n".join(lines) + "\n"


def render_daily(rows: list[dict], url: str) -> str:
    """The weekday text: what can merge now, and the one thing each stuck team PR needs (GitHub logins only)."""
    if not rows:
        return f"[Joshua's agent] No open pull requests were read on the Omarchy M board today. Board: {url}\n"
    ready = [r for r in rows if r["can_merge"]]
    team_stuck = [r for r in rows if r["group"] == "team" and not r["can_merge"]]
    drafts = [r for r in team_stuck if r["kind"] == "Draft"]
    stuck = sorted((r for r in team_stuck if r["kind"] != "Draft"), key=lambda r: (r["kind"] not in MAINTAINER_KINDS, r["repo"], r["number"]))
    lines = [f"[Joshua's agent] Omarchy M pull requests today: {len(ready)} can merge now, and {len(team_stuck)} team PRs are stuck.",
             "Can merge now: " + (_listed(ready) if ready else "none") + "."]
    if stuck:
        lines.append("Stuck team PRs, and the one thing each needs. Those waiting on a review or a maintainer come first:")
        lines += [f"PR {ref(r)} by {r['owner']}: {r['blocker']}." for r in stuck[:MAX_DAILY_STUCK]]
        if len(stuck) > MAX_DAILY_STUCK:
            lines.append(f"...and {len(stuck) - MAX_DAILY_STUCK} more on the board.")
    if drafts:
        lines.append(f"{len(drafts)} team drafts are not listed.")
    lines.append(f"All PRs and what each needs: {url}")
    return "\n".join(lines) + "\n"


# -- reading GitHub -------------------------------------------------------------------------

def gh_graphql(owner: str, name: str, after: str | None, size: int, query: str = QUERY) -> dict:
    args = ["gh", "api", "graphql", "-f", f"query={query}", "-F", f"owner={owner}", "-F", f"name={name}", "-F", f"size={size}"]
    if after:
        args += ["-F", f"after={after}"]
    out = subprocess.run(args, capture_output=True, text=True, timeout=120, check=True).stdout
    return json.loads(out)


def gh_graphql_checks(owner: str, name: str, after: str | None, size: int) -> dict:
    return gh_graphql(owner, name, after, size, CHECKS_QUERY)


def gh_numbers(owner: str, name: str, after: str | None) -> dict:
    args = ["gh", "api", "graphql", "-f", f"query={NUMBERS_QUERY}", "-F", f"owner={owner}", "-F", f"name={name}"]
    if after:
        args += ["-F", f"after={after}"]
    return json.loads(subprocess.run(args, capture_output=True, text=True, timeout=120, check=True).stdout)


def gh_single(owner: str, name: str, number: int, light: bool = False) -> dict:
    query = SINGLE_LIGHT_QUERY if light else SINGLE_QUERY
    args = ["gh", "api", "graphql", "-f", f"query={query}", "-F", f"owner={owner}", "-F", f"name={name}", "-F", f"number={number}"]
    return json.loads(subprocess.run(args, capture_output=True, text=True, timeout=120, check=True).stdout)


def fetch(repos, run: Callable[[str, str, str | None, int], dict] | None = None, checks_run: Callable[[str, str, str | None, int], dict] | None = None,
          numbers_run: Callable | None = None, single_run: Callable | None = None) -> dict:
    """{repo: [PR nodes]}, or None for a repo that could not be read (so the page can say so)."""
    data: dict = {}
    for repo in repos:
        owner, name = repo.split("/", 1)
        live = run is None
        try:
            data[repo] = _read_repo(owner, name, run or gh_graphql, checks_run if run else gh_graphql_checks,
                                    numbers_run or (gh_numbers if live else None), single_run or (gh_single if live else None))
        except PrivateRepo:
            print("skipped a repo that is not public", file=sys.stderr)
    return data


def gh_graphql_merged(owner: str, name: str, after: str | None, size: int) -> dict:
    args = ["gh", "api", "graphql", "-f", f"query={MERGED_QUERY}", "-F", f"owner={owner}", "-F", f"name={name}", "-F", f"size={size}"]
    if after:
        args += ["-F", f"after={after}"]
    return json.loads(subprocess.run(args, capture_output=True, text=True, timeout=120, check=True).stdout)


def fetch_merged(repos, run: Callable[[str, str, str | None, int], dict] = gh_graphql_merged) -> dict:
    """{repo: set of lower-case logins with a merged PR there}, or None for a repo whose history could not be read. A private repo is dropped."""
    merged: dict = {}
    for repo in repos:
        owner, name = repo.split("/", 1)
        logins: set[str] = set()
        after = None
        try:
            for _ in range(MAX_MERGED_PAGES):
                repository = run(owner, name, after, 100)["data"]["repository"]
                if repository is None:
                    raise KeyError("repository")
                if repository.get("isPrivate") is not False:
                    raise PrivateRepo
                page = repository["pullRequests"]
                logins |= {n["author"]["login"].lower() for n in page["nodes"] if n.get("author") and n["author"].get("login")}
                if not page["pageInfo"]["hasNextPage"]:
                    break
                after = page["pageInfo"]["endCursor"]
            merged[repo] = logins
        except PrivateRepo:
            print("skipped a repo that is not public", file=sys.stderr)
        except (subprocess.SubprocessError, KeyError, TypeError, ValueError):
            merged[repo] = None
    return merged


class PrivateRepo(Exception):
    """The board is public: a private repo is never read into it, and never named on it."""


def _with_checks(page_nodes: list, checks: dict) -> None:
    """Put each PR's commits (from the checks query over the same page) on its node; a PR the checks query lacks fails the page."""
    commits = {n["number"]: n.get("commits") for n in checks["data"]["repository"]["pullRequests"]["nodes"]}
    for node in page_nodes:
        node["commits"] = commits[node["number"]]


def _read_one_by_one(owner: str, name: str, numbers_run: Callable, single_run: Callable) -> list | None:
    """The last resort: list the open PR numbers, then read each PR alone. A PR that will not come becomes an 'Unread' row, and its number is logged."""
    numbers, after = [], None
    try:
        while True:
            repository = numbers_run(owner, name, after)["data"]["repository"]
            if repository is None or repository.get("isPrivate") is not False:
                raise PrivateRepo
            page = repository["pullRequests"]
            numbers += [n["number"] for n in page["nodes"]]
            if not page["pageInfo"]["hasNextPage"]:
                break
            after = page["pageInfo"]["endCursor"]
    except (subprocess.SubprocessError, KeyError, TypeError, ValueError) as error:
        print(f"{owner}/{name}: listing the open PRs failed: {type(error).__name__}: {_brief(error)}", file=sys.stderr)
        return None
    nodes = []
    deadline = time.monotonic() + LAST_RESORT_SECONDS
    for number in numbers:
        if time.monotonic() > deadline:
            nodes.append({"number": number, "_unread": True})
            continue
        node = _read_one(owner, name, number, single_run)
        if node != {}:
            nodes.append(node)
    return nodes


def _read_one(owner: str, name: str, number: int, single_run: Callable) -> dict:
    """One PR: in full, else without the merge state GitHub computes on demand (shown as unknown), else an Unread stub. {} when it is gone."""
    for light in (False, True):
        try:
            repository = single_run(owner, name, number, light)["data"]["repository"]
            if repository is None or repository.get("isPrivate") is not False:
                raise PrivateRepo
            pr = repository["pullRequest"]
            if pr is None:
                return {}  # closed or deleted between the list and the read
            if light:
                pr = {**pr, "mergeable": "UNKNOWN", "mergeStateStatus": "UNKNOWN"}
            return pr
        except (subprocess.SubprocessError, KeyError, TypeError, ValueError) as error:
            print(f"{owner}/{name}#{number}: {'light ' if light else ''}read failed: {type(error).__name__}: {_brief(error)}", file=sys.stderr)
    return {"number": number, "_unread": True}


def _read_repo(owner: str, name: str, run: Callable[[str, str, str | None, int], dict], checks_run: Callable[[str, str, str | None, int], dict] | None = None,
               numbers_run: Callable | None = None, single_run: Callable | None = None) -> list | None:
    for size in (PAGE_SIZE, SMALL_PAGE_SIZE):
        nodes, after = [], None
        try:
            while True:
                repository = run(owner, name, after, size)["data"]["repository"]
                if repository is None:
                    raise KeyError("repository")
                if repository.get("isPrivate") is not False:  # private, or not stated: refuse
                    raise PrivateRepo
                page = repository["pullRequests"]
                if checks_run:
                    _with_checks(page["nodes"], checks_run(owner, name, after, size))
                nodes += page["nodes"]
                if not page["pageInfo"]["hasNextPage"]:
                    return nodes
                after = page["pageInfo"]["endCursor"]
        except (subprocess.SubprocessError, KeyError, TypeError, ValueError) as error:
            print(f"{owner}/{name}: page size {size} failed: {type(error).__name__}: {_brief(error)}", file=sys.stderr)
            continue
    if numbers_run and single_run:
        return _read_one_by_one(owner, name, numbers_run, single_run)
    return None


def _brief(error: Exception) -> str:
    """One short line about an error, from gh's stderr when there is one."""
    text = getattr(error, "stderr", None) or str(error)
    return " ".join(str(text).split())[:120]


def _worse_than_saved(out: str, unreadable: list[str]) -> bool:
    try:
        with open(os.path.join(out, "board.json"), encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return False
    return len(unreadable) > len(saved.get("unreadable", []))


def _same_as_saved(out: str, rows: list[dict], unreadable: list[str]) -> bool:
    try:
        with open(os.path.join(out, "board.json"), encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return False
    complete = all(os.path.exists(os.path.join(out, name)) for name in OUTPUT_FILES)  # a file added since the last write must be written
    return complete and saved.get("rows") == rows and saved.get("unreadable") == unreadable


def _load_data(args) -> tuple[dict, dict]:
    """(PR nodes by repo, merged-PR logins by repo): from a fixture, or from GitHub (and then saved when asked)."""
    if args.fixture:
        with open(args.fixture, encoding="utf-8") as f:
            saved = json.load(f)
        prs, merged = (saved["prs"], saved.get("merged", {})) if "prs" in saved else (saved, {})
    else:
        repos = [r for r in args.repos.split(",") if r]
        prs, merged = fetch(repos), fetch_merged(repos)
        if args.save:
            with open(args.save, "w", encoding="utf-8") as f:
                json.dump({"prs": prs, "merged": {repo: None if logins is None else sorted(logins) for repo, logins in merged.items()}}, f)
    return prs, {repo: None if logins is None else set(logins) for repo, logins in merged.items()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--fixture", help="read this saved data ({prs, merged}, or just repo -> PR nodes) instead of GitHub")
    ap.add_argument("--save", help="also save the data read from GitHub to this file")
    ap.add_argument("--team", default=TEAM_FILE, help="the team file (default: team.json beside board.py)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--url", default="")
    ap.add_argument("--now", default=None)
    ap.add_argument("--only-if-changed", action="store_true", help="write nothing when the rows and the unreadable repos match the board.json already in --out")
    ap.add_argument("--keep-last-good", action="store_true", help="write nothing when more repos are unreadable than in the board.json already in --out")
    ap.add_argument("--repos", default=",".join(REPOS))
    args = ap.parse_args(argv)
    data, merged = _load_data(args)
    rows = rows_from(data, load_team(args.team), merged)
    unreadable = sorted(repo for repo, nodes in data.items() if nodes is None)
    generated = args.now or time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    if args.only_if_changed and _same_as_saved(args.out, rows, unreadable):
        return 0
    if args.keep_last_good and _worse_than_saved(args.out, unreadable):
        print("more repos are unreadable than on the saved page: keeping the saved page", file=sys.stderr)
        return 0
    os.makedirs(args.out, exist_ok=True)
    outputs = {"index.html": render_html(rows, generated, unreadable), "summary.txt": render_summary(rows, args.url), "daily.txt": render_daily(rows, args.url),
               "board.json": json.dumps({"generated": generated, "rows": rows, "unreadable": unreadable}, indent=1) + "\n"}
    for name, text in outputs.items():
        with open(os.path.join(args.out, name), "w", encoding="utf-8") as f:
            f.write(text)
    return 1 if rows == [] and unreadable else 0


if __name__ == "__main__":
    sys.exit(main())
