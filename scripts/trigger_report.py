"""Fire the Claude routine that writes the daily Witcher 3 Workshop report (best effort).

Runs after the stats commit is pushed, so the routine sees this run's data. Needs
CLAUDE_ROUTINE_TOKEN; exits quietly when it is missing. Never prints the token or the response
body. A failure is also written to the workflow run summary, so a skipped report is visible on
the run page even though the step cannot fail the run.
"""
import os, sys, urllib.error, urllib.request

ROUTINE_ID = os.environ.get("REPORT_ROUTINE_ID", "").strip() or "trig_01PFu9axiWE5vbKp2GxcxzcU"
URL = f"https://api.anthropic.com/v1/claude_code/routines/{ROUTINE_ID}/fire"


def summary(line):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as f:
            f.write(line + "\n")


def main():
    token = os.environ.get("CLAUDE_ROUTINE_TOKEN", "").strip()
    if not token:
        print("CLAUDE_ROUTINE_TOKEN not set; skipping")
        return 0
    req = urllib.request.Request(URL, data=b"{}", method="POST", headers={
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "experimental-cc-routine-2026-04-01",
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
        "User-Agent": "steam-stats-workflow/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
    except Exception as e:
        status = type(e).__name__
    print("routine fire HTTP status:", status)
    if status == 200:
        return 0
    summary(f"⚠️ Daily Workshop report was NOT triggered (routine fire returned {status}). "
            "Check the CLAUDE_ROUTINE_TOKEN secret, or run the report routine by hand.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
