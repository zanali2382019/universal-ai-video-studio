"""Unit and integration tests for Media Asset Management (MAM) and License Management with Export Gate."""
import unittest
import tempfile
import gc
from pathlib import Path

from src.database.connection import DatabaseManager
from src.database.migrations import MigrationManager
from src.database.repositories.channel_repository import ChannelRepository
from src.database.repositories.project_repository import ProjectRepository
from src.database.repositories.scene_repository import SceneRepository
from src.database.repositories.asset_repository import AssetRepository
from src.database.repositories.license_repository import LicenseRepository
from src.database.repositories.file_operation_repository import FileOperationRepository
from src.database.repositories.audit_repository import AuditRepository
from src.database.models import ChannelCreate, ProjectCreate, SceneCreate
from src.services.asset_service import AssetService
from src.services.license_service import LicenseService
from src.services.audit_service import AuditService
from src.core.config import get_settings
from src.core.constants import AssetType, LegalBasis, VerificationStatus
from src.core.exceptions import (
    AssetNotFoundException,
    AssetInUseException,
    LicenseVerificationException,
    PathTraversalSecurityException,
    ValidationException,
)


class TestMAMAndLicenses(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root_path = Path(self.temp_dir.name)
        self.db_path = self.root_path / "test_mam.db"
        self.channels_dir = self.root_path / "channels"
        self.channels_dir.mkdir(parents=True, exist_ok=True)

        self.settings = get_settings()
        self._orig_channels_root = self.settings.channels_root
        self.settings.channels_root = self.channels_dir

        self.db_manager = DatabaseManager(db_path=self.db_path)
        self.migration_manager = MigrationManager(self.db_manager)
        self.migration_manager.run_migrations()

        self.channel_repo = ChannelRepository(self.db_manager)
        self.project_repo = ProjectRepository(self.db_manager)
        self.scene_repo = SceneRepository(self.db_manager)
        self.asset_repo = AssetRepository(self.db_manager)
        self.license_repo = LicenseRepository(self.db_manager)
        self.file_op_repo = FileOperationRepository(self.db_manager)
        self.audit_repo = AuditRepository(self.db_manager)

        self.audit_service = AuditService(self.audit_repo)
        self.license_service = LicenseService(self.license_repo, self.audit_service)
        self.asset_service = AssetService(
            asset_repository=self.asset_repo,
            license_repository=self.license_repo,
            file_operation_repository=self.file_op_repo,
            audit_service=self.audit_service,
        )

        # Setup Channel Alpha & Beta
        self.channel_repo.create(
            ChannelCreate(id="ch_alpha", name="Alpha Studio", handle="@alpha", niche="3d")
        )
        self.channel_repo.create(
            ChannelCreate(id="ch_beta", name="Beta Studio", handle="@beta", niche="vlog")
        )

        # Create dummy sample media files
        self.sample_img1 = self.root_path / "sample_character.png"
        self.sample_img1.write_bytes(b"FAKE_PNG_BINARY_BYTES_FOR_TOMMY_CHARACTER_01")

        self.sample_audio = self.root_path / "sample_voice.wav"
        self.sample_audio.write_bytes(b"FAKE_WAV_AUDIO_BYTES_FOR_VOICE_SAMPLE_01")

    def tearDown(self):
        self.settings.channels_root = self._orig_channels_root
        gc.collect()
        self.temp_dir.cleanup()

    def test_asset_ingestion_and_sha256_deduplication(self):
        """Verify unique asset ingestion calculates SHA-256 and identical upload reuses storage."""
        # 1. Ingest sample image into Channel Alpha
        asset1 = self.asset_service.ingest_asset(
            channel_id="ch_alpha",
            source_path=self.sample_img1,
            asset_type=AssetType.IMAGE,
            asset_category="character_model_sheet",
            character_id="chara_tommy",
        )
        self.assertEqual(asset1.asset_type, AssetType.IMAGE)
        self.assertEqual(asset1.channel_id, "ch_alpha")
        self.assertTrue(asset1.file_path.startswith("media_vault\\originals\\images") or asset1.file_path.startswith("media_vault/originals/images"))

        # Physical file exists on disk in originals
        physical_path = self.channels_dir / "ch_alpha" / asset1.file_path
        self.assertTrue(physical_path.exists())

        # Count files in originals/images/
        images_dir = self.channels_dir / "ch_alpha" / "media_vault" / "originals" / "images"
        self.assertEqual(len(list(images_dir.glob("*"))), 1)

        # 2. Ingest duplicate file with different source filename
        dup_file = self.root_path / "sample_character_copy.png"
        dup_file.write_bytes(b"FAKE_PNG_BINARY_BYTES_FOR_TOMMY_CHARACTER_01")  # identical content

        asset2 = self.asset_service.ingest_asset(
            channel_id="ch_alpha",
            source_path=dup_file,
            asset_type=AssetType.IMAGE,
            asset_category="scene_bg",
        )
        self.assertNotEqual(asset1.id, asset2.id)
        self.assertEqual(asset1.file_sha256, asset2.file_sha256)
        self.assertEqual(asset1.file_path, asset2.file_path)  # Reused same physical path!

        # Storage deduplication verified: Still only 1 physical file on disk!
        self.assertEqual(len(list(images_dir.glob("*"))), 1)

    def test_four_tier_asset_deletion_and_retention(self):
        """Verify Disable, Archive, Remove Association, and Purge constraints."""
        asset = self.asset_service.ingest_asset(
            channel_id="ch_alpha",
            source_path=self.sample_audio,
            asset_type=AssetType.AUDIO,
            asset_category="voice_sample",
        )

        # 1. Disable
        disabled = self.asset_service.disable_asset("ch_alpha", asset.id)
        self.assertFalse(disabled.is_active)

        # 2. Archive
        archived = self.asset_service.archive_asset("ch_alpha", asset.id)
        self.assertTrue(archived.is_archived)

        # 3. Test active reference block on physical purge:
        # Link asset to project and scene
        self.project_repo.create(ProjectCreate(id="proj_ref", channel_id="ch_alpha", title="Ref Project"))
        self.scene_repo.create(
            SceneCreate(id="sc_ref", project_id="proj_ref", channel_id="ch_alpha", sequence_number=1)
        )
        self.asset_service.remove_association("ch_alpha", asset.id, project_id="proj_ref", scene_id="sc_ref")

        # Attempt to purge while referenced -> AssetInUseException
        with self.assertRaises(AssetInUseException):
            self.asset_service.purge_asset_physical("ch_alpha", asset.id)

        # 4. Unlink and purge successfully
        self.asset_service.remove_association("ch_alpha", asset.id, project_id=None, scene_id=None)
        purged = self.asset_service.purge_asset_physical("ch_alpha", asset.id)
        self.assertTrue(purged)

        # Verify asset is deleted from DB
        with self.assertRaises(AssetNotFoundException):
            self.asset_service.get_asset("ch_alpha", asset.id)

        # Verify physical file removed
        physical_path = self.channels_dir / "ch_alpha" / asset.file_path
        self.assertFalse(physical_path.exists())

        # Verify audit record of purge exists in audit log
        audit_log = self.channels_dir / "ch_alpha" / "audit" / "license_audit.log"
        self.assertTrue(audit_log.exists())
        self.assertIn("PURGE_ASSET_PHYSICAL", audit_log.read_text(encoding="utf-8"))

    def test_strict_export_gate(self):
        """Verify Export Gate blocks unverified, restricted, or locked channels (ADR-008)."""
        asset = self.asset_service.ingest_asset(
            channel_id="ch_alpha",
            source_path=self.sample_img1,
            asset_type=AssetType.IMAGE,
        )

        # 1. Unverified asset (pending_review) is blocked by export gate
        with self.assertRaises(LicenseVerificationException) as ctx:
            self.license_service.check_export_eligibility("ch_alpha", [asset.id])
        self.assertIn("not verified", str(ctx.exception))

        # 2. Verify asset with documented legal basis
        lic = self.license_service.get_license_by_asset("ch_alpha", asset.id)
        self.assertIsNotNone(lic)

        self.license_service.verify_license(
            channel_id="ch_alpha",
            license_id=lic.id,
            legal_basis=LegalBasis.PROPRIETARY_OWNERSHIP,
            verified_by="legal_admin",
            commercial_use_allowed=True,
            notes="Original 3D Model created in-house",
        )

        # Now export gate clears
        cleared = self.license_service.check_export_eligibility("ch_alpha", [asset.id])
        self.assertTrue(cleared)

        # 3. Restricted asset is blocked
        self.license_service.restrict_license("ch_alpha", lic.id, reason="DMCA Notice received")
        with self.assertRaises(LicenseVerificationException) as ctx_res:
            self.license_service.check_export_eligibility("ch_alpha", [asset.id])
        self.assertIn("restricted", str(ctx_res.exception).lower())

