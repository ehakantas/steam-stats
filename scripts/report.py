"""Daily report helper for the Claude scheduled task.

usage: python3 scripts/report.py STATE_JSON OUTDIR [PATCH_ALERT]
  STATE_JSON  dashboard meta/state document (must contain last_run, last_generated_at;
              last_items, last_comments, comments_bad_streak, issues optional)
  OUTDIR      writes run.json (new runs/rNNN doc) and state.json (meta/state update)
  PATCH_ALERT optional extra alert line (PC patch that may break mods)
Prints a short summary for the task's reply. All printed dates/times are Istanbul time (TR).

Comments: data/comments.json (fetch_comments.py) is refreshed on every workflow run, so its
"new_since_last" only covers the last workflow run. This script instead diffs the cumulative
counters (seen_count, keywords_total) against the values stored in the dashboard state at the
previous report, so every comment since the last report is counted exactly once. seen_count only
grows, so a deleted comment cannot hide a new one; "total" (Steam's current count) is used as a
fallback until both sides have a seen_count.

Note: the subscriber changes here are measured against the previous REPORT (dashboard state),
not the previous workflow run like the "delta" fields in stats.json, so they are computed here.

The work is split into build_report() (pure: dicts in, dicts and lines out; used by the tests)
and main() (file input/output).
"""
import datetime, json, os, sys, time

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
from config import COMPLAINT_KEYWORDS, STALE_H, TR  # noqa: E402


def tr(t):
    return datetime.datetime.fromtimestamp(t, TR)


def kwtxt(kw):
    return ", ".join(f"{k}×{v}" for k, v in sorted(kw.items(), key=lambda x: (-x[1], x[0])))


def merge_kw(a, b):
    o = dict(a or {})
    for k, v in (b or {}).items():
        o[k] = o.get(k, 0) + v
    return o


# ---------------------------------------------------------------- comments
def diff_comments(cur, craw, state, now):
    """Compares comments.json with the counters stored at the previous report.

    Adds "comments"/"comments_new" to the items in cur and returns a dict with the comment
    status, the per-mod news since the last report (cnew) and the counters to store next.
    """
    prev_lc = state.get("last_comments") if isinstance(state.get("last_comments"), dict) else None
    citems = craw["items"] if craw else {}
    cerrors = {e.get("id") for e in (craw or {}).get("errors") or [] if isinstance(e, dict)}

    if craw is None:
        cstatus = "missing"
    elif (now - (craw.get("generated_at") or 0)) / 3600 > STALE_H:
        cstatus = f"stale ({(now - (craw.get('generated_at') or 0)) / 3600:.0f} h old)"
    elif cerrors:
        cstatus = f"errors ({len(cerrors)} of {len(cur)} mods unreadable)"
    elif prev_lc is None:
        cstatus = "baseline (first comment check; new-comment counts start next report)"
    else:
        cstatus = "ok"
    cbad = cstatus.split()[0] in ("missing", "stale", "errors")
    streak = (state.get("comments_bad_streak") or 0) + 1 if cbad else 0

    cnew = {}  # id -> {"new": int, "kw": {kw: n}} since the previous report
    for c in cur:
        v = citems.get(c["id"])
        if not isinstance(v, dict):
            continue
        c["comments"] = v.get("total")
        p = (prev_lc or {}).get(c["id"])
        if p is None or c["id"] in cerrors:
            continue  # no baseline yet (or unreadable now): nothing counted as new
        if isinstance(p.get("seen"), int) and isinstance(v.get("seen_count"), int):
            t0, t1 = p["seen"], v["seen_count"]
        else:
            t0, t1 = p.get("total"), v.get("total")
        new = max(0, t1 - t0) if isinstance(t0, int) and isinstance(t1, int) else 0
        kw0, kw1 = p.get("kw") or {}, v.get("keywords_total") or {}
        kw = {k: kw1[k] - kw0.get(k, 0) for k in kw1 if kw1[k] - kw0.get(k, 0) > 0}
        c["comments_new"] = new
        cnew[c["id"]] = {"new": new, "kw": kw}

    # Stored for the next report: cumulative counters; unreadable mods keep their old baseline.
    last_comments = dict(prev_lc or {})
    for cid, v in citems.items():
        if isinstance(v, dict) and cid not in cerrors:
            last_comments[cid] = {"total": v.get("total"), "seen": v.get("seen_count"),
                                  "kw": v.get("keywords_total") or {}}
    return {"status": cstatus, "bad": cbad, "streak": streak, "cnew": cnew, "last_comments": last_comments}


def comment_ctx(cnew, item_id):
    x = cnew.get(item_id)
    if not x:
        return ""
    return f" Comments since last report: +{x['new']}" + (f" ({kwtxt(x['kw'])})" if x["kw"] else "") + "."


