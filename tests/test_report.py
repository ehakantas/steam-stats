"""Tests for report.build_report (no files: dicts in, dicts and summary lines out)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import report  # noqa: E402

NOW = 1792860000
DAY = 86400


def stats(*items):
    return {"generated_at": NOW - 3600, "news": [], "items": [
        {"id": i, "title": f"Mod {i}", "subscribers": s, "favorites": 0, "views": 0, "time_updated": t}
        for i, s, t in items]}


def comments(gen=NOW - 600, errors=(), **items):
    return {"generated_at": gen, "errors": [{"id": e} for e in errors],
            "items": {k: {"total": v[0], "seen_count": v[0], "keywords_total": v[1]} for k, v in items.items()}}


def state(last_run=1, items=(), last_comments=None, **kw):
    s = {"last_run": last_run, "last_generated_at": 0,
         "last_items": [{"id": i, "title": f"Mod {i}", "subs": n, "favs": 0, "views": 0, "time_updated": 0} for i, n in items]}
    if last_comments is not None:
        s["last_comments"] = {k: {"total": v[0], "seen": v[0], "kw": v[1]} for k, v in last_comments.items()}
    s.update(kw)
    return s


def line(lines, key):
    return next(x for x in lines if x.startswith(key + " "))[len(key) + 1:]


class ReportTests(unittest.TestCase):
    def test_subs_drop_and_removed_mod(self):
        run, st, lines = report.build_report(
            stats(("a", 8, 0)), state(items=[("a", 10), ("gone", 3)], last_comments={"a": (0, {})}),
            comments(a=(0, {})), now=NOW)
        alerts = line(lines, "ALERTS")
        self.assertIn("REMOVED/HIDDEN: Mod gone", alerts)
        self.assertIn("SUBS DROP: Mod a 10→8 (-2)", alerts)
        self.assertEqual(line(lines, "TOTAL_SUBS"), "8 (-5 vs run 1)")
        self.assertEqual((run["run"], st["last_run"]), (2, 2))

    def test_complaint_opens_issue_then_tracks_update(self):
        s1 = state(items=[("a", 1)], last_comments={"a": (1, {})})
        _, st, lines = report.build_report(stats(("a", 1, 0)), s1, comments(a=(3, {"crash": 2, "update": 1})), now=NOW)
        self.assertIn("COMMENT REPORT: Mod a — 2 new comment(s)", line(lines, "ALERTS"))
        self.assertEqual(line(lines, "COMMENTS"), "Mod a +2 (crash×2, update×1)")
        (iss,) = st["issues"].values()
        self.assertEqual((iss["status"], iss["keywords"]), ("open", {"crash": 2}))

        # The mod is updated after the issue opened and a new complaint arrives: "still reported".
        _, st2, lines2 = report.build_report(
            stats(("a", 1, NOW + 100)), st, comments(a=(4, {"crash": 3, "update": 1})), now=NOW + DAY)
        self.assertIn("STILL REPORTED AFTER UPDATE: Mod a", line(lines2, "ALERTS"))
        (iss2,) = st2["issues"].values()
        self.assertEqual((iss2["keywords"], iss2["after_update_keywords"]), ({"crash": 3}, {"crash": 1}))

    def test_closed_issue_is_not_reopened_into_same_id(self):
        closed = {"a-r001": {"item_id": "a", "title": "Mod a", "status": "closed", "opened_at": 1}}
        _, st, _ = report.build_report(
            stats(("a", 1, 0)), state(items=[("a", 1)], last_comments={"a": (0, {})}, issues=closed),
            comments(a=(1, {"bug": 1})), now=NOW)
        self.assertEqual(st["issues"]["a-r001"]["status"], "closed")
        self.assertIn("a-r002", st["issues"])

    def test_first_comment_check_is_a_baseline(self):
        _, st, lines = report.build_report(stats(("a", 1, 0)), state(), comments(a=(5, {"crash": 5})), now=NOW)
        self.assertEqual(line(lines, "COMMENTS"), "baseline set")
        self.assertEqual(line(lines, "TOTAL_SUBS"), "1 (no previous run)")
        self.assertEqual(st["issues"], {})
        self.assertEqual(st["last_comments"]["a"]["seen"], 5)

    def test_missing_comments_raise_streak_alert(self):
        _, st, lines = report.build_report(stats(("a", 1, 0)), state(items=[("a", 1)], comments_bad_streak=1), None, now=NOW)
        self.assertEqual(st["comments_bad_streak"], 2)
        self.assertIn("COMMENTS UNCHECKED", line(lines, "ALERTS"))
        self.assertNotIn("last_comments", st)

    def test_unreadable_mod_keeps_old_baseline(self):
        _, st, lines = report.build_report(
            stats(("a", 1, 0)), state(items=[("a", 1)], last_comments={"a": (1, {})}),
            comments(errors=["a"], a=(9, {})), now=NOW)
        self.assertTrue(line(lines, "COMMENTS_STATUS").startswith("errors"))
        self.assertEqual(st["last_comments"]["a"]["seen"], 1)

    def test_stale_comments_and_patch_alert(self):
        _, _, lines = report.build_report(
            stats(("a", 1, 0)), state(items=[("a", 1)], last_comments={"a": (0, {})}),
            comments(gen=NOW - 50 * 3600, a=(0, {})), patch="🔴 PATCH", now=NOW)
        self.assertEqual(line(lines, "COMMENTS"), "not checked")
        self.assertTrue(line(lines, "ALERTS").endswith("🔴 PATCH"))


if __name__ == "__main__":
    unittest.main()
