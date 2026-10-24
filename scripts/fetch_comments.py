"""Workshop comment statistics.

For each mod, reads Steam's comment endpoint (JSON + HTML fragment), counts only
NEW comments and writes aggregate statistics to data/comments.json.

Privacy: the repo is public, so comment text and author names are NOT stored.
A shortened hash of each comment is kept to skip duplicates.

Uses only the standard library (no pip install needed in the workflow).

usage: python3 scripts/fetch_comments.py [stats.json] [comments.json]
Fault tolerant: if one mod fails the others are still processed and the output is still written.
"""
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime
from html.parser import HTMLParser

from common import http_get
from config import COMPLAINT_KEYWORDS, INFO_KEYWORDS, TR

RENDER = "https://steamcommunity.com/comment/PublishedFile_Public/render/{owner}/{feature}/"
ITEM_PAGE = "https://steamcommunity.com/sharedfiles/filedetails/?id={id}"

PAGE_SIZE = 50        # requested page size; if Steam returns a different one, that is used
MAX_PAGES = 30        # upper bound on the first run (30 x 50 = 1500 comments)
SLEEP_SECONDS = 1.0   # delay between requests
SEEN_LIMIT = 5000     # max number of hashes stored per mod

KEYWORDS = COMPLAINT_KEYWORDS + INFO_KEYWORDS
_KW_RE = {kw: re.compile(r"\b" + re.escape(kw)) for kw in KEYWORDS}


# --------------------------------------------------------------------------
# HTML parsing (stdlib)
# --------------------------------------------------------------------------
class _CommentParser(HTMLParser):
    """Extracts (id, timestamp, text) triples from Steam comment HTML."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.comments = []
        self._cur = None
        self._depth = 0          # div depth inside the comment block
        self._text_depth = None  # depth of the text block
        self._text_parts = []

    @staticmethod
    def _classes(attrs):
        return (dict(attrs).get("class") or "").split()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = self._classes(attrs)
        if tag == "div":
            if self._cur is None and "commentthread_comment" in classes:
                self._cur = {"id": a.get("id") or "", "ts": None, "text": ""}
                self._depth = 1
                return
            if self._cur is not None:
                self._depth += 1
                if "commentthread_comment_text" in classes:
                    self._text_depth = self._depth
                    self._text_parts = []
        if self._cur is not None and "commentthread_comment_timestamp" in classes:
            raw = a.get("data-timestamp")
            if raw and raw.isdigit():
                self._cur["ts"] = int(raw)

    def handle_endtag(self, tag):
        if tag != "div" or self._cur is None:
            return
        if self._text_depth == self._depth:
            self._cur["text"] = " ".join("".join(self._text_parts).split())
            self._text_depth = None
        self._depth -= 1
        if self._depth == 0:
            self.comments.append(self._cur)
            self._cur = None

    def handle_data(self, data):
        if self._cur is not None and self._text_depth is not None:
            self._text_parts.append(data)


def parse_comments(html):
    """Returns a [{'key', 'ts', 'text'}] list from the HTML fragment (newest first)."""
    p = _CommentParser()
    p.feed(html or "")
    p.close()
    out = []
    for c in p.comments:
        # Stable key: the element id if present, otherwise timestamp + text.
        basis = c["id"] or f"{c['ts']}|{c['text']}"
        key = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]
        out.append({"key": key, "ts": c["ts"], "text": c["text"]})
    return out


# --------------------------------------------------------------------------
# Network layer (http_get lives in common.py)
# --------------------------------------------------------------------------
def find_owner(item, get=http_get):
    """Owner steamid for the comment endpoint.

    Uses the 'creator' field from stats.json first; otherwise it is read from
    the comment setup code on the mod page (fallback).
    """
    if item.get("creator"):
        return str(item["creator"])
    page = get(ITEM_PAGE.format(id=item["id"]))
    m = re.search(r"PublishedFile_Public[/_](\d{15,20})[/_]", page)
    if not m:
        m = re.search(r'"owner"\s*:\s*"(\d+)"', page)
    if not m:
        raise RuntimeError("comment owner not found")
    return m.group(1)


def _int(v, default=None):
    """Steam returns some numbers as strings ("50"); converts them to int."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def fetch_item_comments(item, known, get=http_get, sleep=time.sleep):
    """Returns a mod's new comments: (new_comments, total_count).

    Comments arrive newest first. Stops at the first page that is fully known.
    """
    owner = find_owner(item, get)
    url = RENDER.format(owner=owner, feature=item["id"])
    known, this_run = set(known), set()
    fresh, total, start = [], None, 0

    for _ in range(MAX_PAGES):
        data = json.loads(get(url, {"start": start, "count": PAGE_SIZE}))
        if not data.get("success", True):
            raise RuntimeError("Steam returned success=false")
        total = _int(data.get("total_count"), total)
        page = parse_comments(data.get("comments_html", ""))
        if not page:
            break
        # A comment posted mid-scan shifts page boundaries, so a page can repeat the previous
        # page's tail. Those repeats are skipped via this_run but must not stop the scan:
        # only comments known from earlier runs mean we have caught up.
        new_here = [c for c in page if c["key"] not in known and c["key"] not in this_run]
        this_run.update(c["key"] for c in new_here)
        fresh.extend(new_here)
        if any(c["key"] in known for c in page):
            break  # reached comments known from earlier runs
        step = _int(data.get("pagesize")) or len(page)
        start += step
        if total is not None and start >= total:
            break
        sleep(SLEEP_SECONDS)
    return fresh, total


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------
def keyword_counts(comments):
    """Counts how many comments contain each keyword."""
    c = Counter()
    for cm in comments:
        t = cm["text"].lower().replace("’", "'")  # curly apostrophe: doesn’t -> doesn't
        for kw, rx in _KW_RE.items():
            if rx.search(t):
                c[kw] += 1
    return dict(c)


