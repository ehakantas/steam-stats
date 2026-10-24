"""Shared settings for the steam-stats scripts (one place to change the game, time zone or keywords)."""
from zoneinfo import ZoneInfo

APPID = 292030  # The Witcher 3: Wild Hunt
TR = ZoneInfo("Europe/Istanbul")  # every printed date/time is Istanbul time
STALE_H = 36  # a snapshot older than this (hours) is reported as stale

# Comment keywords, matched at the START of a word in lower-cased text:
# "bug" matches "bug", "bugs", "buggy" but not "debug"; "crash" matches "crashes", "crashing".
# COMPLAINT_KEYWORDS raise alerts in the daily report; INFO_KEYWORDS are only counted for info
# (they also appear in positive comments such as "thanks for the update").
COMPLAINT_KEYWORDS = [
    "crash", "broken", "bug", "not working", "doesn't work", "doesnt work",
    "error", "freeze", "stuck",
    "isn't working", "isnt working", "does not work", "do not work", "don't work",
    "dont work", "won't work", "wont work", "stopped working", "no longer work",
    "not loading", "won't load",
]
INFO_KEYWORDS = ["patch", "update", "fix"]
