"""Unit tests for exception hierarchy and error formatting."""
import unittest
from src.core.exceptions import (
    StudioException,
    ConfigurationException,
    ChannelNotFoundException,
    LicenseVerificationException,
    PathTraversalSecurityException,
)


class TestExceptions(unittest.TestCase):
    def test_base_studio_exception(self):
        exc = StudioException("General error", error_code="TEST_ERROR", details={"k": "v"})
        data = exc.to_dict()
        self.assertEqual(data["error_code"], "TEST_ERROR")
        self.assertEqual(data["message"], "General error")
        self.assertEqual(data["details"]["k"], "v")

    def test_channel_not_found_exception(self):
        exc = ChannelNotFoundException("ch_non_existent")
        data = exc.to_dict()
        self.assertEqual(data["error_code"], "CHANNEL_NOT_FOUND")
        self.assertIn("ch_non_existent", data["message"])
        self.assertEqual(data["details"]["channel_id"], "ch_non_existent")

    def test_license_verification_exception(self):
        exc = LicenseVerificationException(
            asset_id="asset_123",
            reason="Unresolved rights",
            details={"source": "unknown"},
        )
        data = exc.to_dict()
        self.assertEqual(data["error_code"], "LICENSE_VERIFICATION_FAILED")
        self.assertEqual(data["details"]["asset_id"], "asset_123")
        self.assertEqual(data["details"]["source"], "unknown")

    def test_path_traversal_exception(self):
        exc = PathTraversalSecurityException(
            attempted_path="../../secret.txt",
            root_path="C:/app/workspace",
        )
        data = exc.to_dict()
        self.assertEqual(data["error_code"], "PATH_TRAVERSAL_VIOLATION")


if __name__ == "__main__":
    unittest.main()

