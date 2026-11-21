"""Unit tests for Task 3.9: GitHub Repository & Media Storage Separation.

Tests:
1. LocalStorageProvider: atomic file storage, path traversal protection, streams, hashes.
2. StorageService: provider dispatch, key building, facade methods.
3. STUDIO_DATA_DIR configuration resolution: isolated directory routing.
4. Database Schema Migration 4: storage columns, index, and backfill.
5. AssetService & AssetRepository: ingestion with rich storage references and query by storage_key.
"""
import io
import os
import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

from src.core.config import Settings
from src.core.constants import AssetType, LegalBasis
from src.core.exceptions import (
    StorageException,
    StorageFileNotFoundException,
    StorageKeyInvalidException,
    PathTraversalSecurityException,
)
from src.database.connection import DatabaseManager
from src.database.migrations import MigrationManager
from src.database.models import MediaAssetCreate
from src.database.repositories.asset_repository import AssetRepository
from src.database.repositories.license_repository import LicenseRepository
from src.database.repositories.file_operation_repository import FileOperationRepository
from src.database.repositories.audit_repository import AuditRepository
from src.services.asset_service import AssetService
from src.services.audit_service import AuditService
from src.storage.local_provider import LocalStorageProvider
from src.storage.service import StorageService
from src.utils.file_utils import calculate_file_sha256


def calculate_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class TestLocalStorageProvider(unittest.TestCase):
    """Test LocalStorageProvider isolation, atomic writes, and security boundaries."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="studio_test_storage_")
        self.base_dir = Path(self.temp_dir)
        self.provider = LocalStorageProvider(base_dir=self.base_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_save_and_load_bytes(self):
        key = "channels/ch_01/media/audio.wav"
        data = b"RIFF....test_wav_content_bytes"
        expected_sha = calculate_sha256(data)

        meta = self.provider.save(key, data, mime_type="audio/wav")
        self.assertEqual(meta.key, key)
        self.assertEqual(meta.size_bytes, len(data))
        self.assertEqual(meta.sha256, expected_sha)
        self.assertEqual(meta.mime_type, "audio/wav")

        loaded = self.provider.load(key)
        self.assertEqual(loaded, data)

    def test_save_from_stream(self):
        key = "channels/ch_01/images/hero.png"
        data = b"\x89PNG\r\n\x1a\n...sample_png_bytes"
        stream = io.BytesIO(data)

        meta = self.provider.save(key, stream, mime_type="image/png")
        self.assertEqual(meta.size_bytes, len(data))
        self.assertEqual(meta.sha256, calculate_sha256(data))
        self.assertTrue(self.provider.exists(key))

    def test_save_from_file_path(self):
        src_path = self.base_dir / "temp_input.txt"
        src_path.write_text("sample content to ingest", encoding="utf-8")

        key = "docs/sample.txt"
        meta = self.provider.save(key, src_path)
        self.assertEqual(meta.size_bytes, src_path.stat().st_size)
        self.assertEqual(self.provider.load(key), b"sample content to ingest")

    def test_get_local_path(self):
        key = "channels/ch_01/videos/export.mp4"
        self.provider.save(key, b"mp4data")
        local_path = self.provider.get_local_path(key)
        self.assertTrue(local_path.exists())
        self.assertEqual(local_path, (self.base_dir / key).resolve())

    def test_list_keys(self):
        self.provider.save("channels/ch_01/a.txt", b"a")
        self.provider.save("channels/ch_01/b.txt", b"b")
        self.provider.save("channels/ch_02/c.txt", b"c")

        keys = self.provider.list_keys("channels/ch_01")
        self.assertEqual(len(keys), 2)
        self.assertIn("channels/ch_01/a.txt", keys)
        self.assertIn("channels/ch_01/b.txt", keys)
        self.assertNotIn("channels/ch_02/c.txt", keys)

    def test_delete_key(self):
        key = "temp/to_delete.bin"
        self.provider.save(key, b"bye")
        self.assertTrue(self.provider.exists(key))
        deleted = self.provider.delete(key)
        self.assertTrue(deleted)
        self.assertFalse(self.provider.exists(key))

    def test_path_traversal_rejection(self):
        traversal_keys = [
            "../secret.txt",
            "channels/../../etc/passwd",
            "C:/Windows/System32/calc.exe",
            "\\absolute\\path\\file.txt",
            "/etc/shadow",
        ]
        for bad_key in traversal_keys:
            with self.subTest(key=bad_key):
                with self.assertRaises((PathTraversalSecurityException, StorageKeyInvalidException)):
                    self.provider.save(bad_key, b"attack")

    def test_load_nonexistent_raises_exception(self):
        with self.assertRaises(StorageFileNotFoundException):
            self.provider.load("nonexistent/key.mp4")


class TestStorageService(unittest.TestCase):
    """Test StorageService facade dispatch and key formatting."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="studio_test_svc_")
        self.service = StorageService(local_base_dir=Path(self.temp_dir))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_key_builders(self):
        asset_key = StorageService.build_channel_asset_key(
            channel_id="ch_dreammatter",
            category="images",
            file_name="tommy.png",
            asset_id="ast_123",
        )
        self.assertEqual(asset_key, "channels/ch_dreammatter/media_vault/originals/images/ast_123_tommy.png")

        export_key = StorageService.build_export_key("ch_dreammatter", "final.mp4")
        self.assertEqual(export_key, "channels/ch_dreammatter/exports/videos/final.mp4")

    def test_save_and_load_facade(self):
        key = StorageService.build_export_key("ch_test", "ep1.mp4")
        meta = self.service.save_file(key, b"video_data_123", mime_type="video/mp4")
        self.assertEqual(meta.key, key)
        self.assertEqual(self.service.load_bytes(key), b"video_data_123")
        self.assertTrue(self.service.exists(key))

    def test_unregistered_provider_raises(self):
        with self.assertRaises(StorageException):
            self.service.get_provider("s3_not_configured")


