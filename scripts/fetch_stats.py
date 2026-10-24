"""Fetch the Workshop counters and game news into data/stats.json, and append data/history.csv.

Each item also gets "delta": the change in every counter since the previous stats.json (the copy
on disk before this run), so later steps do not need git to know what changed. A counter's delta
is null when the mod or the counter is missing on either side.
"""
import csv, datetime, json, os, sys, time

from common import get_json
from config import APPID, TR

BASE = "https://api.steampowered.com"
here = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(here, "..", "data")
STATS = os.path.join(DATA, "stats.json")
HISTORY = os.path.join(DATA, "history.csv")
COUNTERS = ("subscribers", "favorites", "views", "comments")
HISTORY_FIELDS = ["date", "generated_at", "id", "title", *COUNTERS]


def get(path, params):
    return get_json(f"{BASE}/{path}", params)


def load_previous():
    try:
        prev = json.load(open(STATS))
        return prev if isinstance(prev, dict) else {}
    except (OSError, ValueError):
        return {}


def add_deltas(items, prev):
    P = {i.get("id"): i for i in prev.get("items") or [] if isinstance(i, dict)}
    for i in items:
        old = P.get(i["id"], {})
        i["delta"] = {k: i[k] - old[k] if isinstance(i.get(k), int) and isinstance(old.get(k), int) else None
                      for k in COUNTERS}


def append_history(items, generated_at):
    """One row per mod per Istanbul day; a second run on the same day replaces that day's rows."""
    date = datetime.datetime.fromtimestamp(generated_at, TR).date().isoformat()
    try:
        with open(HISTORY, newline="") as f:
            rows = [r for r in csv.DictReader(f) if r.get("date") != date]
    except OSError:
        rows = []
    rows += [{"date": date, "generated_at": generated_at, "id": i["id"], "title": i["title"],
              **{k: i[k] for k in COUNTERS}} for i in items]
    with open(HISTORY, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HISTORY_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main():
    key, vanity = os.environ["STEAM_API_KEY"], os.environ["STEAM_VANITY"]
    steamid = get("ISteamUser/ResolveVanityURL/v1/", {"key": key, "vanityurl": vanity})["response"]["steamid"]
    # GetUserFiles returns full PublishedFileDetails (incl. num_comments_public, which the older
    # ISteamRemoteStorage/GetPublishedFileDetails call never returned), so one call is enough.
    files = get("IPublishedFileService/GetUserFiles/v1/", {
        "key": key, "steamid": steamid, "appid": APPID, "numperpage": 100,
    }).get("response", {}).get("publishedfiledetails", [])
    if not files:
        # An empty list is almost always a transient API problem; committing it would make the
        # report flag every mod as removed. Fail the step so nothing is committed this run.
        sys.exit("GetUserFiles returned no items; not writing stats.json")

    items = [{
        "id": d["publishedfileid"], "title": d.get("title"),
        "subscribers": d.get("subscriptions"), "favorites": d.get("favorited"),
        "views": d.get("views"), "comments": d.get("num_comments_public"),
        "time_updated": d.get("time_updated"), "creator": d.get("creator"),
        "url": f"https://steamcommunity.com/sharedfiles/filedetails/?id={d['publishedfileid']}",
    } for d in files]

    news = get("ISteamNews/GetNewsForApp/v2/", {"appid": APPID, "count": 10, "maxlength": 300, "feeds": "steam_community_announcements"}).get("appnews", {}).get("newsitems", [])
    news = [{"title": n["title"], "date": n["date"], "url": n["url"], "text": n["contents"]} for n in news]

    prev = load_previous()
    add_deltas(items, prev)
    generated_at = int(time.time())
    os.makedirs(DATA, exist_ok=True)
    json.dump({"generated_at": generated_at, "previous_generated_at": prev.get("generated_at"),
               "items": items, "news": news},
              open(STATS, "w"), ensure_ascii=False, indent=1)
    append_history(items, generated_at)
    print(f"stats: {len(items)} items, comment counts: {[i['comments'] for i in items]}")


if __name__ == "__main__":
    main()
