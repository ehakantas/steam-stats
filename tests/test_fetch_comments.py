"""fetch_comments.py testleri (ağ gerektirmez; sahte Steam yanıtlarıyla çalışır).

çalıştırma: python3 -m unittest discover -s tests -v
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import fetch_comments as fc  # noqa: E402


def comment_html(cid, ts, text, author="biri"):
    return (
        f'<div class="commentthread_comment responsive_body_text" id="comment_{cid}">'
        f'<div class="commentthread_comment_content">'
        f'<div class="commentthread_comment_author"><bdi>{author}</bdi>'
        f'<span class="commentthread_comment_timestamp" data-timestamp="{ts}" title="x">now</span></div>'
        f'<div class="commentthread_comment_text">{text}</div>'
        f"</div></div>"
    )


def page(*comments, total, start=0, size=2):
    return json.dumps({
        "success": True, "start": start, "pagesize": size, "total_count": total,
        "comments_html": "".join(comments),
    })


DAY = 86400
T0 = 1792500000  # sabit bir zaman damgası


class ParseTests(unittest.TestCase):
    def test_parse_extracts_text_and_ts(self):
        html = comment_html(1, T0, "Game <b>crash</b> after patch") + comment_html(2, T0 - 5, "thanks!")
        out = fc.parse_comments(html)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["ts"], T0)
        self.assertEqual(out[0]["text"], "Game crash after patch")
        self.assertNotEqual(out[0]["key"], out[1]["key"])

    def test_parse_empty(self):
        self.assertEqual(fc.parse_comments(""), [])

    def test_key_is_stable(self):
        a = fc.parse_comments(comment_html(7, T0, "x"))[0]["key"]
        b = fc.parse_comments(comment_html(7, T0, "x"))[0]["key"]
        self.assertEqual(a, b)


class StatsTests(unittest.TestCase):
    def test_keywords_and_days(self):
        cs = [{"ts": T0, "text": "it crash and is broken"}, {"ts": T0 + DAY, "text": "ok"}]
        self.assertEqual(fc.keyword_counts(cs), {"crash": 1, "broken": 1})
        self.assertEqual(sum(fc.day_counts(cs).values()), 2)


class FetchTests(unittest.TestCase):
    ITEM = {"id": "111", "title": "Mod A", "creator": "76561198000000000"}

    def make_get(self, pages):
        calls = []

        def get(url, params=None, retries=2):
            calls.append(params)
            return pages[len(calls) - 1]
        get.calls = calls
        return get

    def test_first_run_paginates_all(self):
        pages = [
            page(comment_html(4, T0, "crash"), comment_html(3, T0 - 1, "hi"), total=4),
            page(comment_html(2, T0 - 2, "fix?"), comment_html(1, T0 - 3, "yo"), total=4, start=2),
        ]
        get = self.make_get(pages)
        fresh, total = fc.fetch_item_comments(self.ITEM, [], get=get, sleep=lambda s: None)
        self.assertEqual(len(fresh), 4)
        self.assertEqual(total, 4)
        self.assertEqual([c["start"] for c in get.calls], [0, 2])

    def test_incremental_stops_at_known(self):
        old = fc.parse_comments(comment_html(3, T0 - 1, "hi"))[0]["key"]
        pages = [page(comment_html(4, T0, "crash"), comment_html(3, T0 - 1, "hi"), total=4)]
        get = self.make_get(pages)
        fresh, _ = fc.fetch_item_comments(self.ITEM, [old], get=get, sleep=lambda s: None)
        self.assertEqual(len(fresh), 1)
        self.assertEqual(len(get.calls), 1)  # ikinci sayfaya gitmedi

    def test_run_isolates_item_errors_and_stores_no_text(self):
        good = {"id": "111", "title": "A", "creator": "1" * 17}
        bad = {"id": "222", "title": "B", "creator": "1" * 17}
        responses = {
            "111": page(comment_html(1, T0, "secret text crash", author="kimlik"), total=1),
        }

        def get(url, params=None, retries=2):
            if "/111/" in url:
                return responses["111"]
            raise RuntimeError("boom")

        out = fc.run({"items": [good, bad]}, {}, get=get, sleep=lambda s: None, now=lambda: 5)
        self.assertEqual(out["items"]["111"]["new_since_last"], 1)
        self.assertEqual(out["items"]["111"]["keywords_total"], {"crash": 1})
        self.assertEqual(len(out["errors"]), 1)
        blob = json.dumps(out)
        self.assertNotIn("secret", blob)   # metin saklanmamalı
        self.assertNotIn("kimlik", blob)   # yazar adı saklanmamalı

    def test_second_run_counts_zero_new(self):
        item = {"id": "111", "title": "A", "creator": "1" * 17}
        p = page(comment_html(1, T0, "hello"), total=1)
        get = lambda url, params=None, retries=2: p  # noqa: E731
        first = fc.run({"items": [item]}, {}, get=get, sleep=lambda s: None)
        second = fc.run({"items": [item]}, first, get=get, sleep=lambda s: None)
        self.assertEqual(first["items"]["111"]["new_since_last"], 1)
        self.assertEqual(second["items"]["111"]["new_since_last"], 0)
        self.assertEqual(second["items"]["111"]["total"], 1)


if __name__ == "__main__":
    unittest.main()