class TestStudioDataDirConfiguration(unittest.TestCase):
    """Test STUDIO_DATA_DIR configuration isolation hierarchy."""

    def test_studio_data_dir_resolution(self):
        with tempfile.TemporaryDirectory(prefix="studio_data_test_") as tmp_data_dir:
            custom_data_path = Path(tmp_data_dir)
            settings = Settings(studio_data_dir=custom_data_path)

            self.assertEqual(settings.studio_data_dir, custom_data_path)
            self.assertEqual(settings.channels_root, (custom_data_path / "channels").resolve())
            self.assertEqual(settings.workspace_root, (custom_data_path / "workspace").resolve())
            self.assertEqual(settings.db_path, (custom_data_path / "workspace" / "runtime" / "studio.db").resolve())
            self.assertEqual(settings.backups_dir, (custom_data_path / "backups").resolve())

    def test_default_without_studio_data_dir(self):
        settings = Settings(studio_data_dir=None)
        self.assertIsNotNone(settings.studio_data_dir)
        # Defaults remain relative to project root
        self.assertTrue(str(settings.channels_root).endswith("channels"))
        self.assertTrue(str(settings.workspace_root).endswith("workspace"))

    def test_historical_asset_reading_with_custom_studio_data_dir(self):
        """Verify that historical assets are accessible via StorageService after moving/setting STUDIO_DATA_DIR."""
        with tempfile.TemporaryDirectory(prefix="studio_external_data_") as external_data_dir:
            ext_data_path = Path(external_data_dir)

            # Setup simulated historical asset file in external data directory
            historical_key = "channels/ch_dreammatter_001/media_vault/originals/images/tommy_scene_1.jpg"
            target_file = ext_data_path / historical_key
            target_file.parent.mkdir(parents=True, exist_ok=True)
            dummy_content = b"SIMULATED_HISTORICAL_JPEG_DATA_12345"
            target_file.write_bytes(dummy_content)

            # Initialize StorageService with the external base directory
            storage = StorageService(local_base_dir=ext_data_path)

            self.assertTrue(storage.exists(historical_key))
            loaded_bytes = storage.load_bytes(historical_key)
            self.assertEqual(loaded_bytes, dummy_content)
            self.assertEqual(storage.get_local_path(historical_key), target_file.resolve())


