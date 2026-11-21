"""Unit tests verifying strict multi-channel logical data isolation (ADR-005)."""
import unittest
import tempfile
import gc
from pathlib import Path
from src.database.connection import DatabaseManager
from src.database.migrations import MigrationManager
from src.database.repositories.channel_repository import ChannelRepository
from src.database.repositories.scoped_repository import ScopedProjectRepository
from src.database.models import ChannelCreate
from src.core.exceptions import ChannelNotFoundException, ValidationException


class TestChannelDataIsolation(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root_path = Path(self.temp_dir.name)
        self.db_path = self.root_path / "test_isolation.db"

        self.db_manager = DatabaseManager(db_path=self.db_path)
        self.migration_manager = MigrationManager(self.db_manager)
        self.migration_manager.run_migrations()

        self.channel_repo = ChannelRepository(db_manager=self.db_manager)

        # Create two distinct channels
        self.channel_repo.create(
            ChannelCreate(
                id="ch_alpha",
                name="Channel Alpha",
                handle="@channel_alpha",
                niche="cartoons",
            )
        )
        self.channel_repo.create(
            ChannelCreate(
                id="ch_beta",
                name="Channel Beta",
                handle="@channel_beta",
                niche="documentary",
            )
        )

    def tearDown(self):
        gc.collect()
        self.temp_dir.cleanup()

    def test_scoped_repository_rejects_non_existent_channel(self):
        """Verify ScopedRepository raises ChannelNotFoundException for invalid channel IDs."""
        with self.assertRaises(ChannelNotFoundException):
            ScopedProjectRepository(channel_id="ch_non_existent", db_manager=self.db_manager)

    def test_scoped_repository_rejects_empty_channel(self):
        """Verify ScopedRepository raises ValidationException for empty or whitespace channel IDs."""
        with self.assertRaises(ValidationException):
            ScopedProjectRepository(channel_id="   ", db_manager=self.db_manager)

    def test_channel_data_isolation_and_anti_leakage(self):
        """Verify that projects created under Channel Alpha are strictly isolated from Channel Beta."""
        alpha_proj_repo = ScopedProjectRepository(channel_id="ch_alpha", db_manager=self.db_manager)
        beta_proj_repo = ScopedProjectRepository(channel_id="ch_beta", db_manager=self.db_manager)

        # Create projects under Channel Alpha
        alpha_proj_1 = alpha_proj_repo.create("proj_alpha_01", "Alpha Video 1", aspect_ratio="9:16")
        alpha_proj_2 = alpha_proj_repo.create("proj_alpha_02", "Alpha Video 2", aspect_ratio="1:1")

        # Create project under Channel Beta
        beta_proj_1 = beta_proj_repo.create("proj_beta_01", "Beta Documentary", aspect_ratio="16:9")

        # 1. Verify Alpha repo only lists Alpha projects (count = 2)
        alpha_list = alpha_proj_repo.list_all()
        alpha_ids = [p["id"] for p in alpha_list]
        self.assertEqual(len(alpha_list), 2)
        self.assertIn("proj_alpha_01", alpha_ids)
        self.assertIn("proj_alpha_02", alpha_ids)
        self.assertNotIn("proj_beta_01", alpha_ids)  # Beta project must NEVER appear in Alpha list

        # 2. Verify Beta repo only lists Beta projects (count = 1)
        beta_list = beta_proj_repo.list_all()
        beta_ids = [p["id"] for p in beta_list]
        self.assertEqual(len(beta_list), 1)
        self.assertIn("proj_beta_01", beta_ids)
        self.assertNotIn("proj_alpha_01", beta_ids)  # Alpha projects must NEVER appear in Beta list

        # 3. Direct ID Leakage Prevention: Attempting to query Beta's project using Alpha's repo MUST return None
        leaked_attempt = alpha_proj_repo.get_by_id("proj_beta_01")
        self.assertIsNone(
            leaked_attempt,
            "CRITICAL SECURITY VIOLATION: Channel Alpha was able to query Channel Beta's project!",
        )

        # 4. Attempting to query Alpha's project using Beta's repo MUST return None
        reverse_leak_attempt = beta_proj_repo.get_by_id("proj_alpha_01")
        self.assertIsNone(
            reverse_leak_attempt,
            "CRITICAL SECURITY VIOLATION: Channel Beta was able to query Channel Alpha's project!",
        )


if __name__ == "__main__":
    unittest.main()

