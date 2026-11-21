"""Unit and integration tests for ChannelService (Task 3.3)."""
import unittest
import tempfile
import gc
import logging
from pathlib import Path
from unittest.mock import patch

from src.database.connection import DatabaseManager
from src.database.migrations import MigrationManager
from src.database.repositories.channel_repository import ChannelRepository
from src.database.models import ChannelCreate, ChannelUpdate
from src.services.channel_service import ChannelService
from src.core.config import get_settings
from src.core.constants import AspectRatio, GenerationMode
from src.core.exceptions import (
    StudioException,
    ChannelNotFoundException,
    ValidationException,
)


class TestChannelService(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root_path = Path(self.temp_dir.name)
        self.db_path = self.root_path / "test_service.db"
        self.channels_dir = self.root_path / "channels"
        self.channels_dir.mkdir(parents=True, exist_ok=True)

        # Redirect settings.channels_root to temp directory for clean test isolation
        self.settings = get_settings()
        self._orig_channels_root = self.settings.channels_root
        self.settings.channels_root = self.channels_dir

        self.db_manager = DatabaseManager(db_path=self.db_path)
        self.migration_manager = MigrationManager(self.db_manager)
        self.migration_manager.run_migrations()

        self.repo = ChannelRepository(db_manager=self.db_manager)
        self.service = ChannelService(repository=self.repo)

    def tearDown(self):
        self.settings.channels_root = self._orig_channels_root
        gc.collect()
        self.temp_dir.cleanup()

    def test_create_channel_success(self):
        """Verify successful creation of a channel via ChannelService using ChannelCreate."""
        data = ChannelCreate(
            id="ch_dreammatter_001",
            name="DreamMatter Studio",
            handle="@DreamMatterWorld",
            niche="3d_animation",
            default_language="en",
            supported_locales=["en", "ar"],
            default_aspect_ratio=AspectRatio.VERTICAL_9_16,
            default_generation_mode=GenerationMode.IMAGE_TO_VIDEO,
        )

        channel = self.service.create_channel(data)
        self.assertEqual(channel.id, "ch_dreammatter_001")
        self.assertEqual(channel.name, "DreamMatter Studio")
        self.assertEqual(channel.handle, "@DreamMatterWorld")
        self.assertTrue(channel.is_active)

        # Verify physical directories were created under settings.channels_root
        channel_dir = self.channels_dir / "ch_dreammatter_001"
        self.assertTrue(channel_dir.exists())
        for sub in ["branding", "templates", "characters", "media_vault", "projects"]:
            self.assertTrue((channel_dir / sub).exists(), f"Subdirectory '{sub}' missing")

    def test_create_channel_from_dict(self):
        """Verify channel creation accepts a dictionary payload and validates correctly."""
        payload = {
            "id": "ch_dict_test",
            "name": "Dict Channel",
            "handle": "dict_handle",  # Auto-prefixed with @
            "niche": "education",
            "default_language": "en",
        }
        channel = self.service.create_channel(payload)
        self.assertEqual(channel.id, "ch_dict_test")
        self.assertEqual(channel.handle, "@dict_handle")
        self.assertTrue(channel.is_active)

    def test_get_channel_success_and_not_found(self):
        """Verify retrieval by ID succeeds for existing and raises ChannelNotFoundException for missing."""
        self.service.create_channel(
            ChannelCreate(id="ch_find_me", name="Find Me", handle="@find_me", niche="testing")
        )

        found = self.service.get_channel("ch_find_me")
        self.assertEqual(found.id, "ch_find_me")
        self.assertEqual(found.name, "Find Me")

        with self.assertRaises(ChannelNotFoundException):
            self.service.get_channel("ch_non_existent")

    def test_get_channel_by_handle_success_and_not_found(self):
        """Verify retrieval by handle works with or without @ prefix."""
        self.service.create_channel(
            ChannelCreate(id="ch_handle_test", name="Handle Test", handle="@unique_handle", niche="testing")
        )

        # Retrieval with @
        found1 = self.service.get_channel_by_handle("@unique_handle")
        self.assertEqual(found1.id, "ch_handle_test")

        # Retrieval without @
        found2 = self.service.get_channel_by_handle("unique_handle")
        self.assertEqual(found2.id, "ch_handle_test")

        with self.assertRaises(ChannelNotFoundException):
            self.service.get_channel_by_handle("@does_not_exist")

    def test_update_channel_success(self):
        """Verify updating existing channel properties."""
        self.service.create_channel(
            ChannelCreate(id="ch_update_me", name="Original Name", handle="@update_me", niche="original_niche")
        )

        updates = ChannelUpdate(name="Updated Name", niche="updated_niche")
        updated = self.service.update_channel("ch_update_me", updates)

        self.assertEqual(updated.name, "Updated Name")
        self.assertEqual(updated.niche, "updated_niche")
        self.assertEqual(updated.handle, "@update_me")

    def test_update_channel_not_found(self):
        """Verify updating non-existent channel raises ChannelNotFoundException."""
        with self.assertRaises(ChannelNotFoundException):
            self.service.update_channel("ch_ghost", ChannelUpdate(name="Ghost"))

    def test_update_channel_handle_conflict(self):
        """Verify updating channel handle to an existing handle of another channel raises ValidationException."""
        self.service.create_channel(
            ChannelCreate(id="ch_ch1", name="Channel 1", handle="@handle_ch1", niche="tech")
        )
        self.service.create_channel(
            ChannelCreate(id="ch_ch2", name="Channel 2", handle="@handle_ch2", niche="gaming")
        )

        # Attempt to change ch2's handle to ch1's handle
        with self.assertRaises(ValidationException) as ctx:
            self.service.update_channel("ch_ch2", ChannelUpdate(handle="@handle_ch1"))

        self.assertIn("already registered", str(ctx.exception))

    def test_reject_invalid_data(self):
        """Verify invalid payloads and empty parameters raise ValidationException."""
        # Empty channel ID
        with self.assertRaises(ValidationException):
            self.service.get_channel("")

        with self.assertRaises(ValidationException):
            self.service.get_channel("   ")

        with self.assertRaises(ValidationException):
            self.service.get_channel_by_handle("")

        # Missing required fields in dictionary payload
        with self.assertRaises(ValidationException):
            self.service.create_channel({"name": "No ID"})

        # Unsupported input type
        with self.assertRaises(ValidationException):
            self.service.create_channel(12345)  # type: ignore

    def test_duplicate_channel_id_and_handle(self):
        """Verify service rejects duplicate channel ID or handle."""
        self.service.create_channel(
            ChannelCreate(id="ch_dup_id", name="Dup Channel", handle="@dup_handle", niche="news")
        )

        # Duplicate ID
        with self.assertRaises(ValidationException) as ctx_id:
            self.service.create_channel(
                ChannelCreate(id="ch_dup_id", name="Other Name", handle="@other_handle", niche="news")
            )
        self.assertIn("already exists", str(ctx_id.exception))

        # Duplicate Handle
        with self.assertRaises(ValidationException) as ctx_h:
            self.service.create_channel(
                ChannelCreate(id="ch_new_id", name="Other Channel", handle="@dup_handle", niche="news")
            )
        self.assertIn("already registered", str(ctx_h.exception))

    def test_no_direct_database_access_on_service(self):
        """Verify ChannelService depends solely on Repository and does not execute SQL directly (ADR-001)."""
        self.assertFalse(hasattr(self.service, "db_manager"))
        self.assertFalse(hasattr(self.service, "connection"))
        self.assertFalse(hasattr(self.service, "execute"))
        self.assertIsInstance(self.service.repository, ChannelRepository)

    def test_soft_and_hard_deletion(self):
        """Verify soft-delete marks is_active=False and hard-delete purges DB and files."""
        channel = self.service.create_channel(
            ChannelCreate(id="ch_delete_me", name="Delete Me", handle="@del_me", niche="temp")
        )
        channel_dir = self.channels_dir / "ch_delete_me"
        self.assertTrue(channel_dir.exists())

        # 1. Soft-delete (default)
        self.service.delete_channel("ch_delete_me", hard_delete=False)
        fetched = self.service.get_channel("ch_delete_me")
        self.assertFalse(fetched.is_active)
        self.assertTrue(channel_dir.exists(), "Workspace directory must remain intact on soft-delete")

        # Inactive channel is excluded from default list_channels
        active_list = self.service.list_channels(include_inactive=False)
        self.assertNotIn("ch_delete_me", [c.id for c in active_list])

        all_list = self.service.list_channels(include_inactive=True)
        self.assertIn("ch_delete_me", [c.id for c in all_list])

        # 2. Hard-delete with purge_files=True
        self.service.delete_channel("ch_delete_me", hard_delete=True, purge_files=True)
        with self.assertRaises(ChannelNotFoundException):
            self.service.get_channel("ch_delete_me")
        self.assertFalse(channel_dir.exists(), "Workspace directory should be purged on hard-delete")

    def test_filesystem_failure_compensating_rollback(self):
        """Verify that if filesystem workspace initialization fails, the DB record is rolled back (no orphan)."""
        data = ChannelCreate(
            id="ch_fs_fail",
            name="FS Fail Channel",
            handle="@fs_fail",
            niche="testing",
        )

        # Simulate filesystem failure during workspace directory initialization
        with patch.object(
            self.repo,
            "_initialize_channel_workspace",
            side_effect=OSError("Simulated disk error during directory creation"),
        ):
            with self.assertRaises(StudioException) as ctx:
                self.service.create_channel(data)

            self.assertIn("Failed to initialize workspace", str(ctx.exception))

        # Verify DB record was rolled back: channel does NOT exist in DB
        self.assertFalse(self.repo.exists("ch_fs_fail"))
        with self.assertRaises(ChannelNotFoundException):
            self.service.get_channel("ch_fs_fail")

        # Verify no orphan directory remains on disk
        channel_dir = self.channels_dir / "ch_fs_fail"
        self.assertFalse(channel_dir.exists())

    def test_validate_channel_exists(self):
        """Verify validate_channel_exists returns True or raises ChannelNotFoundException."""
        self.service.create_channel(
            ChannelCreate(id="ch_check_me", name="Check Me", handle="@check_me", niche="qa")
        )
        self.assertTrue(self.service.validate_channel_exists("ch_check_me"))

        with self.assertRaises(ChannelNotFoundException):
            self.service.validate_channel_exists("ch_missing")

    def test_logging_context_and_operations(self):
        """Verify operational and error logs capture channel_id in log context."""
        log_records = []

        class TestLogHandler(logging.Handler):
            def emit(self, record):
                log_records.append(record)

        handler = TestLogHandler()
        service_logger = logging.getLogger("test_service_logger")
        service_logger.setLevel(logging.INFO)
        service_logger.addHandler(handler)

        test_service = ChannelService(repository=self.repo, service_logger=service_logger)

        # Perform channel creation
        test_service.create_channel(
            ChannelCreate(id="ch_logged_01", name="Logged", handle="@logged_handle", niche="logs")
        )

        messages = [r.getMessage() for r in log_records]
        self.assertTrue(any("Initiating creation of channel 'ch_logged_01'" in m for m in messages))
        self.assertTrue(any("created successfully" in m for m in messages))

        # Perform invalid retrieval to verify warning log
        with self.assertRaises(ChannelNotFoundException):
            test_service.get_channel("ch_unknown_xyz")

        warning_messages = [r.getMessage() for r in log_records if r.levelno == logging.WARNING]
        self.assertTrue(any("Channel retrieval failed" in m for m in warning_messages))