def day_counts(comments):
    """Counts comments per Istanbul (TR) calendar day."""
    c = Counter()
    for cm in comments:
        if cm["ts"]:
            c[datetime.fromtimestamp(cm["ts"], TR).strftime("%Y-%m-%d")] += 1
    return dict(c)


def merge_counts(old, new):
    out = Counter(old or {})
    out.update(new or {})
    return dict(out)


def update_item(prev, item, fresh, total):
    """Previous state + new comments -> new state (comment text is not stored)."""
    prev = prev or {}
    seen = (prev.get("seen") or []) + [c["key"] for c in fresh]
    ts = [c["ts"] for c in fresh if c["ts"]]
    return {
        "title": item.get("title"),
        "total": total if total is not None else prev.get("total"),
        "new_since_last": len(fresh),
        # Cumulative count of comments ever seen; unlike "total" it never drops when a
        # comment is deleted, so report.py can diff it without deletions hiding new ones.
        "seen_count": (prev.get("seen_count") or 0) + len(fresh),
        "new_keywords": keyword_counts(fresh),
        "keywords_total": merge_counts(prev.get("keywords_total"), keyword_counts(fresh)),
        "daily": merge_counts(prev.get("daily"), day_counts(fresh)),
        "last_comment_ts": max(ts) if ts else prev.get("last_comment_ts"),
        "seen": seen[-SEEN_LIMIT:],
    }


def run(stats, previous, get=http_get, sleep=time.sleep, now=time.time):
    prev_items = (previous or {}).get("items", {})
    items, errors = {}, []
    for item in stats.get("items", []):
        pid = item["id"]
        prev = prev_items.get(pid)
        try:
            fresh, total = fetch_item_comments(
                item, (prev or {}).get("seen", []), get=get, sleep=sleep)
            items[pid] = update_item(prev, item, fresh, total)
        except Exception as e:  # one failing mod must not stop the others
            errors.append({"id": pid, "error": str(e)})
            if prev:
                items[pid] = {**prev, "new_since_last": 0, "new_keywords": {}}
        sleep(SLEEP_SECONDS)
    return {"generated_at": int(now()), "items": items, "errors": errors}


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    stats_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "..", "data", "stats.json")
    out_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(here, "..", "data", "comments.json")

    stats = json.load(open(stats_path))
    # An empty/corrupt file (e.g. cleared by hand) is treated like a first run.
    try:
        previous = json.load(open(out_path))
        if not isinstance(previous, dict):
            raise ValueError("expected a JSON object")
    except FileNotFoundError:
        previous = {}
    except ValueError as e:
        print(f"WARNING: could not read {out_path} ({e}); starting from scratch", file=sys.stderr)
        previous = {}
    result = run(stats, previous)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    json.dump(result, open(out_path, "w"), ensure_ascii=False, indent=1)

    new = sum(i["new_since_last"] for i in result["items"].values())
    print(f"comments: {len(result['items'])} mods, {new} new comments, {len(result['errors'])} errors")
    for e in result["errors"]:
        print(f"  ERROR {e['id']}: {e['error']}", file=sys.stderr)


if __name__ == "__main__":
    main()
