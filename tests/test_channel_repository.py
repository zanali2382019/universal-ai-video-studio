"""Unit tests for ChannelRepository: creation, validation, uniqueness, updates, and directories."""
import unittest
import tempfile
import gc
from pathlib import Path
from src.database.connection import DatabaseManager
from src.database.migrations import MigrationManager
from src.database.repositories.channel_repository import ChannelRepository
from src.database.models import ChannelCreate, ChannelUpdate
from src.core.constants import AspectRatio, GenerationMode
from src.core.exceptions import ValidationException, ChannelNotFoundException


class TestChannelRepository(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root_path = Path(self.temp_dir.name)
        self.db_path = self.root_path / "test_channels.db"
        
        self.db_manager = DatabaseManager(db_path=self.db_path)
        self.migration_manager = MigrationManager(self.db_manager)
        self.migration_manager.run_migrations()

        self.repo = ChannelRepository(db_manager=self.db_manager)

    def tearDown(self):
        gc.collect()
        self.temp_dir.cleanup()

    def test_create_channel_success(self):
        """Test successful creation and retrieval of a new channel."""
        data = ChannelCreate(
            id="ch_dreammatter_001",
            name="DreamMatter",
            handle="DreamMatterWorld",  # Validator should prepend @
            niche="3d_animation_comedy",
            default_language="en",
            supported_locales=["en", "ar", "es"],
            default_aspect_ratio=AspectRatio.VERTICAL_9_16,
            default_generation_mode=GenerationMode.IMAGE_TO_VIDEO,
        )

        channel = self.repo.create(data)
        self.assertEqual(channel.id, "ch_dreammatter_001")
        self.assertEqual(channel.name, "DreamMatter")
        self.assertEqual(channel.handle, "@DreamMatterWorld")
        self.assertEqual(channel.supported_locales, ["en", "ar", "es"])
        self.assertTrue(channel.is_active)

        # Retrieve by ID
        fetched = self.repo.get_by_id("ch_dreammatter_001")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.handle, "@DreamMatterWorld")

        # Retrieve by handle
        fetched_by_handle = self.repo.get_by_handle("@DreamMatterWorld")
        self.assertIsNotNone(fetched_by_handle)
        self.assertEqual(fetched_by_handle.id, "ch_dreammatter_001")

    def test_prevent_duplicate_channel_id(self):
        """Verify duplicate channel ID raises ValidationException."""
        data1 = ChannelCreate(
            id="ch_unique_id",
            name="Channel One",
            handle="@handle_one",
            niche="tech",
        )
        self.repo.create(data1)

        data2 = ChannelCreate(
            id="ch_unique_id",  # Duplicate ID
            name="Channel Two",
            handle="@handle_two",
            niche="tech",
        )
        with self.assertRaises(ValidationException) as ctx:
            self.repo.create(data2)
        self.assertIn("already exists", str(ctx.exception))

    def test_prevent_duplicate_channel_handle(self):
        """Verify duplicate channel handle raises ValidationException."""
        data1 = ChannelCreate(
            id="ch_first_id",
            name="Channel One",
            handle="@shared_handle",
            niche="history",
        )
        self.repo.create(data1)

        data2 = ChannelCreate(
            id="ch_second_id",
            name="Channel Two",
            handle="@shared_handle",  # Duplicate handle
            niche="history",
        )
        with self.assertRaises(ValidationException) as ctx:
            self.repo.create(data2)
        self.assertIn("already registered", str(ctx.exception))

    def test_update_channel(self):
        """Verify updating channel properties."""
        data = ChannelCreate(
            id="ch_update_test",
            name="Old Name",
            handle="@old_handle",
            niche="comedy",
        )
        self.repo.create(data)

        updates = ChannelUpdate(
            name="New Name Updated",
            niche="satire_and_comedy",
            default_aspect_ratio=AspectRatio.SQUARE_1_1,
        )
        updated = self.repo.update("ch_update_test", updates)
        self.assertEqual(updated.name, "New Name Updated")
        self.assertEqual(updated.niche, "satire_and_comedy")
        self.assertEqual(updated.default_aspect_ratio, AspectRatio.SQUARE_1_1)

    def test_update_non_existent_channel_raises(self):
        """Verify updating a non-existent channel raises ChannelNotFoundException."""
        with self.assertRaises(ChannelNotFoundException):
            self.repo.update("ch_does_not_exist", ChannelUpdate(name="Fake"))


if __name__ == "__main__":
    unittest.main()