# ------------------------------------------------------------------ alerts
def item_alerts(cur, prev, last_run, cnew):
    """Removed mods and subscriber drops vs the previous report; also returns the per-mod deltas."""
    P = {p["id"]: p for p in prev}
    C = {c["id"]: c for c in cur}
    alerts = [f"🟠 REMOVED/HIDDEN: {p['title']} ({p['id']}) missing vs run {last_run}; likely hidden/removed on Workshop; check the item page."
              for p in prev if p["id"] not in C]
    deltas = []
    for c in cur:
        d = c["subs"] - P[c["id"]]["subs"] if c["id"] in P else None
        deltas.append((c, d))
        if d is not None and d < 0:
            alerts.append(f"🟡 SUBS DROP: {c['title']} {P[c['id']]['subs']}→{c['subs']} ({d}); likely unsubscribes after an update or game patch; check comments." + comment_ctx(cnew, c["id"]))
    return alerts, deltas


# ----------------------------------------------------------------- issues
def update_issues(issues_in, cur, cnew, n, nowi):
    """One issue per mod, opened by a complaint comment and kept OPEN until the owner closes it on
    the dashboard (the page writes status "closed" into meta/state.issues). This never closes an
    issue; it only records new complaints and whether the mod was updated since.

    Returns (issues, alerts).
    """
    issues = {k: dict(v) for k, v in (issues_in or {}).items() if isinstance(v, dict)}
    open_by_item = {v.get("item_id"): k for k, v in issues.items() if v.get("status") == "open"}
    alerts = []
    for c in cur:
        iid = open_by_item.get(c["id"])
        iss = issues.get(iid) if iid else None
        if iss is not None:
            iss["title"] = c["title"]
            if (c.get("time_updated") or 0) > iss.get("opened_at", 0) and c["time_updated"] != iss.get("updated_at"):
                iss["updated_at"] = c["time_updated"]  # latest mod update after the issue was opened
                iss["after_update_keywords"] = {}       # a new fix attempt starts a fresh watch
        x = cnew.get(c["id"])
        bad = {k: v for k, v in (x or {}).get("kw", {}).items() if k in COMPLAINT_KEYWORDS}
        if not bad:
            continue
        if iss is None:
            iid = f"{c['id']}-r{n:03d}"
            iss = issues[iid] = {"item_id": c["id"], "title": c["title"], "status": "open", "opened_at": nowi,
                                 "opened_run": n, "keywords": {}, "complaint_reports": 0,
                                 "updated_at": None, "after_update_keywords": {}}
            open_by_item[c["id"]] = iid
            alerts.append(f"🟡 COMMENT REPORT: {c['title']} — {x['new']} new comment(s) since last report mention {kwtxt(bad)}; "
                          f"opened an issue on the dashboard; read the comments on the Workshop page and close the issue there once fixed.")
        elif iss.get("updated_at"):
            iss["after_update_keywords"] = merge_kw(iss.get("after_update_keywords"), bad)
            alerts.append(f"🟠 STILL REPORTED AFTER UPDATE: {c['title']} — {x['new']} new comment(s) mention {kwtxt(bad)} "
                          f"after the {tr(iss['updated_at']):%Y-%m-%d %H:%M} TR update (issue open since {tr(iss['opened_at']):%Y-%m-%d}); "
                          f"the fix may not have worked.")
        else:
            alerts.append(f"🟡 COMMENT REPORT: {c['title']} — {x['new']} more comment(s) mention {kwtxt(bad)} "
                          f"(issue open since {tr(iss['opened_at']):%Y-%m-%d}, mod not updated yet).")
        iss["keywords"] = merge_kw(iss.get("keywords"), bad)
        iss["complaint_reports"] = iss.get("complaint_reports", 0) + 1
        iss["last_complaint_at"] = nowi
    return issues, alerts


def issue_txt(iss):
    s = f"{iss['title']}: {kwtxt(iss.get('keywords') or {})}, open since {tr(iss['opened_at']):%Y-%m-%d}"
    if iss.get("after_update_keywords"):
        return s + f"; STILL reported after the {tr(iss['updated_at']):%Y-%m-%d} update"
    if iss.get("updated_at"):
        quiet = (iss.get("last_complaint_at") or 0) <= iss["updated_at"]
        return s + f"; mod updated {tr(iss['updated_at']):%Y-%m-%d}" + (", no complaints since — close it on the dashboard if fixed" if quiet else "")
    return s + "; mod not updated yet"


