"""The PR board, against fixture data shaped like GitHub's GraphQL answer. No network."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import board


def check(name: str, conclusion: str | None = "SUCCESS", status: str = "COMPLETED") -> dict:
    return {"__typename": "CheckRun", "name": name, "conclusion": conclusion, "status": status}


def node(number: int = 1, **over) -> dict:
    """A clean, approved, green PR; keyword arguments change it."""
    base = {
        "number": number, "title": f"Change {number}", "url": f"https://github.com/o/r/pull/{number}", "isDraft": False,
        "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "reviewDecision": "APPROVED", "updatedAt": "2026-10-29T12:00:00Z",
        "baseRefName": "main", "author": {"login": "alice"}, "labels": {"nodes": []},
        "reviews": {"nodes": [{"state": "APPROVED", "author": {"login": "bob"}}]},
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS", "contexts": {"nodes": [check("build")]}}}}]},
    }
    base.update(over)
    return base


def rollup(state: str, *contexts: dict) -> dict:
    return {"nodes": [{"commit": {"statusCheckRollup": {"state": state, "contexts": {"nodes": list(contexts)}}}}]}


def row(**over) -> dict:
    return board.classify("o/r", node(**over))


class BlockingItemTest(unittest.TestCase):
    def test_a_green_approved_clean_pr_can_merge_now(self):
        r = row()
        self.assertTrue(r["can_merge"])
        self.assertEqual((r["blocker"], r["merge"], r["checks"], r["approvals"]), ("None: can merge now", "mergeable", "passing", "approved by 1"))

    def test_a_draft_is_blocked_by_being_a_draft_before_anything_else(self):
        r = row(isDraft=True, mergeable="CONFLICTING", mergeStateStatus="DIRTY")
        self.assertEqual(r["blocker"], "Draft")
        self.assertFalse(r["can_merge"])

    def test_conflicts_come_before_review_and_checks(self):
        r = row(mergeable="CONFLICTING", mergeStateStatus="DIRTY", reviewDecision="REVIEW_REQUIRED")
        self.assertEqual((r["blocker"], r["merge"]), ("Merge conflicts with the base branch", "conflicts"))

    def test_a_dirty_merge_state_is_a_conflict_even_when_mergeable_is_still_unknown(self):
        r = row(mergeable="UNKNOWN", mergeStateStatus="DIRTY")
        self.assertEqual(r["blocker"], "Merge conflicts with the base branch")

    def test_changes_requested_names_the_reviewers_whose_latest_review_asks_for_them(self):
        reviews = {"nodes": [{"state": "CHANGES_REQUESTED", "author": {"login": "carol"}}, {"state": "APPROVED", "author": {"login": "bob"}},
                             {"state": "CHANGES_REQUESTED", "author": {"login": "dave"}}, {"state": "APPROVED", "author": {"login": "dave"}}]}
        r = row(reviewDecision="CHANGES_REQUESTED", reviews=reviews, mergeStateStatus="BLOCKED")
        self.assertEqual(r["blocker"], "Changes requested by carol")
        self.assertEqual(r["approvals"], "approved by 2, changes requested")

    def test_failing_checks_are_named(self):
        contexts = [check("build"), check("lint", "FAILURE"), check("test", "TIMED_OUT")]
        r = row(commits=rollup("FAILURE", *contexts), mergeStateStatus="UNSTABLE")
        self.assertEqual((r["blocker"], r["checks"]), ("Failing check: lint, test", "failing: lint, test"))

    def test_a_failing_check_named_approve_is_the_approval_gate_not_a_broken_build(self):
        r = row(commits=rollup("FAILURE", check("build"), check("approve", "FAILURE")), reviewDecision=None, mergeStateStatus="BLOCKED")
        self.assertEqual((r["kind"], r["blocker"], r["checks"]), ("Needs review", "Needs an approving review", "passing (approval check failing)"))

    def test_a_real_failure_next_to_the_gate_still_shows_as_a_failure(self):
        r = row(commits=rollup("FAILURE", check("lint", "FAILURE"), check("approve", "FAILURE")), mergeStateStatus="BLOCKED")
        self.assertEqual((r["kind"], r["blocker"]), ("Failing check", "Failing check: lint"))

    def test_a_workflow_run_that_needs_approval_is_not_a_failure(self):
        r = row(commits=rollup("PENDING", check("build", "ACTION_REQUIRED")), mergeStateStatus="BLOCKED", reviewDecision="REVIEW_REQUIRED")
        self.assertEqual((r["blocker"], r["checks"]), ("A maintainer must approve the workflow run", "needs a maintainer to approve the run"))

    def test_review_required_says_who_has_not_yet_approved(self):
        r = row(reviewDecision="REVIEW_REQUIRED", reviews={"nodes": []}, mergeStateStatus="BLOCKED")
        self.assertEqual((r["blocker"], r["approvals"]), ("Needs an approving review", "none yet"))

    def test_running_checks_block_until_they_finish(self):
        r = row(commits=rollup("PENDING", check("build", None, "IN_PROGRESS")), mergeStateStatus="BLOCKED")
        self.assertEqual((r["blocker"], r["checks"]), ("Checks still running", "running"))

    def test_a_clean_pr_behind_its_base_needs_an_update(self):
        r = row(mergeStateStatus="BEHIND")
        self.assertEqual(r["blocker"], "Branch is behind the base branch")
        self.assertFalse(r["can_merge"])

    def test_no_checks_at_all_is_reported_as_none_and_does_not_block_a_clean_pr(self):
        r = row(commits={"nodes": [{"commit": {"statusCheckRollup": None}}]})
        self.assertEqual(r["checks"], "none")
        self.assertTrue(r["can_merge"])

    def test_an_unknown_mergeability_is_not_called_clean(self):
        r = row(mergeable="UNKNOWN", mergeStateStatus="UNKNOWN")
        self.assertEqual((r["merge"], r["blocker"]), ("unknown", "GitHub has not computed mergeability yet"))
        self.assertFalse(r["can_merge"])

    def test_a_blocked_but_approved_green_pr_reports_the_branch_rules(self):
        r = row(mergeStateStatus="BLOCKED")
        self.assertEqual(r["blocker"], "Blocked by branch rules (a required check or review is missing)")


class RowFieldsTest(unittest.TestCase):
    def test_rc_blocker_follows_the_label_and_nothing_else(self):
        self.assertEqual(row(labels={"nodes": [{"name": "RC-blocker"}]})["rc_blocker"], "yes")
        self.assertEqual(row(labels={"nodes": [{"name": "release blocker"}]})["rc_blocker"], "yes")
        self.assertEqual(row(title="Fix the blocker in the RC")["rc_blocker"], "no")
        self.assertEqual(row(labels={"nodes": [{"name": "bug"}]})["rc_blocker"], "no")

    def test_owner_is_the_author_and_the_maclab_state_is_unknown(self):
        r = row()
        self.assertEqual((r["owner"], r["maclab"], r["repo"], r["number"]), ("alice", "unknown", "o/r", 1))

    def test_a_deleted_author_does_not_break_the_row(self):
        self.assertEqual(row(author=None)["owner"], "unknown")


class PageTest(unittest.TestCase):
    def rows(self):
        return board.rows_from({"o/r": [node(1), node(2, mergeable="CONFLICTING", mergeStateStatus="DIRTY", title="<script>alert(1)</script>"),
                                         node(3, reviewDecision="REVIEW_REQUIRED", mergeStateStatus="BLOCKED", reviews={"nodes": []})]})

    def test_rows_sort_merge_ready_first_then_by_repo_and_number(self):
        self.assertEqual([r["number"] for r in self.rows()], [1, 2, 3])

    def test_a_merge_ready_pr_sorts_ahead_of_a_lower_numbered_stuck_one(self):
        rows = board.rows_from({"o/r": [node(1, isDraft=True), node(2)]})
        self.assertEqual([r["number"] for r in rows], [2, 1])

    def test_the_html_lists_merge_ready_prs_first_and_escapes_titles(self):
        html = board.render_html(self.rows(), "2026-10-29 12:00 UTC")
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertLess(html.index("Omarchy M team"), html.index("Everyone else"))
        self.assertIn("2026-10-29 12:00 UTC", html)
        self.assertIn("unknown", html)

    def test_the_summary_says_what_can_merge_and_what_waits_on_whom(self):
        text = board.render_summary(self.rows(), "https://example.invalid/board")
        self.assertTrue(text.startswith("[Joshua's agent]"))
        self.assertIn("There are 3 open pull requests", text)
        self.assertIn("1 can merge now. 2 are stuck.", text)
        self.assertIn("Can merge now: r 1.", text)
        self.assertIn("Needs review (1): r 3.", text)
        self.assertIn("Waiting on the author or a run: Merge conflicts 1.", text)
        self.assertIn("https://example.invalid/board", text)

    def test_repo_names_are_shortened_in_the_summary(self):
        self.assertEqual(board.short("omacom/omarchy-mac-installer"), "installer")
        self.assertEqual(board.short("omacom/mesa"), "mesa")
        self.assertEqual(board.short("aurora-silicon/aurora-installer"), "silicon-aurora-installer")
        self.assertEqual(board.short("chriskearney/maclab"), "maclab")

    def test_a_long_list_is_cut_and_says_how_many_more(self):
        rows = board.rows_from({"o/r": [node(n) for n in range(1, board.MAX_LISTED + 4)],
                                "o/s": [node(n, reviewDecision="REVIEW_REQUIRED", mergeStateStatus="BLOCKED") for n in range(1, board.MAX_LISTED + 6)]})
        text = board.render_summary(rows, "u")
        self.assertIn(f", {board.MAX_LISTED}, and 3 more.", text)
        self.assertIn(f"Needs review ({board.MAX_LISTED + 5}): s 1, 2, 3", text)
        self.assertNotIn(f", {board.MAX_LISTED + 1},", text)

    def test_the_author_side_is_counted_by_kind_not_by_the_names_in_the_text(self):
        rows = board.rows_from({"o/r": [node(1, commits=rollup("FAILURE", check("a", "FAILURE"))),
                                         node(2, commits=rollup("FAILURE", check("b", "FAILURE"))),
                                         node(3, reviewDecision="CHANGES_REQUESTED", reviews={"nodes": [{"state": "CHANGES_REQUESTED", "author": {"login": "x"}}]}),
                                         node(4, reviewDecision="CHANGES_REQUESTED", reviews={"nodes": [{"state": "CHANGES_REQUESTED", "author": {"login": "y"}}]})]})
        self.assertIn("Waiting on the author or a run: Failing check 2, Changes requested 2.", board.render_summary(rows, "u"))

    def test_prs_of_two_repos_are_listed_with_one_repo_name_per_run(self):
        rows = board.rows_from({"omacom/mesa": [node(5), node(6)], "omacom/omarchy-mac-installer": [node(134)]})
        self.assertIn("Can merge now: mesa 5, 6; installer 134.", board.render_summary(rows, "u"))

    def test_a_clean_board_has_no_waiting_lines(self):
        text = board.render_summary(board.rows_from({"o/r": [node(1)]}), "u")
        self.assertNotIn("Waiting on", text)
        self.assertNotIn("Needs review", text)

    def test_an_empty_board_says_so(self):
        self.assertIn("No open pull requests were read", board.render_summary([], "u"))
        self.assertIn("No open pull requests", board.render_html([], "t"))


class SplitQueryTest(unittest.TestCase):
    @staticmethod
    def base(numbers, more=False, cursor=None):
        nodes = [{k: v for k, v in node(n).items() if k != "commits"} for n in numbers]
        return {"data": {"repository": {"isPrivate": False, "pullRequests": {"pageInfo": {"hasNextPage": more, "endCursor": cursor}, "nodes": nodes}}}}

    @staticmethod
    def checks(numbers, conclusion="SUCCESS"):
        nodes = [{"number": n, "commits": rollup("SUCCESS" if conclusion == "SUCCESS" else "FAILURE", check("ci", conclusion))} for n in numbers]
        return {"data": {"repository": {"pullRequests": {"nodes": nodes}}}}

    def test_the_page_query_no_longer_asks_for_checks_and_the_checks_query_does(self):
        self.assertNotIn("commits", board.QUERY)
        self.assertNotIn("statusCheckRollup", board.QUERY)
        self.assertIn("statusCheckRollup", board.CHECKS_QUERY)
        self.assertIn("orderBy", board.CHECKS_QUERY)

    def test_each_pr_gets_its_checks_from_the_second_query_with_the_same_cursor(self):
        seen = []

        def run(owner, name, after, size):
            seen.append(("base", after, size))
            return self.base([1, 2], True, "c1") if after is None else self.base([3])

        def checks_run(owner, name, after, size):
            seen.append(("checks", after, size))
            return self.checks([1, 2] if after is None else [3], "FAILURE" if after else "SUCCESS")

        data = board.fetch(["o/r"], run, checks_run)
        self.assertEqual([n["number"] for n in data["o/r"]], [1, 2, 3])
        self.assertEqual(board.classify("o/r", data["o/r"][0])["checks"], "passing")
        self.assertEqual(board.classify("o/r", data["o/r"][2])["checks"], "failing: ci")
        self.assertEqual(seen, [("base", None, board.PAGE_SIZE), ("checks", None, board.PAGE_SIZE), ("base", "c1", board.PAGE_SIZE), ("checks", "c1", board.PAGE_SIZE)])

    def test_a_failing_checks_query_retries_the_page_with_a_smaller_size_then_gives_up(self):
        sizes = []

        def checks_run(owner, name, after, size):
            sizes.append(size)
            raise subprocess.CalledProcessError(1, "gh", stderr="Something went wrong")

        data = board.fetch(["o/r"], lambda *a: self.base([1]), checks_run)
        self.assertEqual((data, sizes), ({"o/r": None}, [board.PAGE_SIZE, board.SMALL_PAGE_SIZE]))

    def test_a_pr_the_checks_query_does_not_return_fails_the_page_instead_of_showing_no_checks(self):
        data = board.fetch(["o/r"], lambda *a: self.base([1, 2]), lambda *a: self.checks([1]))
        self.assertEqual(data, {"o/r": None})

    def test_without_a_checks_runner_the_nodes_keep_what_the_page_query_gave(self):
        data = board.fetch(["o/r"], lambda *a: self.base([1]))
        self.assertNotIn("commits", data["o/r"][0])


class OneByOneTest(unittest.TestCase):
    @staticmethod
    def failing_pages(owner, name, after, size):
        raise subprocess.CalledProcessError(1, "gh", stderr="Something went wrong")

    @staticmethod
    def numbers(nums, more=False, cursor=None, private=False):
        return lambda owner, name, after: {"data": {"repository": {"isPrivate": private, "pullRequests": {
            "pageInfo": {"hasNextPage": more, "endCursor": cursor}, "nodes": [{"number": n} for n in nums]}}}}

    @staticmethod
    def single(bad=()):
        def run(owner, name, number, light=False):
            if number in bad:
                raise subprocess.CalledProcessError(1, "gh", stderr="Something went wrong")
            return {"data": {"repository": {"isPrivate": False, "pullRequest": node(number)}}}
        return run

    def test_a_pr_that_fails_in_full_is_read_again_without_its_merge_state(self):
        calls = []

        def single(owner, name, number, light=False):
            calls.append((number, light))
            if not light:
                raise subprocess.CalledProcessError(1, "gh", stderr="Something went wrong")
            pr = {k: v for k, v in node(number).items() if k not in ("mergeable", "mergeStateStatus")}
            return {"data": {"repository": {"isPrivate": False, "pullRequest": pr}}}

        with contextlib.redirect_stderr(io.StringIO()):
            data = board.fetch(["o/r"], self.failing_pages, None, self.numbers([4]), single)
        r = board.classify("o/r", data["o/r"][0])
        self.assertEqual(calls, [(4, False), (4, True)])
        self.assertEqual((r["merge"], r["kind"], r["owner"]), ("unknown", "Mergeability unknown", "alice"))

    def test_a_pr_that_vanished_between_the_list_and_the_read_is_skipped(self):
        def single(owner, name, number, light=False):
            return {"data": {"repository": {"isPrivate": False, "pullRequest": None if number == 5 else node(number)}}}

        data = board.fetch(["o/r"], self.failing_pages, None, self.numbers([4, 5, 6]), single)
        self.assertEqual([n["number"] for n in data["o/r"]], [4, 6])

    def test_after_the_deadline_the_rest_come_back_unread_without_another_call(self):
        calls = []

        def single(owner, name, number, light=False):
            calls.append(number)
            return {"data": {"repository": {"isPrivate": False, "pullRequest": node(number)}}}

        original = board.LAST_RESORT_SECONDS
        board.LAST_RESORT_SECONDS = -1
        try:
            data = board.fetch(["o/r"], self.failing_pages, None, self.numbers([1, 2]), single)
        finally:
            board.LAST_RESORT_SECONDS = original
        self.assertEqual((calls, data["o/r"]), ([], [{"number": 1, "_unread": True}, {"number": 2, "_unread": True}]))

    def test_the_summary_gives_unread_its_own_line(self):
        rows = board.rows_from({"o/r": [node(1), {"number": 2, "_unread": True}, node(3, isDraft=True)]})
        text = board.render_summary(rows, "u")
        self.assertIn("GitHub would not return the details of 1: r 2.", text)
        self.assertIn("Waiting on the author or a run: Draft 1.", text)

    def test_when_no_page_will_come_each_pr_is_read_alone_and_one_bad_pr_costs_one_row(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            data = board.fetch(["o/r"], self.failing_pages, None, self.numbers([4, 5, 6]), self.single(bad={5}))
        self.assertEqual([n["number"] for n in data["o/r"]], [4, 5, 6])
        rows = board.rows_from(data)
        self.assertEqual([(r["number"], r["kind"]) for r in rows], [(4, "None"), (6, "None"), (5, "Unread")])
        self.assertIn("o/r#5: light read failed", err.getvalue())
        self.assertNotIn("o/r#4", err.getvalue())

    def test_the_unread_row_claims_nothing(self):
        r = board.classify("o/r", {"number": 5, "_unread": True}, {"alice"}, {"o/r": {"alice"}})
        self.assertEqual((r["group"], r["owner"], r["can_merge"], r["merge"], r["checks"], r["approvals"]), ("other", "unknown", False, "unknown", "unknown", "unknown"))
        self.assertEqual(r["url"], "https://github.com/o/r/pull/5")

    def test_the_listing_follows_its_pages(self):
        pages = iter([{"data": {"repository": {"isPrivate": False, "pullRequests": {"pageInfo": {"hasNextPage": True, "endCursor": "c"}, "nodes": [{"number": 1}]}}}},
                      {"data": {"repository": {"isPrivate": False, "pullRequests": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [{"number": 2}]}}}}])
        data = board.fetch(["o/r"], self.failing_pages, None, lambda *a: next(pages), self.single())
        self.assertEqual([n["number"] for n in data["o/r"]], [1, 2])

    def test_a_failing_listing_makes_the_repo_unreadable(self):
        def numbers(owner, name, after):
            raise subprocess.CalledProcessError(1, "gh", stderr="boom")

        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(board.fetch(["o/r"], self.failing_pages, None, numbers, self.single()), {"o/r": None})

    def test_a_private_answer_in_the_last_resort_is_refused_too(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(board.fetch(["o/r"], self.failing_pages, None, self.numbers([1], private=True), self.single()), {})

    def test_a_pr_that_the_single_query_returns_as_null_is_skipped_not_a_crash(self):
        def single(owner, name, number, light=False):
            return {"data": {"repository": {"isPrivate": False, "pullRequest": None}}}

        data = board.fetch(["o/r"], self.failing_pages, None, self.numbers([7]), single)
        self.assertEqual(data["o/r"], [])

    def test_without_the_last_resort_a_failing_page_is_still_just_unreadable(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(board.fetch(["o/r"], self.failing_pages), {"o/r": None})


class PublicOnlyTest(unittest.TestCase):
    page = staticmethod(FetchTest.page) if False else None

    @staticmethod
    def answer(private):
        return {"data": {"repository": {"isPrivate": private, "pullRequests": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [node(1)]}}}}

    def test_a_private_repo_is_never_read_into_the_board_and_never_named(self):
        calls = []

        def run(owner, name, after, size):
            calls.append(name)
            return self.answer(name == "secret")

        data = board.fetch(["o/secret", "o/open"], run)
        self.assertEqual(list(data), ["o/open"])
        rows = board.rows_from(data)
        out = board.render_html(rows, "t", sorted(r for r, nodes in data.items() if nodes is None)) + board.render_summary(rows, "u")
        self.assertNotIn("secret", out)
        self.assertEqual(calls, ["secret", "open"], "a private repo is not retried with a smaller page")

    def test_an_answer_that_does_not_say_whether_the_repo_is_private_is_refused(self):
        def run(owner, name, after, size):
            answer = self.answer(False)
            del answer["data"]["repository"]["isPrivate"]
            return answer

        self.assertEqual(board.fetch(["o/r"], run), {})


class FetchTest(unittest.TestCase):
    @staticmethod
    def page(nodes, more=False, cursor=None, private=False):
        return {"data": {"repository": {"isPrivate": private, "pullRequests": {"pageInfo": {"hasNextPage": more, "endCursor": cursor}, "nodes": nodes}}}}

    def test_it_follows_the_pages_until_the_last(self):
        calls = []

        def run(owner, name, after, size):
            calls.append((owner, name, after, size))
            return self.page([node(1)], True, "c1") if after is None else self.page([node(2)])

        data = board.fetch(["o/r"], run)
        self.assertEqual([n["number"] for n in data["o/r"]], [1, 2])
        self.assertEqual([c[2] for c in calls], [None, "c1"])

    def test_a_failing_page_is_tried_again_with_a_smaller_page_then_the_repo_is_marked_unreadable(self):
        sizes = []

        def run(owner, name, after, size):
            sizes.append(size)
            raise subprocess.CalledProcessError(1, "gh")

        self.assertEqual(board.fetch(["o/r"], run), {"o/r": None})
        self.assertEqual(sizes, [board.PAGE_SIZE, board.SMALL_PAGE_SIZE])

    def test_a_repo_that_works_with_the_smaller_page_is_read(self):
        def run(owner, name, after, size):
            if size == board.PAGE_SIZE:
                raise subprocess.CalledProcessError(1, "gh")
            return self.page([node(1)])

        self.assertEqual([n["number"] for n in board.fetch(["o/r"], run)["o/r"]], [1])

    def test_one_unreadable_repo_does_not_stop_the_others(self):
        def run(owner, name, after, size):
            if name == "bad":
                raise subprocess.CalledProcessError(1, "gh")
            return self.page([node(1)])

        data = board.fetch(["o/bad", "o/good"], run)
        self.assertEqual((data["o/bad"], len(data["o/good"])), (None, 1))


class FixtureRunTest(unittest.TestCase):
    def test_main_with_a_fixture_writes_the_page_the_json_and_the_summary(self):
        data = {"o/r": [node(1), node(2, isDraft=True)], "o/empty": []}
        with tempfile.TemporaryDirectory() as tmp:
            fixture = os.path.join(tmp, "data.json")
            with open(fixture, "w", encoding="utf-8") as f:
                json.dump(data, f)
            status = board.main(["--fixture", fixture, "--out", tmp, "--url", "https://example.invalid/b", "--now", "2026-10-29 12:00 UTC"])
            self.assertEqual(status, 0)
            with open(os.path.join(tmp, "board.json"), encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual([(r["number"], r["can_merge"]) for r in saved["rows"]], [(1, True), (2, False)])
            self.assertEqual(saved["generated"], "2026-10-29 12:00 UTC")
            with open(os.path.join(tmp, "index.html"), encoding="utf-8") as f:
                self.assertIn("o/r#1", f.read())
            with open(os.path.join(tmp, "summary.txt"), encoding="utf-8") as f:
                self.assertIn("1 can merge now", f.read())

    def test_a_reply_with_no_repository_makes_the_repo_unreadable_not_a_crash(self):
        def run(owner, name, after, size):
            return {"data": {"repository": None}}

        self.assertEqual(board.fetch(["o/r"], run), {"o/r": None})

    def test_the_page_says_when_it_last_changed_not_when_it_was_last_refreshed(self):
        html = board.render_html([], "2026-10-29 12:00 UTC")
        self.assertIn("Last change: 2026-10-29 12:00 UTC", html)
        self.assertNotIn("Last refresh", html)

    def test_only_if_changed_leaves_the_files_alone_when_no_row_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = os.path.join(tmp, "data.json")
            with open(fixture, "w", encoding="utf-8") as f:
                json.dump({"o/r": [node(1)]}, f)
            args = ["--fixture", fixture, "--out", tmp, "--url", "u", "--only-if-changed"]
            board.main([*args, "--now", "first"])
            board.main([*args, "--now", "second"])
            with open(os.path.join(tmp, "board.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f)["generated"], "first")
            with open(fixture, "w", encoding="utf-8") as f:
                json.dump({"o/r": [node(1, isDraft=True)]}, f)
            board.main([*args, "--now", "third"])
            with open(os.path.join(tmp, "board.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f)["generated"], "third")

    def test_only_if_changed_writes_when_a_repo_becomes_unreadable(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = os.path.join(tmp, "data.json")
            args = ["--fixture", fixture, "--out", tmp, "--url", "u", "--only-if-changed"]
            with open(fixture, "w", encoding="utf-8") as f:
                json.dump({"o/r": [node(1)], "o/s": []}, f)
            board.main([*args, "--now", "first"])
            with open(fixture, "w", encoding="utf-8") as f:
                json.dump({"o/r": [node(1)], "o/s": None}, f)
            board.main([*args, "--now", "second"])
            with open(os.path.join(tmp, "board.json"), encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual((saved["generated"], saved["unreadable"]), ("second", ["o/s"]))

    def test_keep_last_good_leaves_the_page_alone_when_more_repos_are_unreadable(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = os.path.join(tmp, "data.json")
            args = ["--fixture", fixture, "--out", tmp, "--url", "u", "--keep-last-good"]
            with open(fixture, "w", encoding="utf-8") as f:
                json.dump({"o/r": [node(1)], "o/s": [node(2)]}, f)
            board.main([*args, "--now", "good"])
            with open(fixture, "w", encoding="utf-8") as f:
                json.dump({"o/r": [node(1)], "o/s": None}, f)
            board.main([*args, "--now", "worse"])
            with open(os.path.join(tmp, "board.json"), encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual((saved["generated"], saved["unreadable"], len(saved["rows"])), ("good", [], 2))
            with open(fixture, "w", encoding="utf-8") as f:
                json.dump({"o/r": [node(1, isDraft=True)], "o/s": [node(2)]}, f)
            board.main([*args, "--now", "better"])
            with open(os.path.join(tmp, "board.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f)["generated"], "better")

    def test_a_failed_read_says_which_repo_and_why_on_stderr(self):
        def run(owner, name, after, size):
            raise subprocess.CalledProcessError(1, "gh", stderr="HTTP 502  Bad Gateway")

        with contextlib.redirect_stderr(io.StringIO()) as err:
            board.fetch(["o/r"], run)
        self.assertIn("o/r: page size", err.getvalue())
        self.assertIn("HTTP 502 Bad Gateway", err.getvalue())

    def test_a_repo_that_cannot_be_read_is_listed_not_hidden(self):
        data = {"o/r": [node(1)], "o/gone": None}
        with tempfile.TemporaryDirectory() as tmp:
            fixture = os.path.join(tmp, "data.json")
            with open(fixture, "w", encoding="utf-8") as f:
                json.dump(data, f)
            board.main(["--fixture", fixture, "--out", tmp, "--url", "u", "--now", "t"])
            with open(os.path.join(tmp, "index.html"), encoding="utf-8") as f:
                self.assertIn("o/gone", f.read())
            with open(os.path.join(tmp, "board.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f)["unreadable"], ["o/gone"])


if __name__ == "__main__":
    unittest.main()
