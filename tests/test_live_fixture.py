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
    DATA = json.load(f)


class LiveFixtureTest(unittest.TestCase):
    def test_every_pr_becomes_one_row_with_every_field(self):
        rows = board.rows_from(DATA)
        self.assertEqual(len(rows), sum(len(nodes) for nodes in DATA.values()))
        fields = {"repo", "number", "title", "url", "owner", "merge", "checks", "approvals", "maclab", "kind", "blocker", "can_merge", "rc_blocker", "updated"}
        for r in rows:
            self.assertEqual(set(r), fields)
            self.assertTrue(r["blocker"])

    def test_a_pr_that_can_merge_has_no_other_blocker(self):
        for r in board.rows_from(DATA):
            if r["can_merge"]:
                self.assertEqual((r["kind"], r["merge"]), ("None", "mergeable"))
            else:
                self.assertNotEqual(r["kind"], "None")

    def test_the_page_and_the_summary_render(self):
        rows = board.rows_from(DATA)
        self.assertIn(f"{len(rows)} open", board.render_html(rows, "t"))
        self.assertIn(f"{len(rows)} open", board.render_summary(rows, "u"))


if __name__ == "__main__":
    unittest.main()
