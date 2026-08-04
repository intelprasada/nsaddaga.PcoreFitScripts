"""Unit tests for the pipe-aware diff routing in dashboard_server.

Motivation:
  Before this fix, REPOS["JNC"] pointed to the fit-pipe baseline only, so any
  JNC core-pipe turnin (e.g. TI 14941) failed to resolve its diff even when
  the TI was released. We now build a per-(project, pipe) baseline map and
  route the diff endpoint by the TI's `cluster` field (with fallback to the
  other pipe). These tests pin that behavior at the config level so a
  future edit that drops core-pipe support fails a test.

Run:  python3 -m unittest tools/teamhub/tests/test_diff_routing.py
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEAMHUB = HERE.parent
sys.path.insert(0, str(TEAMHUB))

import dashboard_server as ds  # noqa: E402


class TestReposByPipe(unittest.TestCase):
    def test_all_four_pipes_configured(self):
        # Every (project, pipe) combination that used to be reachable via the
        # tcsh hdk.rc source aliases must have a repo path configured.
        for project in ("GFC", "JNC"):
            for pipe in ("core", "fit"):
                path = ds.REPOS_BY_PIPE.get((project, pipe))
                self.assertTrue(
                    path,
                    f"REPOS_BY_PIPE missing ({project}, {pipe}); "
                    "core-pipe TIs will lose their diff routing.",
                )

    def test_jnc_core_and_fit_are_distinct(self):
        # The specific regression: if core and fit resolve to the same repo,
        # core-pipe TIs will still fail to resolve because they land in a
        # different git bundle.
        self.assertNotEqual(
            ds.REPOS_BY_PIPE[("JNC", "core")],
            ds.REPOS_BY_PIPE[("JNC", "fit")],
            "JNC core-pipe repo must differ from fit-pipe repo",
        )

    def test_gfc_core_and_fit_are_distinct(self):
        self.assertNotEqual(
            ds.REPOS_BY_PIPE[("GFC", "core")],
            ds.REPOS_BY_PIPE[("GFC", "fit")],
        )

    def test_jnc_core_points_at_core_bundle(self):
        # Guard against someone accidentally swapping the fit path in as the
        # core value.
        core_path = ds.REPOS_BY_PIPE[("JNC", "core")]
        self.assertIn(
            "/jnc/core/",
            core_path,
            f"JNC core-pipe repo path should live under jnc/core/, got {core_path}",
        )
        self.assertIn("core-jnc", os.path.basename(core_path))

    def test_gfc_core_points_at_core_bundle(self):
        core_path = ds.REPOS_BY_PIPE[("GFC", "core")]
        self.assertIn("/gfc/core/", core_path)
        self.assertIn("core-gfc", os.path.basename(core_path))

    def test_env_var_overrides_are_honored(self):
        # Reload the module with an override in place to confirm the env-var
        # escape hatch still works — needed for the on-call playbook.
        os.environ["JNC_CORE_REPO"] = "/tmp/fake-jnc-core-for-test"
        try:
            import importlib
            reloaded = importlib.reload(ds)
            self.assertEqual(
                reloaded.REPOS_BY_PIPE[("JNC", "core")],
                "/tmp/fake-jnc-core-for-test",
            )
        finally:
            del os.environ["JNC_CORE_REPO"]
            import importlib
            importlib.reload(ds)


if __name__ == "__main__":
    unittest.main()
