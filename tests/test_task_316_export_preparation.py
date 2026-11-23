"""Test Suite for Task 3.16: Export & Publishing Preparation.

Validates:
1. AST isolation: Zero direct sqlite3 imports in src/web/.
2. Platform Export Profiles (YouTube Shorts, Reels, TikTok, YouTube Standard).
3. Arabic Thumbnail Rendering (Pillow, RTL shaping, multi-line wrap, badges).
4. Strict License Preflight (ADR-008 gating, audit reports, rejection details).
5. Final Media Quality Gates (EBU R128 loudness, True Peak ceiling, 'Not Measured' handling).
6. Grounded Platform Metadata Generator (zero hallucination, character limits, chapters).
7. Atomic Package Assembler (manifest SHA-256, relative ZIP archive, staging cleanup).
8. WebUI routes and release ZIP download.
"""
import ast
import os
import json
import shutil
import zipfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from PIL import Image
from fastapi.testclient import TestClient

from src.database.connection import DatabaseManager
from src.database.migrations import MigrationManager
from src.database.models import (
    ChannelModel,
    ChannelCreate,
    ProjectModel,
    ProjectCreate,
    SceneModel,
    SceneCreate,
    MediaAssetCreate,
    AssetLicenseCreate,
)
from src.database.repositories.channel_repository import ChannelRepository
from src.database.repositories.project_repository import ProjectRepository
from src.database.repositories.scene_repository import SceneRepository
from src.database.repositories.asset_repository import AssetRepository
from src.database.repositories.license_repository import LicenseRepository
from src.database.repositories.audit_repository import AuditRepository
from src.database.repositories.export_repository import ExportRepository
from src.core.constants import AssetType, LegalBasis, VerificationStatus
from src.core.exceptions import LicenseVerificationException, ValidationException
from src.services.license_service import LicenseService
from src.services.audit_service import AuditService
from src.services.export_service import ExportService
from src.export.profiles import (
    get_all_profiles,
    get_profile_by_id,
    ExportPlatform,
    YOUTUBE_SHORTS_PROFILE,
    INSTAGRAM_REELS_PROFILE,
    TIKTOK_PROFILE,
)
from src.export.thumbnail import ArabicThumbnailGenerator
from src.export.preflight import LicensePreflightService
from src.export.quality_gates import MediaQualityGateService, QualityGateReport
from src.export.metadata_generator import PlatformMetadataGenerator
from src.export.package_assembler import ExportPackageAssembler, calculate_sha256
from src.web.app import create_app
from src.web.dependencies import ServiceContainer, reset_service_container
from src.storage.service import StorageService


