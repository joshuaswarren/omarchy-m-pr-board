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
from test_board import node

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
        order = [html.index(h) for h in ("Omarchy M team (1 open, 1 can merge now)", "Existing contributors (1 open, 1 can merge now)", "Everyone else (1 open, 1 can merge now)")]
        self.assertEqual(order, sorted(order))

    def test_an_empty_section_says_none(self):
        html = board.render_html(board.rows_from({"o/r": [pr(1, "alice")]}, TEAM, {}), "t")
        self.assertIn("Existing contributors (0 open", html)
        self.assertIn("<p>None.</p>", html)

    def test_the_page_shows_the_login_only_never_a_name(self):
        rows = self.rows()
        self.assertTrue(all(set(r) .isdisjoint({"team_name", "name"}) for r in rows))
        self.assertIn("<td>alice</td>", board.render_html(rows, "t"))

    def test_the_summary_counts_the_sections(self):
        self.assertIn("Team 1, existing contributors 1, everyone else 1.", board.render_summary(self.rows(), "u"))


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
