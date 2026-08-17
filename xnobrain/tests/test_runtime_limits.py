from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from xnobrain.runtime_limits import max_parallel_agents, session_timeout_seconds


class RuntimeLimitTests(unittest.TestCase):
    def test_session_timeout_uses_environment_default_and_one_hour_cap(self) -> None:
        with patch.dict(os.environ, {"RUNTIME_SESSION_TIMEOUT_SECONDS": "60"}):
            self.assertEqual(session_timeout_seconds(), 60)
            self.assertEqual(session_timeout_seconds(120), 120)
            self.assertEqual(session_timeout_seconds(7200), 3600)

    def test_invalid_session_timeout_falls_back_to_safe_default(self) -> None:
        with patch.dict(os.environ, {"RUNTIME_SESSION_TIMEOUT_SECONDS": "invalid"}):
            self.assertEqual(session_timeout_seconds(), 3600)

    def test_parallel_agents_default_to_three_and_never_exceed_five(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(max_parallel_agents(), 3)
        with patch.dict(os.environ, {"RUNTIME_DELEGATION_MAX_CONCURRENT_CHILDREN": "4"}):
            self.assertEqual(max_parallel_agents(), 4)
        self.assertEqual(max_parallel_agents(12), 5)


if __name__ == "__main__":
    unittest.main()
