"""Small helpers shared by the scripts (standard library only)."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "steam-stats/1.0"


def http_get(url, params=None, retries=2, timeout=30):
    """GET a URL and return the body as text, retrying transient network errors.

    Client errors (4xx except 429) are not retried: a bad key or id will not fix itself.
    The query string is left out of the error message because it can hold an API key.
    """
    full = f"{url}?{urllib.parse.urlencode(params)}" if params else url
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(full, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500 and e.code != 429:
                raise RuntimeError(f"request failed: {url} (HTTP {e.code})") from None
            last = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
        if attempt < retries:
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"request failed: {url} ({last})")


def get_json(url, params=None, **kw):
    return json.loads(http_get(url, params, **kw))
