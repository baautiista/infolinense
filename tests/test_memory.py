"""Tests for background memory maintenance; no Railway or database required."""
import unittest
from unittest.mock import patch

from app import memory


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.before = memory._last
        memory._last = float("-inf")

    def tearDown(self):
        memory._last = self.before

    def test_disabled_does_not_collect(self):
        with patch.object(memory.gc, "collect") as collect:
            self.assertFalse(memory.maybe_trim(0, now=1000))
            collect.assert_not_called()

    def test_maintenance_is_throttled_between_threads(self):
        with patch.object(memory.gc, "collect") as collect, patch.object(memory.os, "name", "nt"):
            self.assertTrue(memory.maybe_trim(30, now=1000))
            self.assertFalse(memory.maybe_trim(30, now=1001))
            self.assertTrue(memory.maybe_trim(30, now=2801))
            self.assertEqual(collect.call_count, 2)

    def test_invalid_interval_falls_back(self):
        with patch.dict(memory.os.environ, {"MEMORY_TRIM_INTERVAL_MINUTES": "bad"}):
            with patch.object(memory.gc, "collect") as collect, patch.object(memory.os, "name", "nt"):
                self.assertTrue(memory.maybe_trim(now=1000))
                collect.assert_called_once()

    def test_rss_diagnostic_is_safe_on_non_linux(self):
        with patch("builtins.open", side_effect=FileNotFoundError):
            self.assertIsNone(memory.resident_memory_mb())


if __name__ == "__main__":
    unittest.main()
