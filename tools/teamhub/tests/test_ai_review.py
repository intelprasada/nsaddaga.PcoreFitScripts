"""Unit tests for the AI (Copilot) code-review enrichment helpers.

We can't hit github.com from tests, so we exercise the pure helpers around
the network call and stub the fetcher for the caching path.

Run:  python3 -m unittest tools/teamhub/tests/test_ai_review.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEAMHUB = HERE.parent
sys.path.insert(0, str(TEAMHUB))

import dashboard_server as ds  # noqa: E402


class TestParsePrUrl(unittest.TestCase):
    def test_https_pull(self):
        self.assertEqual(
            ds._parse_pr_url("https://github.com/intel-restricted/foo/pull/123"),
            ("intel-restricted", "foo", 123),
        )

    def test_http_pull(self):
        self.assertEqual(
            ds._parse_pr_url("http://github.com/intel-restricted/foo/pull/9"),
            ("intel-restricted", "foo", 9),
        )

    def test_non_pull_url(self):
        # Bug URL, issue URL, or anything else must return None so the caller
        # skips the fetch.
        self.assertIsNone(ds._parse_pr_url("https://github.com/o/r/issues/1"))
        self.assertIsNone(ds._parse_pr_url("https://example.com/o/r/pull/1"))

    def test_none_and_empty(self):
        self.assertIsNone(ds._parse_pr_url(None))
        self.assertIsNone(ds._parse_pr_url(""))


class TestEnrichmentSkipsTIsWithoutUrl(unittest.TestCase):
    def test_no_url_no_enrichment(self):
        # Patch the cached fetcher so no network happens even if the loop is
        # wrong; then assert TIs without a URL are untouched.
        called_with: list[str] = []

        def fake(url, force=False):
            called_with.append(url)
            return {"resolved": 0, "unresolved": 0, "total": 0, "state": "OPEN"}

        original = ds._get_ai_review_counts_cached
        ds._get_ai_review_counts_cached = fake
        try:
            tis = [
                {"id": "1"},                                  # no url
                {"id": "2", "code_review_url": ""},            # blank url
                {"id": "3", "code_review_url": "https://github.com/o/r/pull/42"},
            ]
            ds._enrich_turnins_with_ai_review(tis)
        finally:
            ds._get_ai_review_counts_cached = original

        self.assertEqual(called_with, ["https://github.com/o/r/pull/42"])
        self.assertNotIn("ai_review", tis[0])
        self.assertNotIn("ai_review", tis[1])
        self.assertEqual(tis[2]["ai_review"]["state"], "OPEN")

    def test_fetch_failure_leaves_ti_without_key(self):
        # A None result (network error, unparseable, etc.) must NOT drop a
        # sentinel `ai_review` on the TI, so the UI cleanly shows "–" instead
        # of a bogus 0/0 pill.
        original = ds._get_ai_review_counts_cached
        ds._get_ai_review_counts_cached = lambda url, force=False: None
        try:
            tis = [{"id": "1", "code_review_url": "https://github.com/o/r/pull/1"}]
            ds._enrich_turnins_with_ai_review(tis)
            self.assertNotIn("ai_review", tis[0])
        finally:
            ds._get_ai_review_counts_cached = original


class TestBotFilterSet(unittest.TestCase):
    def test_copilot_bot_is_included(self):
        # This is the actual login the API returns for Copilot PR review
        # threads on our repos. Guard against someone tightening the set and
        # accidentally excluding the primary AI reviewer.
        self.assertIn("copilot-pull-request-reviewer", ds._AI_REVIEW_BOTS)


if __name__ == "__main__":
    unittest.main()
