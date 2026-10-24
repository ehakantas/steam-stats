"""Send a short stats summary to WhatsApp via CallMeBot (best effort, personal notification).

Needs CALLMEBOT_PHONE and CALLMEBOT_APIKEY in the environment (DASHBOARD_URL is optional); exits quietly when they are
missing. Never prints the URL, phone, key or response body: only the HTTP status.
The changes since the previous run come from the "delta" fields that fetch_stats.py writes.
"""
import datetime, json, os, sys, urllib.parse, urllib.request

from config import TR

here = os.path.dirname(os.path.abspath(__file__))
phone = os.environ.get("CALLMEBOT_PHONE", "").strip()
apikey = os.environ.get("CALLMEBOT_APIKEY", "").strip()
if not phone or not apikey:
    print("CallMeBot secrets not set; skipping")
    sys.exit(0)

stats = json.load(open(os.path.join(here, "..", "data", "stats.json")))

try:
    comments = json.load(open(os.path.join(here, "..", "data", "comments.json")))["items"]
except Exception:
    comments = {}
new_comments = sum(c.get("new_since_last") or 0 for c in comments.values())


def delta(d):
    return f" ({d:+d})" if d else ""


lines = []
tot = {"subscribers": 0, "views": 0}
new_tot = {"subscribers": 0, "views": 0}
for n, i in enumerate(stats["items"]):
    d = i.get("delta") or {}
    if n < 8:  # keep the message short; totals below still cover every mod
        lines.append(f"{i['title']}: {i['subscribers']}{delta(d.get('subscribers'))} abone, "
                     f"{i['views']}{delta(d.get('views'))} görüntülenme")
    for k in tot:
        if i.get(k) is None:  # Steam occasionally omits a counter; skip it instead of crashing
            continue
        tot[k] += i[k]
        new_tot[k] += d.get(k) or 0

dashboard = os.environ.get("DASHBOARD_URL", "").strip()
now = datetime.datetime.now(TR).strftime("%d.%m %H:%M")
text = "\n".join([f"Steam stats {now}", *lines,
                  f"Toplam: {tot['subscribers']} abone, {tot['views']} görüntülenme",
                  f"Yeni Görüntülenme: {new_tot['views']}",
                  f"Yeni abone: {new_tot['subscribers']}",
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
