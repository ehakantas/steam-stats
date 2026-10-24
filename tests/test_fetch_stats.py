"""Tests for the delta and history helpers in fetch_stats.py (no network)."""
import csv
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import fetch_stats as fs  # noqa: E402

T0 = 1792500000  # 2026-10-20 TR


def item(id_, subs, views=10, title="Mod"):
    return {"id": id_, "title": title, "subscribers": subs, "favorites": 1, "views": views, "comments": 0}


class DeltaTests(unittest.TestCase):
    def test_deltas_vs_previous(self):
        items = [item("a", 12, views=None), item("new", 3)]
        fs.add_deltas(items, {"items": [item("a", 10), item("gone", 5)]})
        self.assertEqual(items[0]["delta"], {"subscribers": 2, "favorites": 0, "views": None, "comments": 0})
        self.assertEqual(items[1]["delta"], dict.fromkeys(fs.COUNTERS))  # new mod: no baseline

    def test_no_previous_file(self):
        items = [item("a", 1)]
        fs.add_deltas(items, {})
        self.assertEqual(items[0]["delta"]["subscribers"], None)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.path = os.path.join(d, "history.csv")
        patcher = mock.patch.object(fs, "HISTORY", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def rows(self):
        with open(self.path, newline="") as f:
            return list(csv.DictReader(f))

    def test_appends_one_row_per_mod_per_day(self):
        fs.append_history([item("a", 1, title='Comma, "quote"')], T0)
        fs.append_history([item("a", 2)], T0 + 86400)
        rows = self.rows()
        self.assertEqual([(r["date"], r["subscribers"]) for r in rows], [("2026-10-20", "1"), ("2026-10-21", "2")])
        self.assertEqual(rows[0]["title"], 'Comma, "quote"')

    def test_same_day_run_replaces_that_day(self):
        fs.append_history([item("a", 1), item("b", 1)], T0)
        fs.append_history([item("a", 5)], T0 + 600)
        self.assertEqual([(r["id"], r["subscribers"]) for r in self.rows()], [("a", "5")])


if __name__ == "__main__":
    unittest.main()
