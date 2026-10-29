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
