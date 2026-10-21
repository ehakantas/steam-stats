"""Daily report helper for the Claude scheduled task.

usage: python3 scripts/report.py STATE_JSON OUTDIR [PATCH_ALERT]
  STATE_JSON  dashboard meta/state document (must contain last_run, last_generated_at; last_items optional)
  OUTDIR      writes run.json (new runs/rNNN doc) and state.json (meta/state update)
  PATCH_ALERT optional extra alert line (PC patch that may break mods)
Prints a short summary for the task's reply. All printed dates/times are Istanbul time (TR).
"""
import datetime, json, os, sys, time, zoneinfo

here = os.path.dirname(os.path.abspath(__file__))
stats = json.load(open(os.path.join(here, "..", "data", "stats.json")))
state = json.load(open(sys.argv[1]))
out = sys.argv[2]
patch = sys.argv[3] if len(sys.argv) > 3 else ""

prev = state.get("last_items") or []
L = state["last_run"]
n = L + 1
g = stats["generated_at"]
age = (time.time() - g) / 3600

cur = [{"id": i["id"], "title": i["title"], "subs": i["subscribers"], "favs": i["favorites"],
        "views": i["views"], "time_updated": i["time_updated"]} for i in stats["items"]]
P = {p["id"]: p for p in prev}
C = {c["id"]: c for c in cur}

# Optional comment stats (data/comments.json, written by fetch_comments.py).
# Missing or unreadable file -> everything below behaves exactly as before.
try:
    comments = json.load(open(os.path.join(here, "..", "data", "comments.json"))).get("items", {})
except (OSError, ValueError):
    comments = {}


def comment_ctx(item_id):
    c = comments.get(item_id)
    if not c:
        return ""
    kw = ", ".join(f"{k}×{v}" for k, v in sorted((c.get("new_keywords") or {}).items(), key=lambda x: -x[1]))
    return f" Comments: {c.get('new_since_last', 0)} new since last fetch" + (f" ({kw})" if kw else "") + "."


alerts = [f"🟠 REMOVED/HIDDEN: {p['title']} ({p['id']}) missing vs run {L}; likely hidden/removed on Workshop; check the item page."
          for p in prev if p["id"] not in C]
deltas = []
for c in cur:
    d = c["subs"] - P[c["id"]]["subs"] if c["id"] in P else None
    deltas.append((c, d))
    if d is not None and d < 0:
        alerts.append(f"🟡 SUBS DROP: {c['title']} {P[c['id']]['subs']}→{c['subs']} ({d}); likely unsubscribes after an update or game patch; check comments." + comment_ctx(c["id"]))
if patch:
    alerts.append(patch)

tot = sum(c["subs"] for c in cur)
ptot = sum(p["subs"] for p in prev)
top = max(((c, d) for c, d in deltas if d is not None), key=lambda x: x[1], default=None)
new_news = [x for x in stats.get("news", []) if x["date"] > state["last_generated_at"]]
TR = zoneinfo.ZoneInfo("Europe/Istanbul")
tr = lambda t: datetime.datetime.fromtimestamp(t, TR)
today = datetime.datetime.now(TR).date().isoformat()

os.makedirs(out, exist_ok=True)
json.dump({"run": n, "date": today, "generated_at": g, "items": cur},
          open(os.path.join(out, "run.json"), "w"), ensure_ascii=False)
json.dump({"last_run": n, "last_generated_at": g, "last_checked": today, "alerts": alerts, "last_items": cur},
          open(os.path.join(out, "state.json"), "w"), ensure_ascii=False)

print(f"RUN_DOC r{n:03d}")
print(f"SNAPSHOT {tr(g):%Y-%m-%d %H:%M} TR, {age:.1f} h old"
      + (" | STALE" if age > 36 else "") + (" | UNCHANGED" if g == state["last_generated_at"] else ""))
print(f"TOTAL_SUBS {tot} ({tot - ptot:+d} vs run {L})" if prev else f"TOTAL_SUBS {tot} (no previous run)")
print(f"TOP_MOVER {top[0]['title']} {top[1]:+d}" if top and top[1] else "TOP_MOVER none")
print("ALERTS " + (" || ".join(alerts) if alerts else "none"))
if comments:
    print("NEW_COMMENTS " + (" || ".join(f"{v['title']} +{v['new_since_last']} (total {v.get('total')})"
                                         for v in comments.values() if v.get("new_since_last")) or "none"))
print("NEW_NEWS " + (" || ".join(f"{tr(x['date']):%Y-%m-%d} {x['title']}" for x in new_news) or "none"))