class TestStorageDatabaseMigrationAndAssetService(unittest.TestCase):
    """Test schema migration 4, storage column persistence, and AssetService integration."""

    def setUp(self):
        self.channel_id = "ch_task39_test"
        ch_dir = Path("channels") / self.channel_id
        if ch_dir.exists():
            shutil.rmtree(ch_dir, ignore_errors=True)

        self.temp_dir = tempfile.mkdtemp(prefix="studio_test_mam_")
        self.db_path = Path(self.temp_dir) / "test_studio.db"
        self.db_manager = DatabaseManager(self.db_path)

        # Run all migrations including migration 4
        migrator = MigrationManager(self.db_manager)
        applied = migrator.run_migrations()
        self.assertIn(4, applied)

        self.storage_service = StorageService(local_base_dir=Path(self.temp_dir))
        self.asset_repo = AssetRepository(self.db_manager)
        self.license_repo = LicenseRepository(self.db_manager)
        self.file_op_repo = FileOperationRepository(self.db_manager)
        self.audit_repo = AuditRepository(self.db_manager)
        self.audit_service = AuditService(audit_repository=self.audit_repo)

        # Create test channel record to satisfy foreign keys
        with self.db_manager.transaction() as conn:
            conn.execute(
                f"""
                INSERT INTO channels (
                    id, name, handle, niche, default_language, created_at, updated_at
                ) VALUES ('{self.channel_id}', 'Test Channel', '@task39_test', 'animation', 'en', '2026-01-01', '2026-01-01');
                """
            )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        ch_dir = Path("channels") / self.channel_id
        if ch_dir.exists():
            shutil.rmtree(ch_dir, ignore_errors=True)

    def test_asset_repo_stores_and_queries_storage_fields(self):
        asset_create = MediaAssetCreate(
            id="ast_custom_storage_01",
            channel_id=self.channel_id,
            file_name="background.png",
            file_path=f"channels/{self.channel_id}/media_vault/originals/images/background.png",
            file_sha256="abc123def456" * 5 + "abcd",
            file_size_bytes=1024,
            mime_type="image/png",
            asset_type=AssetType.IMAGE,
            asset_category="background",
            storage_provider="local",
            storage_key=f"channels/{self.channel_id}/media_vault/originals/images/ast_custom_storage_01_background.png",
            license_reference="lic_proprietary_01",
        )
        created = self.asset_repo.create(asset_create)
        self.assertEqual(created.storage_provider, "local")
        self.assertEqual(
            created.storage_key,
            f"channels/{self.channel_id}/media_vault/originals/images/ast_custom_storage_01_background.png",
        )
        self.assertEqual(created.license_reference, "lic_proprietary_01")

        # Query by storage key
        fetched = self.asset_repo.get_by_storage_key(
            self.channel_id,
            f"channels/{self.channel_id}/media_vault/originals/images/ast_custom_storage_01_background.png",
        )
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.id, "ast_custom_storage_01")

    def test_asset_service_ingestion_populates_storage_metadata(self):
        asset_svc = AssetService(
            asset_repository=self.asset_repo,
            license_repository=self.license_repo,
            file_operation_repository=self.file_op_repo,
            audit_service=self.audit_service,
            storage_service=self.storage_service,
        )

        # Create a sample physical file to ingest
        sample_file = Path(self.temp_dir) / "sample_voiceover.wav"
        sample_file.write_bytes(b"RIFF_sample_voiceover_data_for_test")

        asset = asset_svc.ingest_asset(
            channel_id=self.channel_id,
            source_path=sample_file,
            asset_type=AssetType.AUDIO,
            asset_category="voiceover",
            legal_basis=LegalBasis.PROPRIETARY_OWNERSHIP,
        )

        self.assertEqual(asset.storage_provider, "local")
        self.assertTrue(asset.storage_key.startswith(f"channels/{self.channel_id}/media_vault/originals/audios/"))
        self.assertTrue(asset.storage_key.endswith("sample_voiceover.wav"))
        self.assertTrue(asset.license_reference.startswith("lic_"))

        # Verify lookup by storage_key
        found = asset_svc.get_asset_by_storage_key(self.channel_id, asset.storage_key)
        self.assertIsNotNone(found)
        self.assertEqual(found.id, asset.id)


if __name__ == "__main__":
    unittest.main()
