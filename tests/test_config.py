"""Unit tests for configuration management."""
import unittest
import os
from pathlib import Path
from src.core.config import Settings, get_settings


class TestConfig(unittest.TestCase):
    def test_default_settings(self):
        settings = Settings()
        self.assertIsNotNone(settings.project_root)
        self.assertTrue(settings.project_root.exists())
        self.assertEqual(settings.default_true_peak_dbtp, -1.0)
        self.assertEqual(settings.default_target_lufs, -14.0)
        self.assertEqual(settings.default_ducking_db, -14.0)
        self.assertEqual(settings.db_busy_timeout_ms, 10000)

    def test_directory_creation(self):
        settings = Settings()
        settings.ensure_directories()
        self.assertTrue(settings.workspace_root.exists())
        self.assertTrue(settings.channels_root.exists())
        self.assertTrue(settings.global_templates_root.exists())
        self.assertTrue(settings.runtime_dir.exists())
        self.assertTrue(settings.temp_dir.exists())
        self.assertTrue(settings.logs_dir.exists())

    def test_singleton_get_settings(self):
        s1 = get_settings()
        s2 = get_settings()
        self.assertIs(s1, s2)


if __name__ == "__main__":
    unittest.main()

