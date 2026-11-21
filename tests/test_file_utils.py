"""Unit tests for file utility functions and path security."""
import unittest
import tempfile
from pathlib import Path
from src.utils.file_utils import (
    safe_resolve_path,
    calculate_file_sha256,
    calculate_data_hash,
)
from src.core.exceptions import PathTraversalSecurityException


class TestFileUtils(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_safe_resolve_path_success(self):
        sub = "channels/ch_01/assets"
        resolved = safe_resolve_path(self.root_path, sub)
        self.assertTrue(str(resolved).startswith(str(self.root_path.resolve())))

    def test_safe_resolve_path_traversal_blocked(self):
        malicious = "../../../windows/system32/cmd.exe"
        with self.assertRaises(PathTraversalSecurityException):
            safe_resolve_path(self.root_path, malicious)

    def test_calculate_file_sha256(self):
        test_file = self.root_path / "sample.txt"
        test_file.write_text("Hello Universal Studio", encoding="utf-8")
        
        sha = calculate_file_sha256(test_file)
        self.assertIsInstance(sha, str)
        self.assertEqual(len(sha), 64)

    def test_calculate_data_hash_idempotency(self):
        payload1 = {"prompt": "Toddler Tommy eating giant pizza", "aspect_ratio": "9:16"}
        payload2 = {"aspect_ratio": "9:16", "prompt": "Toddler Tommy eating giant pizza"}
        
        # Must produce identical hashes regardless of dictionary key order
        hash1 = calculate_data_hash(payload1)
        hash2 = calculate_data_hash(payload2)
        self.assertEqual(hash1, hash2)


if __name__ == "__main__":
    unittest.main()

