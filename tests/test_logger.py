"""Unit tests for structured logging and context tracking."""
import unittest
import json
import logging
from src.core.logger import (
    get_logger,
    set_log_context,
    clear_log_context,
    JSONLogFormatter,
    ConsoleLogFormatter,
)


class TestLogger(unittest.TestCase):
    def setUp(self):
        clear_log_context()

    def tearDown(self):
        clear_log_context()

    def test_context_management(self):
        set_log_context(channel_id="ch_test_01", project_id="proj_123", task_id="task_999")
        
        # Test JSON Formatter output
        formatter = JSONLogFormatter()
        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=10,
            msg="Test operational event",
            args=(),
            exc_info=None,
        )
        formatted = formatter.format(record)
        parsed = json.loads(formatted)

        self.assertEqual(parsed["message"], "Test operational event")
        self.assertEqual(parsed["level"], "INFO")
        self.assertEqual(parsed["channel_id"], "ch_test_01")
        self.assertEqual(parsed["project_id"], "proj_123")
        self.assertEqual(parsed["task_id"], "task_999")

    def test_console_formatter(self):
        set_log_context(channel_id="ch_dreammatter")
        formatter = ConsoleLogFormatter()
        record = logging.LogRecord(
            name="studio.core",
            level=logging.WARNING,
            pathname="test.py",
            lineno=25,
            msg="Warning message",
            args=(),
            exc_info=None,
        )
        formatted = formatter.format(record)
        self.assertIn("[WARNING]", formatted)
        self.assertIn("ch=ch_dreammatter", formatted)
        self.assertIn("Warning message", formatted)


if __name__ == "__main__":
    unittest.main()

