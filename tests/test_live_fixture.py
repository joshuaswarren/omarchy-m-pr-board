"""The board against one real answer from GitHub (2026-10-29, public repos only): every PR becomes a row, nothing raises."""

from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import board

with open(os.path.join(HERE, "fixtures", "live-2026-10-29.json"), encoding="utf-8") as f:
    SAVED = json.load(f)
DATA = SAVED["prs"]
MERGED = {repo: None if logins is None else set(logins) for repo, logins in SAVED["merged"].items()}
TEAM = board.load_team()


class LiveFixtureTest(unittest.TestCase):
    def test_every_pr_becomes_one_row_with_every_field(self):
        rows = board.rows_from(DATA, TEAM, MERGED)
        self.assertEqual(len(rows), sum(len(nodes) for nodes in DATA.values()))
        fields = {"group", "draft", "state", "repo", "number", "title", "url", "owner", "merge", "checks", "approvals", "maclab", "kind", "blocker", "can_merge", "rc_blocker", "updated", "sha", "maclab_title", "waiting", "evidence", "act"}
        for r in rows:
            self.assertEqual(set(r), fields)
            self.assertTrue(r["blocker"])

    def test_a_pr_that_can_merge_has_no_other_blocker(self):
        for r in board.rows_from(DATA, TEAM, MERGED):
            if r["can_merge"]:
                self.assertEqual((r["kind"], r["merge"]), ("None", "mergeable"))
            else:
                self.assertNotEqual(r["kind"], "None")

    def test_the_team_section_holds_only_team_logins_and_the_others_none(self):
        rows = board.rows_from(DATA, TEAM, MERGED)
        for r in rows:
            self.assertEqual(r["group"] == "team", r["owner"].lower() in TEAM, r["owner"])
            if r["group"] == "contributor":
                self.assertIn(r["owner"].lower(), MERGED[r["repo"]])

    def test_the_page_and_the_summary_render(self):
        rows = board.rows_from(DATA, TEAM, MERGED)
        self.assertIn(f"<strong>{len(rows)}</strong> open", board.render_html(rows, "t"))
        self.assertIn(f"{len(rows)} open", board.render_summary(rows, "u"))


if __name__ == "__main__":
    unittest.main()
