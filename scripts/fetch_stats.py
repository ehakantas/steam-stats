import json, os, sys, time, urllib.parse, urllib.request

KEY = os.environ["STEAM_API_KEY"]
VANITY = os.environ["STEAM_VANITY"]
APPID = 292030
BASE = "https://api.steampowered.com"

def get(path, params):
    url = f"{BASE}/{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)

steamid = get("ISteamUser/ResolveVanityURL/v1/", {"key": KEY, "vanityurl": VANITY})["response"]["steamid"]
# GetUserFiles returns full PublishedFileDetails (incl. num_comments_public, which the older
# ISteamRemoteStorage/GetPublishedFileDetails call never returned), so one call is enough.
files = get("IPublishedFileService/GetUserFiles/v1/", {
    "key": KEY, "steamid": steamid, "appid": APPID, "numperpage": 100,
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

os.makedirs("data", exist_ok=True)
json.dump({"generated_at": int(time.time()), "items": items, "news": news},
          open("data/stats.json", "w"), ensure_ascii=False, indent=1)
print(f"stats: {len(items)} items, comment counts: {[i['comments'] for i in items]}")
