import json, os, time, urllib.parse, urllib.request

KEY = os.environ["STEAM_API_KEY"]
VANITY = os.environ["STEAM_VANITY"]
APPID = 292030
BASE = "https://api.steampowered.com"

def get(path, params):
    url = f"{BASE}/{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)

def post(path, data):
    body = urllib.parse.urlencode(data).encode()
    with urllib.request.urlopen(urllib.request.Request(f"{BASE}/{path}", body), timeout=30) as r:
        return json.load(r)

steamid = get("ISteamUser/ResolveVanityURL/v1/", {"key": KEY, "vanityurl": VANITY})["response"]["steamid"]
files = get("IPublishedFileService/GetUserFiles/v1/", {
    "key": KEY, "steamid": steamid, "appid": APPID, "numperpage": 100,
}).get("response", {}).get("publishedfiledetails", [])
ids = [f["publishedfileid"] for f in files]

details = []
if ids:
    data = {"itemcount": len(ids)}
    for i, pid in enumerate(ids):
        data[f"publishedfileids[{i}]"] = pid
    details = post("ISteamRemoteStorage/GetPublishedFileDetails/v1/", data)["response"]["publishedfiledetails"]

items = [{
    "id": d["publishedfileid"], "title": d.get("title"),
    "subscribers": d.get("subscriptions"), "favorites": d.get("favorited"),
    "views": d.get("views"), "comments": d.get("num_comments_public"),
    "time_updated": d.get("time_updated"),
    "url": f"https://steamcommunity.com/sharedfiles/filedetails/?id={d['publishedfileid']}",
} for d in details]

news = get("ISteamNews/GetNewsForApp/v2/", {"appid": APPID, "count": 10, "maxlength": 1500}).get("appnews", {}).get("newsitems", [])
news = [{"title": n["title"], "date": n["date"], "url": n["url"], "text": n["contents"]} for n in news]

os.makedirs("data", exist_ok=True)
json.dump({"generated_at": int(time.time()), "items": items, "news": news},
          open("data/stats.json", "w"), ensure_ascii=False, indent=1)