class TestExportPreparationSuite(unittest.TestCase):
    """Full test suite covering all mandatory engineering guardrails for Task 3.16."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="task316_test_")
        self.base_path = Path(self.temp_dir)

    def tearDown(self):
        reset_service_container()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_ast_zero_sqlite3_in_src_web(self):
        """Verify that src/web/ contains strictly ZERO direct sqlite3 imports."""
        web_dir = Path(__file__).resolve().parent.parent / "src" / "web"
        self.assertTrue(web_dir.exists())
        violations = []

        for py_file in web_dir.rglob("*.py"):
            with open(py_file, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=str(py_file))

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if "sqlite" in alias.name.lower():
                            violations.append((py_file.name, alias.name))
                elif isinstance(node, ast.ImportFrom):
                    if node.module and "sqlite" in node.module.lower():
                        violations.append((py_file.name, node.module))

        self.assertEqual(violations, [], f"AST Isolation Failure in src/web: {violations}")

    def test_02_platform_export_profiles_specs(self):
        """Verify independent, versioned platform profiles and constraints."""
        profiles = get_all_profiles()
        self.assertGreaterEqual(len(profiles), 4)

        p_ids = [p.profile_id for p in profiles]
        self.assertIn("youtube_shorts", p_ids)
        self.assertIn("instagram_reels", p_ids)
        self.assertIn("tiktok", p_ids)
        self.assertIn("youtube_standard", p_ids)

        shorts = get_profile_by_id("youtube_shorts")
        self.assertIsNotNone(shorts)
        self.assertEqual(shorts.aspect_ratio, "9:16")
        self.assertEqual((shorts.width, shorts.height), (1080, 1920))
        self.assertEqual(shorts.max_duration_seconds, 180.0)
        self.assertEqual(shorts.preferred_duration_sec, 60.0)
        self.assertEqual(shorts.target_lufs, -14.0)
        self.assertEqual(shorts.max_true_peak_dbtp, -1.0)
        self.assertTrue("YouTube Shorts" in shorts.notes or "YouTube Shorts" in shorts.name)

        reels = get_profile_by_id("instagram_reels")
        self.assertIsNotNone(reels)
        self.assertEqual(reels.max_duration_seconds, 90.0)

        tiktok = get_profile_by_id("tiktok")
        self.assertIsNotNone(tiktok)
        self.assertEqual(tiktok.max_duration_seconds, 180.0)

    def test_03_arabic_thumbnail_generation(self):
        """Test Arabic text shaping, multi-line wrap, and image creation."""
        generator = ArabicThumbnailGenerator()
        out_img = self.base_path / "test_thumb.png"

        res_path = generator.render_thumbnail(
            output_path=out_img,
            headline_ar="مغامرات تومي الرائعة في الغابة السحرية",
            subtitle_ar="الحلقة الأولى - الاكتشاف المثير",
            badge_text="حلقة حصرية",
            width=720,
            height=1280,
        )
        self.assertTrue(res_path.is_file())
        self.assertGreater(res_path.stat().st_size, 0)

        with Image.open(res_path) as img:
            self.assertEqual(img.size, (720, 1280))
            self.assertEqual(img.format, "PNG")

        # Mixed Arabic / English with background
        bg_img = self.base_path / "bg.png"
        Image.new("RGB", (720, 1280), color=(40, 80, 120)).save(bg_img)

        out_mixed = self.base_path / "test_mixed.png"
        generator.render_thumbnail(
            output_path=out_mixed,
            headline_ar="Tommy والمغامرة الكبرى 2026",
            badge_text="EPISODE 01",
            background_image_path=bg_img,
            width=720,
            height=1280,
        )
        self.assertTrue(out_mixed.is_file())

    def test_04_strict_license_preflight_audit(self):
        """Verify license preflight rejects unverified assets and approves valid ones."""
        channels_dir = self.base_path / "channels"
        channels_dir.mkdir(parents=True, exist_ok=True)
        from src.core.config import get_settings
        settings = get_settings()
        orig_channels_root = settings.channels_root
        settings.channels_root = channels_dir

        try:
            db_file = self.base_path / "preflight_test.db"
            db_mgr = DatabaseManager(db_path=db_file)
            MigrationManager(db_mgr).run_migrations()

            channel_repo = ChannelRepository(db_mgr)
            project_repo = ProjectRepository(db_mgr)
            scene_repo = SceneRepository(db_mgr)
            asset_repo = AssetRepository(db_mgr)
            license_repo = LicenseRepository(db_mgr)
            audit_repo = AuditRepository(db_mgr)
            audit_srv = AuditService(audit_repo)
            lic_srv = LicenseService(license_repo, audit_srv, db_manager=db_mgr)

            channel_repo.create(ChannelCreate(id="ch_test", name="Test Channel", handle="@test_ch", niche="education"))
            project_repo.create(ProjectCreate(id="proj_test", channel_id="ch_test", title="Test Episode", description="Desc"))

            dummy_file = self.base_path / "clip.mp4"
            dummy_file.write_bytes(b"dummy video content")
            asset_repo.create(MediaAssetCreate(
                id="asset_01",
                channel_id="ch_test",
                project_id="proj_test",
                file_name="clip.mp4",
                file_path=str(dummy_file),
                file_sha256="A"*64,
                file_size_bytes=dummy_file.stat().st_size,
                mime_type="video/mp4",
                asset_type=AssetType.VIDEO,
            ))

            preflight_srv = LicensePreflightService(lic_srv, asset_repo, scene_repo, channel_repo)

            # 1. Must fail when asset is unlicensed
            rep = preflight_srv.run_preflight_audit("ch_test", "proj_test")
            self.assertFalse(rep.passed)
            self.assertEqual(rep.total_assets, 1)
            self.assertEqual(rep.unverified_count, 1)

            with self.assertRaises(LicenseVerificationException):
                preflight_srv.assert_export_preflight("ch_test", "proj_test")

            # 2. Register pending license -> Still must fail
            lic = lic_srv.register_license("ch_test", AssetLicenseCreate(
                id="lic_01",
                channel_id="ch_test",
                asset_id="asset_01",
                legal_basis=LegalBasis.COMMERCIAL_LICENSE,
                source_uri="https://api.generator.test/v1",
            ))
            rep2 = preflight_srv.run_preflight_audit("ch_test", "proj_test")
            self.assertFalse(rep2.passed)
            self.assertEqual(rep2.unverified_count, 1)

            # 3. Verify license with commercial rights -> Must PASS
            lic_srv.verify_license(
                channel_id="ch_test",
                license_id=lic.id,
                legal_basis=LegalBasis.COMMERCIAL_LICENSE,
                verified_by="legal_reviewer",
                proof_file_path=str(dummy_file),
                proof_sha256="A"*64,
                commercial_use_allowed=True,
            )

            rep3 = preflight_srv.run_preflight_audit("ch_test", "proj_test")
            self.assertTrue(rep3.passed)
            self.assertEqual(rep3.unverified_count, 0)
            self.assertEqual(rep3.verified_count, 1)
        finally:
            settings.channels_root = orig_channels_root

    def test_05_media_quality_gates(self):
        """Verify EBU R128 loudness, true peak, codec checks, and 'Not Measured' behavior."""
        from src.rendering.probe_service import ProbeResult, VideoStreamInfo, AudioStreamInfo, TruePeakResult

        mock_probe = MagicMock()
        q_service = MediaQualityGateService(probe_service=mock_probe)
        profile = YOUTUBE_SHORTS_PROFILE

        dummy_vid = self.base_path / "valid_test.mp4"
        dummy_vid.write_bytes(b"dummy mp4 data")

        # 1. Compliant media test
        mock_probe.probe_file.return_value = ProbeResult(
            file_path=str(dummy_vid),
            format_name="mov,mp4,m4a,3gp,3g2,mj2",
            duration_sec=45.0,
            size_bytes=1024000,
            video_streams=[
                VideoStreamInfo(index=0, codec_name="h264", width=1080, height=1920, fps=30.0)
            ],
            audio_streams=[
                AudioStreamInfo(index=1, codec_name="aac", sample_rate=48000, channels=2)
            ],
        )
        mock_probe.measure_true_peak.return_value = TruePeakResult(
            file_path=str(dummy_vid),
            integrated_loudness_lufs=-14.2,
            true_peak_dbtp=-1.5,
            is_compliant=True,
            max_allowed_dbtp=-1.0,
            raw_summary="Summary",
        )

        rep = q_service.evaluate_media(dummy_vid, profile)
        self.assertTrue(rep.passed)
        self.assertEqual(len(rep.failure_reasons), 0)

        # 2. Loudness violation (-18.0 LUFS)
        mock_probe.measure_true_peak.return_value = TruePeakResult(
            file_path=str(dummy_vid),
            integrated_loudness_lufs=-18.0,
            true_peak_dbtp=-1.5,
            is_compliant=True,
            max_allowed_dbtp=-1.0,
            raw_summary="Summary",
        )
        rep2 = q_service.evaluate_media(dummy_vid, profile)
        self.assertFalse(rep2.passed)
        self.assertTrue(any("Integrated loudness" in r for r in rep2.failure_reasons))

        # 3. Missing audio -> Must mark "Not Measured" and NEVER false PASS
        mock_probe.probe_file.return_value = ProbeResult(
            file_path=str(dummy_vid),
            format_name="mp4",
            duration_sec=30.0,
            size_bytes=500000,
            video_streams=[
                VideoStreamInfo(index=0, codec_name="h264", width=1080, height=1920, fps=30.0)
            ],
            audio_streams=[],
        )
        rep3 = q_service.evaluate_media(dummy_vid, profile)
        self.assertFalse(rep3.passed)
        self.assertEqual(rep3.measurements["integrated_lufs"], "Not Measured")
        self.assertEqual(rep3.measurements["true_peak_dbtp"], "Not Measured")
        self.assertTrue(any(c.status == "NOT_MEASURED" for c in rep3.checks))

    def test_06_grounded_platform_metadata_generation(self):
        """Verify non-hallucinated grounded metadata and platform constraints."""
        proj = ProjectModel(
            id="p101",
            channel_id="ch_kids",
            title="تومي يبحث عن الكنز في الجزيرة المهجورة",
            description="حلقة مشوقة من مغامرات تومي في جزيرة الكنز الغامضة.",
            status="draft",
            metadata_json={"tags": ["كرتون", "أطفال", "مغامرات"]},
            created_at="2026-11-23T00:00:00",
            updated_at="2026-11-23T00:00:00",
        )

        scenes = [
            SceneModel(
                id="s1", project_id="p101", channel_id="ch_kids", sequence_number=1,
                title="الوصول إلى الشاطئ", narration_text="وصل تومي إلى شاطئ الجزيرة الذهبي.",
                duration_seconds=5.0, created_at="", updated_at="",
            ),
            SceneModel(
                id="s2", project_id="p101", channel_id="ch_kids", sequence_number=2,
                title="خريطة الكنز", narration_text="وجد تومي خريطة قديمة تحت صخرة كبيرة.",
                duration_seconds=7.0, created_at="", updated_at="",
            ),
        ]

        ch = ChannelModel(
            id="ch_kids",
            name="قناة مغامرات الأطفال",
            handle="@kids_adventures",
            niche="kids_animation",
            default_language="ar",
            profile_json={"tags": ["kids", "animation"]},
            created_at="",
            updated_at="",
        )

        yt_meta = PlatformMetadataGenerator.generate_metadata(proj, scenes, ch, YOUTUBE_SHORTS_PROFILE)
        self.assertEqual(yt_meta.platform, ExportPlatform.YOUTUBE_SHORTS.value)
        self.assertLessEqual(len(yt_meta.title), 100)
        self.assertTrue("#Shorts" in yt_meta.title or "#Shorts" in yt_meta.description)
        self.assertEqual(len(yt_meta.chapters), 2)
        self.assertEqual(yt_meta.chapters[0]["timecode"], "00:00")
        self.assertEqual(yt_meta.chapters[1]["timecode"], "00:05")

        ig_meta = PlatformMetadataGenerator.generate_metadata(proj, scenes, ch, INSTAGRAM_REELS_PROFILE)
        self.assertEqual(ig_meta.platform, ExportPlatform.INSTAGRAM_REELS.value)
        self.assertLessEqual(len(ig_meta.description), 2200)

        tt_meta = PlatformMetadataGenerator.generate_metadata(proj, scenes, ch, TIKTOK_PROFILE)
        self.assertEqual(tt_meta.platform, ExportPlatform.TIKTOK.value)
        self.assertLessEqual(len(tt_meta.description), 2200)

    def test_07_atomic_package_assembly(self):
        """Verify staging, SHA-256 manifest, clean relative ZIP archive, and error cleanup."""
        assembler = ExportPackageAssembler(base_storage_dir=self.base_path)

        vid_file = self.base_path / "source_video.mp4"
        vid_file.write_bytes(b"Simulated MP4 bytes for packaging")

        thumb_file = self.base_path / "source_thumb.png"
        thumb_file.write_bytes(b"Simulated PNG bytes for thumbnail")

        meta_dict = {"title": "Test Title", "description": "Test Desc"}
        preflight_dict = {"passed": True, "total_assets": 1}
        quality_dict = {"passed": True, "measurements": {"lufs": -14.1}}

        pkg_res = assembler.assemble_package(
            channel_id="ch_demo",
            project_id="proj_01",
            package_id="pkg_test_01",
            profile_id="youtube_shorts",
            platform="youtube_shorts",
            video_source_path=vid_file,
            thumbnail_source_path=thumb_file,
            metadata_dict=meta_dict,
            preflight_report_dict=preflight_dict,
            quality_report_dict=quality_dict,
        )

        self.assertTrue(Path(pkg_res["package_dir"]).is_dir())
        self.assertTrue(Path(pkg_res["zip_path"]).is_file())
        self.assertGreater(pkg_res["zip_size_bytes"], 0)
        self.assertEqual(len(pkg_res["zip_sha256"]), 64)

        manifest_p = Path(pkg_res["manifest_path"])
        self.assertTrue(manifest_p.is_file())
        with open(manifest_p, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)
        self.assertEqual(manifest_data["package_id"], "pkg_test_01")
        self.assertGreaterEqual(len(manifest_data["files"]), 5)

        with zipfile.ZipFile(pkg_res["zip_path"], "r") as zf:
            for name in zf.namelist():
                self.assertTrue(name.startswith("pkg_test_01/"))
                self.assertFalse(name.startswith("/"))
                self.assertNotIn("\\", name)
                self.assertFalse(any(bad in name for bad in [".git", ".db", ".tmp"]))

        # Cleanup test
        bad_vid = self.base_path / "non_existent.mp4"
        with self.assertRaises(ValidationException):
            assembler.assemble_package(
                channel_id="ch_demo",
                project_id="proj_01",
                package_id="pkg_fail_test",
                profile_id="youtube_shorts",
                platform="youtube_shorts",
                video_source_path=bad_vid,
                thumbnail_source_path=thumb_file,
                metadata_dict={},
                preflight_report_dict={},
                quality_report_dict={},
            )
        staging_p = self.base_path / "channels" / "ch_demo" / "exports" / "packages" / "pkg_fail_test_staging"
        self.assertFalse(staging_p.exists())

    def test_08_webui_export_routes_and_download(self):
        """Test GET /export, POST /export/preflight, GET /export/{id}, and ZIP download."""
        db_file = self.base_path / "web_export_test.db"
        db_mgr = DatabaseManager(db_path=db_file)
        MigrationManager(db_mgr).run_migrations()

        storage = StorageService(local_base_dir=self.base_path / "storage")
        app = create_app(db_manager=db_mgr, storage_service=storage)
        app.state.service_container.render_service.stop_worker_pool()
        client = TestClient(app)

        container: ServiceContainer = app.state.service_container

        container.channel_service.create_channel(
            ChannelCreate(id="ch_web", name="Web Test Channel", handle="@web_test", niche="demo")
        )
        container.project_service.create_project(
            "ch_web",
            ProjectCreate(id="proj_web", channel_id="ch_web", title="Web Project 01", description="Description")
        )

        # GET /export
        resp = client.get("/export?channel_id=ch_web")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("YouTube Shorts", resp.text)

        # POST /export/preflight
        resp_pf = client.post("/export/preflight", data={"channel_id": "ch_web", "project_id": "proj_web"})
        self.assertEqual(resp_pf.status_code, 200)
        pf_json = resp_pf.json()
        self.assertIn("passed", pf_json)
        self.assertEqual(pf_json["project_id"], "proj_web")

        # Create package and test inspect & download
        dummy_vid = self.base_path / "web_vid.mp4"
        dummy_vid.write_bytes(b"Simulated MP4 bytes for web route testing")
        dummy_thumb = self.base_path / "web_thumb.png"
        dummy_thumb.write_bytes(b"Simulated PNG bytes for web route testing")

        pkg = container.export_service.create_export_package(
            channel_id="ch_web",
            project_id="proj_web",
            profile_id="youtube_shorts",
            video_file_path=dummy_vid,
            thumbnail_file_path=dummy_thumb,
            enforce_license_gate=False,
            enforce_quality_gate=False,
        )
        self.assertIsNotNone(pkg)
        self.assertEqual(pkg.status, "quality_failed")

        resp_detail = client.get(f"/export/{pkg.id}")
        self.assertEqual(resp_detail.status_code, 200)
        self.assertIn(pkg.id, resp_detail.text)

        # Verify failed quality package download is forbidden (403)
        resp_dl_fail = client.get(f"/export/{pkg.id}/download")
        self.assertEqual(resp_dl_fail.status_code, 403)

        # Update package to completed to verify approved release download (200)
        container.export_service._export_repo.update_package(pkg.id, status="completed")
        resp_dl = client.get(f"/export/{pkg.id}/download")
        self.assertEqual(resp_dl.status_code, 200)
        self.assertEqual(resp_dl.headers["content-type"], "application/zip")
        self.assertGreater(len(resp_dl.content), 0)

        # Stop background worker threads cleanly
        try:
            container.render_service.stop_worker_pool()
        except Exception:
            pass


if __name__ == "__main__":
    unittest.main()
