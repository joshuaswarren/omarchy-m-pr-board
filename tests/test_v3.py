"""Board v3: who each PR waits on, the repo label and filter, the maclab chip, the evidence link, and the default view."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import board
import maclab_export
from test_board import check, node, rollup

SHA = "a" * 40
OTHER = "b" * 40


def request(*logins: str, team: str | None = None) -> dict:
    nodes = [{"requestedReviewer": {"__typename": "User", "login": login}} for login in logins]
    if team:
        nodes.append({"requestedReviewer": {"__typename": "Team", "slug": team}})
    return {"nodes": nodes}


def classify(repo: str = "o/r", maclab: dict | None = None, **over) -> dict:
    return board.classify(repo, node(1, **over), maclab=maclab)


class WaitingOnTest(unittest.TestCase):
    def test_a_clean_pr_waits_on_a_maintainer_to_merge(self):
        self.assertEqual(classify()["waiting"], "Waiting on a maintainer to merge")

    def test_a_pr_that_needs_review_names_the_requested_reviewers(self):
        r = classify(reviewDecision="REVIEW_REQUIRED", mergeStateStatus="BLOCKED", reviewRequests=request("carol", "dave"))
        self.assertEqual(r["waiting"], "Waiting on carol and dave for an approving review")

    def test_a_requested_team_is_named_by_its_slug(self):
        r = classify(reviewDecision="REVIEW_REQUIRED", mergeStateStatus="BLOCKED", reviewRequests=request("carol", team="core"))
        self.assertEqual(r["waiting"], "Waiting on carol and core for an approving review")

    def test_more_than_three_reviewers_are_counted_not_listed(self):
        r = classify(reviewDecision="REVIEW_REQUIRED", mergeStateStatus="BLOCKED", reviewRequests=request("a", "b", "c", "d", "e"))
        self.assertEqual(r["waiting"], "Waiting on a, b, c and 2 more for an approving review")

    def test_without_a_requested_reviewer_it_is_a_maintainer(self):
        r = classify(reviewDecision="REVIEW_REQUIRED", mergeStateStatus="BLOCKED")
        self.assertEqual(r["waiting"], "Waiting on a maintainer for an approving review")

    def test_a_workflow_run_that_needs_approval_waits_on_the_reviewers(self):
        r = classify(commits=rollup("PENDING", check("build", "ACTION_REQUIRED")), mergeStateStatus="BLOCKED", reviewDecision="REVIEW_REQUIRED", reviewRequests=request("carol"))
        self.assertEqual(r["waiting"], "Waiting on carol to approve the workflow run")

    def test_fixes_wait_on_the_author(self):
        self.assertEqual(classify(mergeable="CONFLICTING", mergeStateStatus="DIRTY")["waiting"], "Waiting on alice to fix the conflicts")
        self.assertEqual(classify(commits=rollup("FAILURE", check("lint", "FAILURE")))["waiting"], "Waiting on alice to fix the failing check: lint")
        self.assertEqual(classify(mergeStateStatus="BEHIND")["waiting"], "Waiting on alice to update the branch")
        changes = {"nodes": [{"state": "CHANGES_REQUESTED", "author": {"login": "x"}}]}
        self.assertEqual(classify(reviewDecision="CHANGES_REQUESTED", reviews=changes)["waiting"], "Waiting on alice to address the changes x asked for")

    def test_running_checks_wait_on_ci(self):
        r = classify(commits=rollup("PENDING", check("build", None, "IN_PROGRESS")), mergeStateStatus="BLOCKED")
        self.assertEqual(r["waiting"], "Waiting on CI to finish")

    def test_an_unknown_merge_state_and_an_unread_pr_wait_on_github(self):
        self.assertEqual(classify(mergeable="UNKNOWN", mergeStateStatus="UNKNOWN")["waiting"], "Waiting on GitHub to compute the merge state")
        self.assertEqual(board.classify("o/r", {"number": 5, "_unread": True})["waiting"], "Waiting on GitHub to return this PR")

    def test_a_draft_waits_on_its_author(self):
        self.assertEqual(classify(isDraft=True)["waiting"], "Waiting on alice to mark it ready")

    def test_every_kind_has_a_waiting_line(self):
        from board import KIND_STATE, WAITING
        self.assertEqual(set(WAITING), set(KIND_STATE))

    def test_a_check_name_from_a_fork_is_escaped_in_the_waiting_line(self):
        html = board.render_html([classify(commits=rollup("FAILURE", check("<script>alert(1)</script>", "FAILURE")))], "t")
        self.assertNotIn("<script>alert(1)", html)
        self.assertIn("&lt;script&gt;alert(1)", html)

    def test_a_repo_name_is_escaped_in_the_row_and_the_menu(self):
        html = board.render_html([board.classify('o/"><img src=x>', node(1))], "t")
        self.assertNotIn("<img src=x>", html)
        self.assertNotIn('data-repo="o/"', html)

    def test_the_row_shows_the_one_line_and_no_second_blocking_line(self):
        html = board.render_html([classify(mergeable="CONFLICTING", mergeStateStatus="DIRTY")], "t")
        self.assertIn("Waiting on alice to fix the conflicts", html)
        self.assertNotIn("Merge conflicts with the base branch", html)


class EvidenceTest(unittest.TestCase):
    def evidence(self, body):
        return classify(body=body)["evidence"]

    def test_a_line_that_starts_with_evidence_gives_its_link(self):
        self.assertEqual(self.evidence("Fixes the thing.\n\nEvidence: https://github.com/o/r/run/1\n"), "https://github.com/o/r/run/1")

    def test_the_other_labels_work_and_case_does_not_matter(self):
        for label in ("proof", "Receipt", "Test report", "RESULTS", "result"):
            self.assertEqual(self.evidence(f"{label}: https://github.com/o/r/x"), "https://github.com/o/r/x", label)

    def test_a_markdown_link_and_a_list_marker_and_bold_are_read(self):
        self.assertEqual(self.evidence("- **Evidence:** [the run](https://github.com/o/r/r/9)."), "https://github.com/o/r/r/9")

    def test_trailing_punctuation_is_not_part_of_the_link(self):
        self.assertEqual(self.evidence("Evidence: https://github.com/o/r/a."), "https://github.com/o/r/a")

    def test_the_first_evidence_line_wins(self):
        self.assertEqual(self.evidence("Proof: https://github.com/o/r/1\nEvidence: https://github.com/o/r/2"), "https://github.com/o/r/1")

    def test_a_link_that_is_not_https_or_carries_a_login_is_refused(self):
        for bad in ("http://github.com/o/r/x", "javascript:alert(1)", "https://user:pw@github.com/x", "ftp://github.com/x"):
            self.assertEqual(self.evidence(f"Evidence: {bad}"), "", bad)

    def test_only_github_com_is_linked(self):
        for good in ("https://github.com/o/r/pull/1", "https://GitHub.com/o/r"):
            self.assertEqual(self.evidence(f"Evidence: {good}"), good, good)
        for bad in ("https://example.org/x", "https://github.com.evil.org/x", "https://evilgithub.com/x", "https://evil.org/github.com", "https://x.github.io.evil.org/",
                    "https://github.com\\@evil.org/x", "https://raw.githubusercontent.com/o/r/x", "https://evil.github.io/phish", "https://github.io/x"):
            self.assertEqual(self.evidence(f"Evidence: {bad}"), "", bad)

    def test_a_link_over_300_characters_is_refused(self):
        self.assertEqual(self.evidence("Evidence: https://github.com/" + "a" * 290), "")
        self.assertEqual(len(self.evidence("Evidence: https://github.com/" + "a" * 250)), 19 + 250)

    def test_a_link_in_running_text_is_not_evidence(self):
        self.assertEqual(self.evidence("See https://github.com/o/r/x for the evidence."), "")

    def test_no_body_no_evidence(self):
        self.assertEqual(classify()["evidence"], "")
        self.assertEqual(self.evidence(None), "")

    def test_the_page_puts_the_link_next_to_the_state(self):
        html = board.render_html([classify(body="Evidence: https://github.com/o/r/x")], "t")
        self.assertRegex(html, r'class="chip ok">ready to merge</span><a class="chip" href="https://github.com/o/r/x"[^>]*>evidence</a>')

    def test_a_link_with_quotes_is_escaped_on_the_page(self):
        html = board.render_html([classify(body='Evidence: https://github.com/o/r/x?a="b"')], "t")
        self.assertNotIn('"b"', html)


class MaclabChipTest(unittest.TestCase):
    JOBS = {SHA: {"passed": 3, "failed": 0}, OTHER: {"passed": 2, "failed": 1}}

    def test_a_head_with_only_passing_jobs_is_passed(self):
        self.assertEqual(classify("omacom/linux-aurora", self.JOBS, headRefOid=SHA)["maclab"], "passed")

    def test_a_head_with_a_failing_job_is_failed(self):
        self.assertEqual(classify("omacom/linux-aurora", self.JOBS, headRefOid=OTHER)["maclab"], "failed")

    def test_a_kernel_pr_with_no_job_is_not_run(self):
        self.assertEqual(classify("omacom/linux-aurora", self.JOBS, headRefOid="c" * 40)["maclab"], "not run")
        self.assertEqual(classify("omacom/linux-aurora", None)["maclab"], "not run")

    def test_other_repos_show_no_chip_unless_a_job_ran_on_the_head(self):
        self.assertEqual(classify("omacom/mesa", self.JOBS, headRefOid="c" * 40)["maclab"], "")
        self.assertEqual(classify("omacom/mesa", self.JOBS, headRefOid=SHA)["maclab"], "passed")

    def test_the_chip_says_how_many_macs_and_has_no_link(self):
        html = board.render_html([classify("omacom/linux-aurora", self.JOBS, headRefOid=OTHER)], "t")
        self.assertIn('<span class="chip bad" title="2 Macs passed, 1 failed">maclab failed</span>', html)
        html = board.render_html([classify("omacom/linux-aurora", self.JOBS, headRefOid=SHA)], "t")
        self.assertIn('<span class="chip ok" title="3 Macs passed">maclab passed</span>', html)
        self.assertIn('>maclab not run</span>', board.render_html([classify("omacom/linux-aurora")], "t"))

    def test_the_row_keeps_the_head_sha_the_chip_is_for(self):
        self.assertEqual(classify(headRefOid=SHA)["sha"], SHA)
        self.assertEqual(classify()["sha"], "")

    def test_a_count_below_zero_or_above_the_cap_is_not_read(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "maclab.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({SHA: {"passed": -1, "failed": 0}, OTHER: {"passed": 1, "failed": board.MAX_MACS + 1}, "c" * 40: {"passed": board.MAX_MACS, "failed": 0}, "d" * 40: {"passed": True, "failed": 0}}, f)
            self.assertEqual(board.load_maclab(path), {"c" * 40: {"passed": board.MAX_MACS, "failed": 0}})

    def test_the_maclab_file_is_read_and_a_bad_or_missing_one_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "maclab.json")
            self.assertEqual(board.load_maclab(path), {})
            with open(path, "w", encoding="utf-8") as f:
                f.write("not json")
            self.assertEqual(board.load_maclab(path), {})
            with open(path, "w", encoding="utf-8") as f:
                json.dump({SHA: {"passed": 1, "failed": 0}, "junk": 5, "short": {"passed": 1, "failed": 0}}, f)
            self.assertEqual(board.load_maclab(path), {SHA: {"passed": 1, "failed": 0}})


def job(device: str, sha: str, state: str = "done", outcome: str | None = "pass", repo: str = "omacom/linux-aurora", created: str = "2026-10-29T15:00:00-07:00") -> dict:
    return {"state": state, "outcome": outcome, "created": created, "spec": {"device": device, "source": f"https://github.com/{repo}/commit/{sha}"}}


class ExportTest(unittest.TestCase):
    def test_each_mac_counts_once_by_its_latest_verdict(self):
        jobs = [job("m3air", SHA, outcome="tests_failed", created="2026-10-29T15:00:00-07:00"), job("m3air", SHA, created="2026-10-29T16:00:00-07:00"), job("neo", SHA)]
        self.assertEqual(maclab_export.summarize(jobs), {SHA: {"passed": 2, "failed": 0}})

    def test_a_failed_test_counts_and_a_job_that_is_not_a_verdict_is_ignored(self):
        jobs = [job("m3air", SHA, outcome="tests_failed"), job("neo", SHA, state="testing", outcome=None), job("m3pro", SHA, outcome="stage_failed"), job("m1", SHA, state="canceled", outcome=None)]
        self.assertEqual(maclab_export.summarize(jobs), {SHA: {"passed": 0, "failed": 1}})

    def test_only_a_commit_of_the_boards_repos_counts(self):
        for repo in ("omacom/private-thing", "someone/linux-aurora", "omacom/linux-aurora-extra"):
            self.assertEqual(maclab_export.summarize([job("m1", SHA, repo=repo)]), {}, repo)
        for repo in board.REPOS:
            self.assertEqual(maclab_export.summarize([job("m1", SHA, repo=repo)]), {SHA: {"passed": 1, "failed": 0}}, repo)

    def test_the_file_is_capped_and_a_commit_maclab_still_lists_is_kept(self):
        saved = {f"{n:040x}": {"passed": 1, "failed": 0} for n in range(maclab_export.MAX_COMMITS + 20)}
        merged = maclab_export.merge(saved, {SHA: {"passed": 2, "failed": 0}})
        self.assertEqual(len(merged), maclab_export.MAX_COMMITS)
        self.assertEqual(merged[SHA], {"passed": 2, "failed": 0})

    def test_only_a_commit_source_counts(self):
        jobs = [{"state": "done", "outcome": "pass", "created": "x", "spec": {"device": "m1", "source": "https://github.com/o/r/a.tar"}}, {"state": "done", "outcome": "pass", "created": "x", "spec": {"device": "m1"}},
                {"state": "done", "outcome": "pass", "created": "x", "spec": {"device": "m1", "source": "https://github.com/o/r/commit/abc"}}]
        self.assertEqual(maclab_export.summarize(jobs), {})

    def test_an_older_answer_is_kept_when_maclab_no_longer_lists_the_job(self):
        saved = {OTHER: {"passed": 1, "failed": 0}, SHA: {"passed": 1, "failed": 0}}
        self.assertEqual(maclab_export.merge(saved, {SHA: {"passed": 3, "failed": 0}}), {OTHER: {"passed": 1, "failed": 0}, SHA: {"passed": 3, "failed": 0}})

    def test_the_file_holds_commits_and_counts_only(self):
        text = maclab_export.dump(maclab_export.summarize([job("m3air", SHA), job("neo", SHA, outcome="tests_failed")]))
        self.assertEqual(json.loads(text), {SHA: {"passed": 1, "failed": 1}})
        self.assertNotIn("m3air", text)
        self.assertNotIn("neo", text)


class NoScriptTest(unittest.TestCase):
    def test_without_script_every_row_is_shown_and_the_menu_is_not(self):
        html = board.render_html([classify(mergeable="CONFLICTING", mergeStateStatus="DIRTY")], "t")
        self.assertIn("<noscript><style>li.pr[hidden]{display:flex!important}", html)
        self.assertIn(".menu{display:none}", html)


class DefaultViewTest(unittest.TestCase):
    def rows(self):
        data = {"omacom/linux-aurora": [node(1), node(2, reviewDecision="REVIEW_REQUIRED", mergeStateStatus="BLOCKED"), node(3, mergeable="CONFLICTING", mergeStateStatus="DIRTY"), node(4, isDraft=True)],
                "omacom/mesa": [node(5, mergeStateStatus="BEHIND")]}
        return board.rows_from(data, {"alice"}, {})

    def test_a_pr_needs_action_now_when_it_waits_on_a_reviewer_or_maintainer(self):
        acts = {r["number"]: r["act"] for r in self.rows()}
        self.assertEqual(acts, {1: True, 2: True, 3: False, 4: False, 5: False})

    def test_the_page_opens_on_needs_action_now_with_drafts_hidden(self):
        html = board.render_html(self.rows(), "t")
        self.assertEqual(len(re.findall(r'<li class="pr[^"]*" [^>]* hidden>', html)), 3)
        self.assertIn('<option value="focus" selected>Needs action now</option>', html)
        self.assertIn('<input type="checkbox" id="drafts">', html)
        self.assertNotIn('<input type="checkbox" id="drafts" checked', html)

    def test_each_row_carries_what_the_filter_needs(self):
        html = board.render_html(self.rows(), "t")
        self.assertIn('data-repo="omacom/mesa"', html)
        self.assertEqual(len(re.findall(r'data-act="1"', html)), 2)
        self.assertEqual(len(re.findall(r'data-draft="1"', html)), 1)

    def test_the_repo_menu_lists_each_repo_once_by_its_short_name(self):
        html = board.render_html(self.rows(), "t")
        self.assertIn('<option value="">All repos</option>', html)
        self.assertEqual(html.count('<option value="omacom/mesa">mesa</option>'), 1)
        self.assertEqual(html.count('<option value="omacom/linux-aurora">linux-aurora</option>'), 1)

    def test_the_menu_is_closed_and_the_repo_is_a_small_label_on_each_row(self):
        html = board.render_html(self.rows(), "t")
        self.assertRegex(html, r'<details class="menu"><summary>')
        self.assertNotIn('<details class="menu" open', html)
        self.assertIn('<span class="repo">mesa</span>', html)

    def test_the_section_count_is_the_count_of_the_default_view(self):
        html = board.render_html(self.rows(), "t")
        self.assertIn("2 shown, 2 can merge now", html.replace("1 can merge now", "2 can merge now")) if False else None
        self.assertIn("<span>2 shown, 1 can merge now</span>", html)

    def test_drafts_are_no_longer_a_closed_group_in_each_section(self):
        self.assertNotIn('class="held"', board.render_html(self.rows(), "t"))

    def test_the_page_still_works_for_the_summary_texts(self):
        text = board.render_summary(self.rows(), "u")
        self.assertIn("Can merge now: linux-aurora 1", text)

    def test_the_page_script_filters_by_repo_view_and_drafts(self):
        html = board.render_html(self.rows(), "t")
        for needle in ("data-repo", "data-act", "data-draft", "classList"):
            self.assertIn(needle, html)

    def test_the_footer_no_longer_says_maclab_is_not_shown(self):
        self.assertNotIn("Maclab qualification is not shown", board.render_html(self.rows(), "t"))


class FetchTest(unittest.TestCase):
    def test_the_page_queries_ask_for_the_head_the_body_and_the_requested_reviewers(self):
        for query in (board.QUERY, board.SINGLE_QUERY, board.SINGLE_LIGHT_QUERY):
            for field in ("headRefOid", "body", "reviewRequests"):
                self.assertIn(field, query)

    def test_the_checks_query_and_the_merged_query_stay_small(self):
        self.assertNotIn("body", board.CHECKS_QUERY)
        self.assertNotIn("body", board.MERGED_QUERY)


if __name__ == "__main__":
    unittest.main()
