"""Send a short stats summary to WhatsApp via CallMeBot (best effort, personal notification).

Needs CALLMEBOT_PHONE and CALLMEBOT_APIKEY in the environment (DASHBOARD_URL is optional); exits quietly when they are
missing. Never prints the URL, phone, key or response body: only the HTTP status.
Run before the commit step so the previous stats are still HEAD:data/stats.json.
"""
import datetime, json, os, subprocess, sys, urllib.parse, urllib.request, zoneinfo

here = os.path.dirname(os.path.abspath(__file__))
phone = os.environ.get("CALLMEBOT_PHONE", "").strip()
apikey = os.environ.get("CALLMEBOT_APIKEY", "").strip()
if not phone or not apikey:
    print("CallMeBot secrets not set; skipping")
    sys.exit(0)

stats = json.load(open(os.path.join(here, "..", "data", "stats.json")))
try:
    prev = json.loads(subprocess.check_output(
        ["git", "show", "HEAD:data/stats.json"], stderr=subprocess.DEVNULL))
except Exception:
    prev = {}
P = {i["id"]: i for i in prev.get("items", [])}

try:
    comments = json.load(open(os.path.join(here, "..", "data", "comments.json")))["items"]
except Exception:
    comments = {}
new_comments = sum(c.get("new_since_last") or 0 for c in comments.values())


def delta(cur, old):
    return f" ({cur - old:+d})" if old is not None and cur != old else ""


lines = []
tot = {"subscribers": 0, "views": 0}
for i in stats["items"][:8]:
    old = P.get(i["id"], {})
    lines.append(f"*{i['title']}*: {i['subscribers']}{delta(i['subscribers'], old.get('subscribers'))} abone, "
                 f"{i['views']}{delta(i['views'], old.get('views'))} görüntülenme")
    tot["subscribers"] += i["subscribers"]
    tot["views"] += i["views"]

dashboard = os.environ.get("DASHBOARD_URL", "").strip()

now = datetime.datetime.now(zoneinfo.ZoneInfo("Europe/Istanbul")).strftime("%d.%m %H:%M")
text = "\n".join([f"Steam stats {now}", *lines,
                  f"Toplam: {tot['subscribers']} abone, {tot['views']} görüntülenme",
                  f"Yeni yorum: {new_comments}",
                  *([f"Dashboard: {dashboard}"] if dashboard else [])])

url = ("https://api.callmebot.com/whatsapp.php?"
       + urllib.parse.urlencode({"phone": phone, "text": text, "apikey": apikey}, quote_via=urllib.parse.quote))
try:
    with urllib.request.urlopen(url, timeout=30) as r:
        print("CallMeBot HTTP", r.status)
except urllib.error.HTTPError as e:
    print("CallMeBot HTTP", e.code)
    sys.exit(1)
except Exception as e:
    print("CallMeBot failed:", type(e).__name__)
    sys.exit(1)