# ------------------------------------------------------------------ report
def build_report(stats, state, craw, patch="", now=None):
    """Returns (run_doc, state_out, summary_lines). craw is comments.json, or None if unreadable."""
    now = time.time() if now is None else now
    today = tr(now).date().isoformat()
    prev = state.get("last_items") or []
    L = state["last_run"]
    n = L + 1
    g = stats["generated_at"]
    age = (now - g) / 3600

    cur = [{"id": i["id"], "title": i["title"], "subs": i["subscribers"], "favs": i["favorites"],
            "views": i["views"], "time_updated": i["time_updated"]} for i in stats["items"]]

    cm = diff_comments(cur, craw, state, now)
    cnew, cstatus, cbad, streak = cm["cnew"], cm["status"], cm["bad"], cm["streak"]
    alerts, deltas = item_alerts(cur, prev, L, cnew)
    issues, issue_alerts = update_issues(state.get("issues"), cur, cnew, n, int(now))
    alerts += issue_alerts

    open_issues = [v for v in issues.values() if v.get("status") == "open"]
    if streak >= 2:
        alerts.append(f"🟠 COMMENTS UNCHECKED: comments not fully checked for {streak} reports in a row (now: {cstatus}); "
                      f"check the workflow's 'Comment stats' step and data/comments.json.")
    if patch:
        alerts.append(patch)

    tot = sum(c["subs"] for c in cur)
    ptot = sum(p["subs"] for p in prev)
    top = max(((c, d) for c, d in deltas if d is not None), key=lambda x: x[1], default=None)
    new_news = [x for x in stats.get("news", []) if x["date"] > state["last_generated_at"]]

    run_doc = {"run": n, "date": today, "generated_at": g, "items": [dict(c) for c in cur]}
    last_items = [{k: c[k] for k in ("id", "title", "subs", "favs", "views", "time_updated")} for c in cur]
    state_out = {"last_run": n, "last_generated_at": g, "last_checked": today, "alerts": alerts,
                 "last_items": last_items, "comments_bad_streak": streak, "issues": issues}
    if craw is not None:
        state_out["last_comments"] = cm["last_comments"]

    if craw is None or cstatus.startswith("stale") or cbad and not cnew:
        ctext = "not checked"
    elif cstatus.startswith("baseline"):
        ctext = "baseline set"
    else:
        rows = [f"{c['title']} +{cnew[c['id']]['new']}" + (f" ({kwtxt(cnew[c['id']]['kw'])})" if cnew[c["id"]]["kw"] else "")
                for c in cur if c["id"] in cnew and (cnew[c["id"]]["new"] or cnew[c["id"]]["kw"])]
        ctext = " || ".join(rows) or "no new comments since last report"

    lines = [
        f"RUN_DOC r{n:03d}",
        f"SNAPSHOT {tr(g):%Y-%m-%d %H:%M} TR, {age:.1f} h old"
        + (" | STALE" if age > STALE_H else "") + (" | UNCHANGED" if g == state["last_generated_at"] else ""),
        f"TOTAL_SUBS {tot} ({tot - ptot:+d} vs run {L})" if prev else f"TOTAL_SUBS {tot} (no previous run)",
        f"TOP_MOVER {top[0]['title']} {top[1]:+d}" if top and top[1] else "TOP_MOVER none",
        "ALERTS " + (" || ".join(alerts) if alerts else "none"),
        "COMMENTS_STATUS " + cstatus + (f" | streak {streak}" if cbad else ""),
        "COMMENTS " + ctext,
        "OPEN_ISSUES " + (" || ".join(issue_txt(v) for v in sorted(open_issues, key=lambda v: v["opened_at"])) or "none"),
        "NEW_NEWS " + (" || ".join(f"{tr(x['date']):%Y-%m-%d} {x['title']}" for x in new_news) or "none"),
    ]
    return run_doc, state_out, lines


def load_comments(path):
    try:
        craw = json.load(open(path))
        if not isinstance(craw, dict) or not isinstance(craw.get("items"), dict):
            raise ValueError("not a comments object")
        return craw
    except (OSError, ValueError):
        return None


def main():
    stats = json.load(open(os.path.join(here, "..", "data", "stats.json")))
    state = json.load(open(sys.argv[1]))
    out = sys.argv[2]
    patch = sys.argv[3] if len(sys.argv) > 3 else ""
    craw = load_comments(os.path.join(here, "..", "data", "comments.json"))

    run_doc, state_out, lines = build_report(stats, state, craw, patch)
    os.makedirs(out, exist_ok=True)
    json.dump(run_doc, open(os.path.join(out, "run.json"), "w"), ensure_ascii=False)
    json.dump(state_out, open(os.path.join(out, "state.json"), "w"), ensure_ascii=False)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
