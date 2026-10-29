"""The board's three sections: team, existing contributors, everyone else."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import board
from test_board import check, node, rollup

TEAM = {"alice", "bob"}


def pr(number: int, login: str, **over) -> dict:
    return node(number, **{"author": {"login": login}, **over})


class GroupTest(unittest.TestCase):
    def test_a_team_login_is_team_whatever_its_case(self):
        r = board.classify("o/r", pr(1, "ALICE"), TEAM, {"o/r": {"alice"}})
        self.assertEqual(r["group"], "team")

    def test_a_merged_author_in_the_same_repo_is_an_existing_contributor(self):
        r = board.classify("o/r", pr(1, "carol"), TEAM, {"o/r": {"carol"}})
        self.assertEqual(r["group"], "contributor")

    def test_a_merge_in_another_repo_does_not_make_a_contributor_here(self):
        r = board.classify("o/r", pr(1, "carol"), TEAM, {"o/other": {"carol"}})
        self.assertEqual(r["group"], "other")

    def test_a_contributor_login_matches_without_regard_to_case(self):
        self.assertEqual(board.classify("o/r", pr(1, "Carol"), TEAM, {"o/r": {"carol"}})["group"], "contributor")

    def test_team_wins_over_contributor(self):
        self.assertEqual(board.classify("o/r", pr(1, "bob"), TEAM, {"o/r": {"bob"}})["group"], "team")

    def test_nobody_known_is_everyone_else_and_a_deleted_author_too(self):
        self.assertEqual(board.classify("o/r", pr(1, "dave"), TEAM, {})["group"], "other")
        self.assertEqual(board.classify("o/r", pr(2, "x", author=None), TEAM, {})["group"], "other")

    def test_without_team_or_history_everyone_is_everyone_else(self):
        self.assertEqual(board.classify("o/r", pr(1, "alice"))["group"], "other")

    def test_rows_are_ordered_team_then_contributors_then_others_and_merge_ready_first_inside(self):
        data = {"o/r": [pr(1, "dave", ), pr(2, "carol", isDraft=True), pr(3, "carol"), pr(4, "alice", isDraft=True), pr(5, "alice")]}
        rows = board.rows_from(data, TEAM, {"o/r": {"carol"}})
        self.assertEqual([(r["group"], r["number"]) for r in rows], [("team", 5), ("team", 4), ("contributor", 3), ("contributor", 2), ("other", 1)])


class PageSectionsTest(unittest.TestCase):
    def rows(self):
        data = {"o/r": [pr(1, "dave"), pr(2, "carol"), pr(3, "alice")]}
        return board.rows_from(data, TEAM, {"o/r": {"carol"}})

    def test_the_page_has_the_three_sections_in_order_with_counts(self):
        html = board.render_html(self.rows(), "t")
        order = [html.index(h) for h in ("Omarchy M team", "Existing contributors", "Everyone else")]
        self.assertEqual(order, sorted(order))
        self.assertEqual(html.count("<span>1 open, 1 can merge now</span>"), 3)

    def test_an_empty_section_says_none(self):
        html = board.render_html(board.rows_from({"o/r": [pr(1, "alice")]}, TEAM, {}), "t")
        self.assertIn("Existing contributors<span>0 open, 0 can merge now</span>", html)
        self.assertIn('<p class="none">None.</p>', html)

    def test_the_page_shows_the_login_only_never_a_name(self):
        rows = self.rows()
        self.assertTrue(all(set(r).isdisjoint({"team_name", "name"}) for r in rows))
        self.assertIn("r #3 by alice", board.render_html(rows, "t"))


class StateTest(unittest.TestCase):
    def state(self, **over):
        return board.classify("o/r", node(1, **over))["state"]

    def test_a_green_clean_pr_is_ready_to_merge(self):
        self.assertEqual(self.state(), "ready to merge")

    def test_a_draft_is_held_whatever_else_is_wrong_with_it(self):
        self.assertEqual(self.state(isDraft=True, mergeable="CONFLICTING", mergeStateStatus="DIRTY"), "held")

    def test_a_missing_review_a_run_waiting_for_a_maintainer_and_branch_rules_need_review(self):
        self.assertEqual(self.state(reviewDecision="REVIEW_REQUIRED", mergeStateStatus="BLOCKED"), "needs review")
        self.assertEqual(self.state(commits=rollup("PENDING", check("build", "ACTION_REQUIRED")), mergeStateStatus="BLOCKED", reviewDecision="REVIEW_REQUIRED"), "needs review")
        self.assertEqual(self.state(mergeStateStatus="BLOCKED"), "needs review")

    def test_conflicts_failing_checks_requested_changes_and_a_branch_behind_need_fixes(self):
        self.assertEqual(self.state(mergeable="CONFLICTING", mergeStateStatus="DIRTY"), "needs fixes")
        self.assertEqual(self.state(commits=rollup("FAILURE", check("lint", "FAILURE"))), "needs fixes")
        self.assertEqual(self.state(reviewDecision="CHANGES_REQUESTED", reviews={"nodes": [{"state": "CHANGES_REQUESTED", "author": {"login": "x"}}]}), "needs fixes")
        self.assertEqual(self.state(mergeStateStatus="BEHIND"), "needs fixes")

    def test_running_checks_and_an_unknown_merge_state_are_waiting(self):
        self.assertEqual(self.state(commits=rollup("PENDING", check("build", None, "IN_PROGRESS")), mergeStateStatus="BLOCKED"), "waiting")
        self.assertEqual(self.state(mergeable="UNKNOWN", mergeStateStatus="UNKNOWN"), "waiting")
        self.assertEqual(board.classify("o/r", {"number": 5, "_unread": True})["state"], "waiting")

    def test_every_blocking_kind_has_a_state_and_every_state_a_colour(self):
        kinds = {"None", "Draft", "Merge conflicts", "Changes requested", "Failing check", "Workflow run needs approval", "Needs review", "Checks running",
                 "Branch behind", "Mergeability unknown", "Branch rules", "Unread"}
        self.assertEqual(set(board.KIND_STATE), kinds)
        self.assertEqual(set(board.KIND_STATE.values()), set(board.STATES))
        self.assertEqual(set(board.STATE_CLASS), set(board.STATES))

    def test_the_summary_and_the_page_agree_on_what_is_ready(self):
        rows = board.rows_from({"o/r": [node(1), node(2, isDraft=True), node(3, reviewDecision="REVIEW_REQUIRED", mergeStateStatus="BLOCKED")]})
        self.assertEqual([r["can_merge"] for r in rows], [r["state"] == "ready to merge" for r in rows])


class ChipsTest(unittest.TestCase):
    def chips(self, **over):
        return board._chips(board.classify("o/r", node(1, **over)))

    def test_a_row_has_one_state_chip_and_nothing_else_by_default(self):
        self.assertEqual(self.chips(), [("ok", "ready to merge")])

    def test_each_state_has_its_colour(self):
        self.assertEqual(self.chips(isDraft=True), [("draft", "held")])
        self.assertEqual(self.chips(reviewDecision="REVIEW_REQUIRED", mergeStateStatus="BLOCKED"), [("warn", "needs review")])
        self.assertEqual(self.chips(mergeable="CONFLICTING", mergeStateStatus="DIRTY"), [("bad", "needs fixes")])
        self.assertEqual(self.chips(mergeStateStatus="BEHIND")[0][1], "needs fixes")

    def test_an_rc_blocker_gets_a_second_chip(self):
        self.assertEqual(self.chips(labels={"nodes": [{"name": "rc-blocker"}]}), [("ok", "ready to merge"), ("bad", "RC blocker")])


class RowHtmlTest(unittest.TestCase):
    def html(self, *nodes, **kw):
        return board.render_html(board.rows_from({"o/r": list(nodes)}, TEAM, {}), "t", **kw)

    def test_a_ready_row_is_marked_and_has_no_blocking_line(self):
        html = self.html(pr(1, "alice"))
        self.assertIn('class="pr ready"', html)
        self.assertNotIn('class="why"', html)

    def test_a_row_that_needs_something_says_what_under_the_title(self):
        html = self.html(pr(1, "alice", mergeable="CONFLICTING", mergeStateStatus="DIRTY"))
        self.assertNotIn('class="pr ready"', html)
        self.assertIn('<div class="why">Merge conflicts with the base branch</div>', html)

    def test_a_line_that_repeats_the_state_is_cut_and_a_more_specific_one_stays(self):
        plain = self.html(pr(1, "alice", reviewDecision="REVIEW_REQUIRED", mergeStateStatus="BLOCKED"))
        self.assertIn(">needs review<", plain)
        self.assertNotIn('class="why"', plain)
        specific = self.html(pr(1, "alice", mergeStateStatus="BLOCKED"))
        self.assertIn('<div class="why">Blocked by branch rules', specific)
        waiting = self.html(pr(1, "alice", commits=rollup("PENDING", check("build", None, "IN_PROGRESS")), mergeStateStatus="BLOCKED"))
        self.assertIn('<div class="why">Checks still running</div>', waiting)

    def test_held_prs_fold_into_one_closed_group_and_say_nothing_more(self):
        html = self.html(pr(1, "alice"), pr(2, "alice", isDraft=True), pr(3, "alice", isDraft=True))
        self.assertIn('<details class="held"><summary>2 held</summary>', html)
        self.assertNotIn("<details open", html)
        self.assertLess(html.index("r #1 by alice"), html.index('<details class="held">'))
        self.assertLess(html.index('<details class="held">'), html.index("r #2 by alice"))
        self.assertNotIn('<div class="why">Draft</div>', html)

    def test_a_section_with_only_held_prs_shows_the_group_and_not_none(self):
        html = self.html(pr(1, "alice", isDraft=True))
        self.assertIn("1 held", html)
        self.assertNotIn('<p class="none">None.</p>', html.split("Existing contributors")[0])

    def test_the_active_prs_come_in_state_order(self):
        html = self.html(pr(1, "alice", mergeable="CONFLICTING", mergeStateStatus="DIRTY"), pr(2, "alice", reviewDecision="REVIEW_REQUIRED", mergeStateStatus="BLOCKED"), pr(3, "alice"))
        order = [html.index(f"r #{n} by alice") for n in (3, 2, 1)]
        self.assertEqual(order, sorted(order))

    def test_the_age_is_a_time_element_with_the_date_as_its_plain_text(self):
        html = self.html(pr(1, "alice", updatedAt="2026-10-27T09:00:00Z"))
        self.assertIn('<time class="chip" datetime="2026-10-27" title="last updated">2026-10-27</time>', html)
        self.assertIn("document.querySelectorAll", html)

    def test_a_title_a_login_and_a_url_cannot_break_out_of_the_page(self):
        evil = pr(1, '"><img src=x>', title='<b onmouseover="x">t</b>', url='https://example.invalid/"><script>x</script>')
        html = self.html(evil)
        self.assertNotIn("<img src=x>", html)
        self.assertNotIn('<b onmouseover', html)
        self.assertNotIn('"><script>x</script>', html)

    def test_the_page_has_no_external_request(self):
        html = self.html(pr(1, "alice"))
        for needle in ("http://", "https://fonts", "<link", "src="):
            self.assertNotIn(needle, html.replace("https://example.invalid", ""), needle)

    def test_the_page_is_dark_tokyo_night_and_fits_a_phone(self):
        html = self.html(pr(1, "alice"))
        self.assertIn("--night:#1a1b26", html)
        self.assertIn('name="viewport"', html)
        self.assertIn("@media(max-width", html)

    def test_unreadable_repos_are_named_in_a_note(self):
        self.assertIn('<p class="note">Not read this time: o/gone.</p>', self.html(pr(1, "alice"), unreadable=["o/gone"]))


class DailyTest(unittest.TestCase):
    def rows(self):
        data = {"o/r": [pr(1, "alice"), pr(2, "alice", mergeable="CONFLICTING", mergeStateStatus="DIRTY"), pr(3, "bob", reviewDecision="REVIEW_REQUIRED", mergeStateStatus="BLOCKED"),
                        pr(4, "dave", mergeable="CONFLICTING", mergeStateStatus="DIRTY"), pr(5, "carol", reviewDecision="REVIEW_REQUIRED", mergeStateStatus="BLOCKED"),
                        pr(6, "alice", isDraft=True)]}
        return board.rows_from(data, TEAM, {"o/r": {"carol"}})

    def test_it_says_what_can_merge_and_the_one_thing_each_stuck_team_pr_needs(self):
        text = board.render_daily(self.rows(), "https://example.invalid/board")
        self.assertTrue(text.startswith("[Joshua's agent]"))
        self.assertIn("1 can merge now, and 3 team PRs are stuck.", text)
        self.assertIn("Can merge now: r 1.", text)
        self.assertIn("PR r 2 by alice: Merge conflicts with the base branch.", text)
        self.assertIn("PR r 3 by bob: Needs an approving review.", text)
        self.assertIn("https://example.invalid/board", text)

    def test_prs_waiting_on_a_reviewer_come_before_the_others(self):
        text = board.render_daily(self.rows(), "u")
        self.assertLess(text.index("PR r 3 by bob"), text.index("PR r 2 by alice"))

    def test_drafts_are_counted_in_one_line_not_named(self):
        text = board.render_daily(self.rows(), "u")
        self.assertNotIn("PR r 6", text)
        self.assertIn("1 team drafts are not listed.", text)

    def test_other_prs_are_not_named_one_by_one(self):
        text = board.render_daily(self.rows(), "u")
        self.assertNotIn("PR r 4 by", text)
        self.assertNotIn("PR r 5 by", text)
        self.assertNotIn("dave", text)
        self.assertNotIn("carol", text)

    def test_only_logins_appear_no_names(self):
        rows = self.rows()
        self.assertTrue(all(set(r).isdisjoint({"team_name", "name"}) for r in rows))
        self.assertNotIn("Alice A", board.render_daily(rows, "u"))

    def test_a_long_stuck_list_is_cut_and_says_how_many_more(self):
        rows = board.rows_from({"o/r": [pr(n, "alice", mergeable="CONFLICTING", mergeStateStatus="DIRTY") for n in range(1, board.MAX_DAILY_STUCK + 4)]}, TEAM, {})
        text = board.render_daily(rows, "u")
        self.assertIn(f"PR r {board.MAX_DAILY_STUCK} by alice:", text)
        self.assertNotIn(f"PR r {board.MAX_DAILY_STUCK + 1} by", text)
        self.assertIn("...and 3 more on the board.", text)

    def test_when_no_team_pr_is_stuck_there_is_no_stuck_section(self):
        text = board.render_daily(board.rows_from({"o/r": [pr(1, "alice")]}, TEAM, {}), "u")
        self.assertNotIn("Stuck team PRs", text)
        self.assertNotIn("drafts", text)
        self.assertIn("0 team PRs are stuck", text)

    def test_an_empty_board_says_so(self):
        self.assertIn("No open pull requests were read", board.render_daily([], "u"))

    def test_main_writes_daily_txt(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = os.path.join(tmp, "d.json")
            with open(fixture, "w", encoding="utf-8") as f:
                json.dump({"o/r": [pr(1, "alice")]}, f)
            board.main(["--fixture", fixture, "--out", tmp, "--url", "u", "--now", "t"])
            with open(os.path.join(tmp, "daily.txt"), encoding="utf-8") as f:
                self.assertIn("1 can merge now", f.read())


class MergedAuthorsTest(unittest.TestCase):
    @staticmethod
    def answer(logins, more=False, cursor=None, private=False):
        nodes = [{"author": {"login": login} if login else None} for login in logins]
        return {"data": {"repository": {"isPrivate": private, "pullRequests": {"pageInfo": {"hasNextPage": more, "endCursor": cursor}, "nodes": nodes}}}}

    def test_it_follows_the_pages_and_collects_logins_in_lower_case(self):
        def run(owner, name, after, size):
            return self.answer(["Alice", None], True, "c") if after is None else self.answer(["bob"])

        self.assertEqual(board.fetch_merged(["o/r"], run), {"o/r": {"alice", "bob"}})

    def test_a_private_repo_is_dropped_and_not_named(self):
        def run(owner, name, after, size):
            return self.answer(["x"], private=name == "hidden")

        merged = board.fetch_merged(["o/hidden", "o/open"], run)
        self.assertEqual(list(merged), ["o/open"])

    def test_a_repo_whose_history_cannot_be_read_gives_none_not_a_crash(self):
        def run(owner, name, after, size):
            raise subprocess.CalledProcessError(1, "gh")

        self.assertEqual(board.fetch_merged(["o/r"], run), {"o/r": None})

    def test_a_repo_without_history_makes_nobody_there_a_contributor(self):
        self.assertEqual(board.classify("o/r", pr(1, "carol"), TEAM, {"o/r": None})["group"], "other")


class TeamFileTest(unittest.TestCase):
    path = os.path.join(os.path.dirname(HERE), "team.json")

    def load(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def test_the_file_is_a_plain_list_of_logins_and_nothing_else(self):
        logins = self.load()
        self.assertIsInstance(logins, list)
        self.assertGreaterEqual(len(logins), 8)
        self.assertTrue(all(isinstance(login, str) and login and " " not in login for login in logins), logins)

    def test_no_login_is_listed_twice(self):
        lowered = [login.lower() for login in self.load()]
        self.assertEqual(len(lowered), len(set(lowered)))

    def test_load_team_gives_lower_case_logins(self):
        team = board.load_team(self.path)
        self.assertIn("chriskearney", team)
        self.assertIn("djdeveloperr", team, "a login written with capitals is stored in lower case")
        self.assertTrue(all(login == login.lower() for login in team))


class RunTest(unittest.TestCase):
    def test_main_reads_the_new_fixture_shape_and_writes_the_groups(self):
        data = {"prs": {"o/r": [pr(1, "alice"), pr(2, "carol"), pr(3, "dave")]}, "merged": {"o/r": ["carol"]}}
        with tempfile.TemporaryDirectory() as tmp:
            fixture = os.path.join(tmp, "d.json")
            team = os.path.join(tmp, "team.json")
            with open(fixture, "w", encoding="utf-8") as f:
                json.dump(data, f)
            with open(team, "w", encoding="utf-8") as f:
                json.dump(["alice"], f)
            board.main(["--fixture", fixture, "--team", team, "--out", tmp, "--url", "u", "--now", "t"])
            with open(os.path.join(tmp, "board.json"), encoding="utf-8") as f:
                rows = json.load(f)["rows"]
            self.assertEqual([(r["number"], r["group"]) for r in rows], [(1, "team"), (2, "contributor"), (3, "other")])


if __name__ == "__main__":
    unittest.main()
