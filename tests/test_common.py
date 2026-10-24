"""Tests for common.http_get retries (no network: urlopen is faked)."""
import io
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import common  # noqa: E402


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def http_error(code):
    return urllib.error.HTTPError("https://x", code, "err", {}, None)


class HttpGetTests(unittest.TestCase):
    def run_get(self, effects, **kw):
        with mock.patch.object(common.urllib.request, "urlopen", side_effect=effects) as m, \
                mock.patch.object(common.time, "sleep") as sleep:
            try:
                return common.http_get("https://api.example/x", {"key": "SECRET"}, **kw), m, sleep
            except RuntimeError as e:
                return e, m, sleep

    def test_retries_transient_errors_then_succeeds(self):
        out, m, sleep = self.run_get([urllib.error.URLError("reset"), http_error(502), Resp(b"ok")])
        self.assertEqual(out, "ok")
        self.assertEqual(m.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_client_error_is_not_retried(self):
        out, m, _ = self.run_get([http_error(403)])
        self.assertIsInstance(out, RuntimeError)
        self.assertEqual(m.call_count, 1)

    def test_rate_limit_is_retried(self):
        out, m, _ = self.run_get([http_error(429), Resp(b"ok")])
        self.assertEqual(out, "ok")

    def test_gives_up_and_hides_query_string(self):
        out, m, sleep = self.run_get([TimeoutError()] * 3)
        self.assertIsInstance(out, RuntimeError)
        self.assertEqual(m.call_count, 3)
        self.assertEqual(sleep.call_count, 2)  # no pointless wait after the last attempt
        self.assertNotIn("SECRET", str(out))


if __name__ == "__main__":
    unittest.main()
