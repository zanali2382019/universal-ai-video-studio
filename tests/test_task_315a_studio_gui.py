"""Test Suite for Task 3.15a: Studio GUI & Project Management (Local WebUI).

Verifies:
1. Architectural compliance: AST scan ensuring ZERO sqlite3 imports in src/web/.
2. Page rendering via FastAPI TestClient (Dashboard, Channels, Projects, Characters, Assets, Storyboard, Outbound, Inbound).
3. Channel creation and profile updates via WebUI.
4. Project creation and scene prompt editing with version history.
5. Flow Outbound prompt generation, copying cues, and JSON/Markdown export.
6. Flow Inbound dry-run preview and active-take promotion.
7. End-to-end data persistence in isolated temporary STUDIO_DATA_DIR.
"""

import ast
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.core.config import get_settings
from src.database.connection import DatabaseManager
from src.database.migrations import MigrationManager
from src.database.models import MediaAssetCreate, AssetType
from src.storage.service import StorageService
from src.web.app import create_app
from src.web.dependencies import reset_service_container, init_service_container


class TestStudioWebUIArchitecture(unittest.TestCase):
    """Architectural compliance check: Guarantee ZERO sqlite3 imports in src/web/."""

    def test_zero_sqlite3_in_web_package(self):
        web_dir = Path(__file__).resolve().parent.parent / "src" / "web"
        self.assertTrue(web_dir.exists(), f"Web directory does not exist: {web_dir}")

        offending_files = []

        for py_file in web_dir.rglob("*.py"):
            with open(py_file, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=str(py_file))

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "sqlite3":
                            offending_files.append((py_file.name, "import sqlite3"))
                elif isinstance(node, ast.ImportFrom):
                    if node.module == "sqlite3":
                        offending_files.append((py_file.name, f"from sqlite3 import {', '.join(a.name for a in node.names)}"))

        self.assertEqual(
            offending_files,
            [],
            f"Direct sqlite3 usage is strictly prohibited in src/web/. Violations: {offending_files}",
        )


class TestStudioWebUIIntegration(unittest.TestCase):
    """Integration test suite executing WebUI routes using isolated temporary storage."""

    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp(prefix="studio_webui_test_")
        cls.orig_data_dir = os.environ.get("STUDIO_DATA_DIR")
        os.environ["STUDIO_DATA_DIR"] = cls.test_dir
        get_settings(reload=True)

        # Initialize fresh DB with migrations and Storage
        db_path = Path(cls.test_dir) / "studio_test.db"
        cls.db_manager = DatabaseManager(db_path=db_path)
        cls.migration_mgr = MigrationManager(cls.db_manager)
        cls.migration_mgr.run_migrations()
        cls.storage = StorageService(local_base_dir=Path(cls.test_dir))

        # Build FastAPI application
        reset_service_container()
        cls.app = create_app(db_manager=cls.db_manager, storage_service=cls.storage)
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        reset_service_container()
        if cls.orig_data_dir is not None:
            os.environ["STUDIO_DATA_DIR"] = cls.orig_data_dir
        else:
            os.environ.pop("STUDIO_DATA_DIR", None)

        if os.path.exists(cls.test_dir):
            shutil.rmtree(cls.test_dir, ignore_errors=True)

    def test_01_health_and_root_redirect(self):
        """Test health endpoint and root redirect to /dashboard."""
        health_resp = self.client.get("/health")
        self.assertEqual(health_resp.status_code, 200)
        self.assertEqual(health_resp.json()["status"], "healthy")

        root_resp = self.client.get("/", follow_redirects=False)
        self.assertEqual(root_resp.status_code, 303)
        self.assertEqual(root_resp.headers["location"], "/dashboard")

    def test_02_dashboard_renders(self):
        """Test /dashboard loads successfully with summary metrics."""
        resp = self.client.get("/dashboard")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Universal AI Video Studio", resp.text)
        self.assertIn("Total Channels", resp.text)
        self.assertIn("Total Projects", resp.text)

    def test_03_channels_lifecycle(self):
        """Test listing, creating, and modifying channels via WebUI."""
        # 1. View empty channels
        list_resp = self.client.get("/channels")
        self.assertEqual(list_resp.status_code, 200)

        # 2. Create Channel via POST
        create_resp = self.client.post(
            "/channels/create",
            data={
                "channel_id": "dreammatter",
                "name": "DreamMatter Channel",
                "description": "Magical childhood stories channel",
            },
            follow_redirects=False,
        )
        self.assertEqual(create_resp.status_code, 303)
        self.assertEqual(create_resp.headers["location"], "/channels/dreammatter")

        # 3. View Channel Detail
        detail_resp = self.client.get("/channels/dreammatter")
        self.assertEqual(detail_resp.status_code, 200)
        self.assertIn("DreamMatter Channel", detail_resp.text)
        self.assertIn("dreammatter", detail_resp.text)

        # 4. Edit Channel Profile
        edit_resp = self.client.post(
            "/channels/dreammatter/edit",
            data={
                "brand_description": "Updated brand identity for DreamMatter",
                "visual_style": "Pixar-like vibrant 3D cartoon style",
                "color_primary": "#1E3A8A",
                "color_secondary": "#3B82F6",
                "color_accent": "#F59E0B",
                "color_background": "#0F172A",
                "aspect_ratio": "9:16",
                "language": "ar",
                "target_duration_sec": 4.5,
                "branding_rules": "Rule 1: Always keep character colors consistent\nRule 2: High saturation",
            },
            follow_redirects=False,
        )
        self.assertEqual(edit_resp.status_code, 303)

        # 5. Verify Profile saved
        verify_resp = self.client.get("/channels/dreammatter")
        self.assertEqual(verify_resp.status_code, 200)
        self.assertIn("Pixar-like vibrant 3D cartoon style", verify_resp.text)
        self.assertIn("#1E3A8A", verify_resp.text)

    def test_04_project_and_storyboard_management(self):
        """Test creating project, viewing scenes, and reordering in storyboard."""
        # 1. Create Project
        create_proj_resp = self.client.post(
            "/projects/create",
            data={
                "channel_id": "dreammatter",
                "name": "Episode 04 - The Crystal Cave",
                "target_aspect_ratio": "9:16",
                "target_duration": 15.0,
                "description": "Tommy explores the mysterious glowing crystal cave.",
            },
            follow_redirects=False,
        )
        self.assertEqual(create_proj_resp.status_code, 303)
        redirect_url = create_proj_resp.headers["location"]
        self.assertTrue(redirect_url.startswith("/projects/dreammatter/"))
        project_id = redirect_url.split("/")[-1]

        # 2. Add sample scenes using SceneService via container
        container = self.app.state.service_container
        s1 = container.scene_service.create_scene(
            channel_id="dreammatter",
            project_id=project_id,
            scene_input={
                "id": "sc_ep04_01",
                "sequence_number": 1,
                "title": "Scene 1 Entrance",
                "narration_text": "Tommy reaches the glowing cave entrance.",
                "visual_prompt": "Tommy in red hoodie standing in front of crystal cave entrance",
                "duration_seconds": 5.0,
            },
        )
        s2 = container.scene_service.create_scene(
            channel_id="dreammatter",
            project_id=project_id,
            scene_input={
                "id": "sc_ep04_02",
                "sequence_number": 2,
                "title": "Scene 2 Discovery",
                "narration_text": "The crystals light up with purple glow.",
                "visual_prompt": "Magical crystals glowing brightly in dark cavern",
                "duration_seconds": 5.0,
            },
        )

        # 3. View Project Detail
        proj_detail_resp = self.client.get(f"/projects/dreammatter/{project_id}")
        self.assertEqual(proj_detail_resp.status_code, 200)
        self.assertIn("Episode 04 - The Crystal Cave", proj_detail_resp.text)
        self.assertIn(s1.id, proj_detail_resp.text)
        self.assertIn(s2.id, proj_detail_resp.text)

        # 4. View Storyboard
        sb_resp = self.client.get(f"/storyboard/dreammatter/{project_id}")
        self.assertEqual(sb_resp.status_code, 200)
        self.assertIn("Tommy reaches the glowing cave entrance", sb_resp.text)
        self.assertIn("The crystals light up with purple glow", sb_resp.text)

        # 5. Reorder Storyboard (move scene 2 up)
        reorder_resp = self.client.post(
            f"/storyboard/dreammatter/{project_id}/reorder",
            data={
                "scene_id": s2.id,
                "direction": "up",
            },
            follow_redirects=False,
        )
        self.assertEqual(reorder_resp.status_code, 303)

        # 6. Verify reordered scenes
        scenes_after = container.scene_service.list_scenes("dreammatter", project_id)
        self.assertEqual(scenes_after[0].id, s2.id)
        self.assertEqual(scenes_after[1].id, s1.id)

    def test_05_scene_prompt_edit_and_versioning(self):
        """Test editing visual prompt and verifying version history tracking."""
        container = self.app.state.service_container
        projects = container.project_service.list_projects(channel_id="dreammatter")
        project_id = projects[0].id
        scenes = container.scene_service.list_scenes("dreammatter", project_id)
        target_scene = scenes[0]

        # 1. GET Scene Edit page
        edit_get_resp = self.client.get(f"/scenes/dreammatter/{project_id}/{target_scene.id}")
        self.assertEqual(edit_get_resp.status_code, 200)
        self.assertIn("Prompt Version History", edit_get_resp.text)

        # 2. POST Scene Edit with new prompt and versioning reason
        updated_prompt = "Tommy with sparkling golden eyes examining huge blue crystal"
        edit_post_resp = self.client.post(
            f"/scenes/dreammatter/{project_id}/{target_scene.id}/edit_prompt",
            data={
                "script_text": "Updated narration script for Tommy.",
                "visual_prompt": updated_prompt,
                "character_id": "tommy",
                "outfit_id": "default",
                "target_duration_sec": 6.0,
                "modification_reason": "Improved cinematic lighting and details",
            },
            follow_redirects=False,
        )
        self.assertEqual(edit_post_resp.status_code, 303)

        # 3. Verify updated scene and prompt history
        verify_edit_resp = self.client.get(f"/scenes/dreammatter/{project_id}/{target_scene.id}")
        self.assertEqual(verify_edit_resp.status_code, 200)
        self.assertIn(updated_prompt, verify_edit_resp.text)

    def test_06_characters_and_character_bible(self):
        """Test /characters and /characters/{id} viewing Character Bible entries."""
        # 1. Characters list
        chars_resp = self.client.get("/characters")
        self.assertEqual(chars_resp.status_code, 200)
        self.assertIn("Character Bible", chars_resp.text)
        self.assertIn("Tommy", chars_resp.text)

        # 2. Tommy detail view
        tommy_resp = self.client.get("/characters/tommy")
        self.assertEqual(tommy_resp.status_code, 200)
        self.assertIn("Tommy", tommy_resp.text)
        self.assertIn("Outfit Profiles", tommy_resp.text)
        self.assertIn("Character Consistency", tommy_resp.text)

    def test_07_flow_outbound_generation_and_export(self):
        """Test Flow Outbound prompt generation, one-click copy display, and JSON/MD export."""
        container = self.app.state.service_container
        projects = container.project_service.list_projects(channel_id="dreammatter")
        project_id = projects[0].id

        # 1. View Outbound page
        outbound_resp = self.client.get(f"/flow/outbound/dreammatter/{project_id}")
        self.assertEqual(outbound_resp.status_code, 200)
        self.assertIn("Google Flow Outbound Package", outbound_resp.text)

        # 2. Trigger generation
        gen_resp = self.client.post(
            f"/flow/outbound/dreammatter/{project_id}/generate",
            follow_redirects=False,
        )
        self.assertEqual(gen_resp.status_code, 303)

        # 3. View Outbound page with generated package
        outbound_view_resp = self.client.get(f"/flow/outbound/dreammatter/{project_id}")
        self.assertEqual(outbound_view_resp.status_code, 200)
        self.assertIn("Master Style Prompt", outbound_view_resp.text)
        self.assertIn("copyToClipboard", outbound_view_resp.text)

        # 4. Export JSON
        json_export_resp = self.client.get(f"/flow/outbound/dreammatter/{project_id}/export/json")
        self.assertEqual(json_export_resp.status_code, 200)
        self.assertEqual(json_export_resp.headers["content-type"], "application/json")
        self.assertIn("dreammatter", json_export_resp.text)

        # 5. Export Markdown
        md_export_resp = self.client.get(f"/flow/outbound/dreammatter/{project_id}/export/markdown")
        self.assertEqual(md_export_resp.status_code, 200)
        self.assertIn("text/markdown", md_export_resp.headers["content-type"])
        self.assertIn("Google Flow Production Package", md_export_resp.text)

    def test_08_flow_inbound_dry_run_and_active_take_switch(self):
        """Test Flow Inbound dry run and active take switcher."""
        container = self.app.state.service_container
        projects = container.project_service.list_projects(channel_id="dreammatter")
        project_id = projects[0].id
        scenes = container.scene_service.list_scenes("dreammatter", project_id)
        scene1 = scenes[0]

        # 1. View Inbound page
        inbound_resp = self.client.get(f"/flow/inbound/dreammatter/{project_id}")
        self.assertEqual(inbound_resp.status_code, 200)
        self.assertIn("Google Flow Inbound", inbound_resp.text)

        # 2. Create a temporary folder with a mock video file for scene1
        mock_import_dir = Path(self.test_dir) / "mock_flow_drops"
        mock_import_dir.mkdir(parents=True, exist_ok=True)
        mock_file = mock_import_dir / f"scene_{scene1.sequence_number}_take_01.mp4"
        mock_file.write_bytes(b"dummy mp4 media stream")

        # 3. Test Dry Run (Dry Run: no changes committed to DB)
        dry_run_resp = self.client.post(
            f"/flow/inbound/dreammatter/{project_id}/import",
            data={
                "import_path": str(mock_import_dir),
                "dry_run": "true",
            },
        )
        self.assertEqual(dry_run_resp.status_code, 200)
        self.assertIn("Dry Run (Preview)", dry_run_resp.text)

        # 4. Register a take directly into inbound_repo for testing active-take switching via WebUI
        container.asset_repo.create(
            MediaAssetCreate(
                id="ast_mock_01",
                channel_id="dreammatter",
                project_id=project_id,
                scene_id=scene1.id,
                file_name="scene_1_take_01.mp4",
                file_path=str(mock_file),
                file_sha256="abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
                file_size_bytes=1024,
                mime_type="video/mp4",
                asset_type=AssetType.VIDEO,
            )
        )
        container.asset_repo.create(
            MediaAssetCreate(
                id="ast_mock_02",
                channel_id="dreammatter",
                project_id=project_id,
                scene_id=scene1.id,
                file_name="scene_1_take_02.mp4",
                file_path=str(mock_file),
                file_sha256="123456abcdef7890123456abcdef7890123456abcdef7890123456abcdef7890",
                file_size_bytes=2048,
                mime_type="video/mp4",
                asset_type=AssetType.VIDEO,
            )
        )
        t1 = container.flow_inbound_repo.create_take(
            take_id="tk_ep04_01_v1",
            channel_id="dreammatter",
            project_id=project_id,
            scene_id=scene1.id,
            prompt_version=1,
            take_number=1,
            asset_id="ast_mock_01",
            asset_role="video",
            file_sha256="abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
            source_file_name="scene_1_take_01.mp4",
            storage_key="media/video/take1.mp4",
            is_active=True,
        )
        t2 = container.flow_inbound_repo.create_take(
            take_id="tk_ep04_01_v2",
            channel_id="dreammatter",
            project_id=project_id,
            scene_id=scene1.id,
            prompt_version=1,
            take_number=2,
            asset_id="ast_mock_02",
            asset_role="video",
            file_sha256="123456abcdef7890123456abcdef7890123456abcdef7890123456abcdef7890",
            source_file_name="scene_1_take_02.mp4",
            storage_key="media/video/take2.mp4",
            is_active=False,
        )

        # 5. Switch active take to take 2 via WebUI POST
        activate_resp = self.client.post(
            f"/flow/inbound/dreammatter/{project_id}/activate_take",
            data={
                "scene_id": scene1.id,
                "take_id": str(t2["id"]),
            },
            follow_redirects=False,
        )
        self.assertEqual(activate_resp.status_code, 303)

        # 6. Verify take 2 is now active in database
        active_take = container.flow_inbound_repo.get_active_take(project_id, scene1.id, "video")
        self.assertIsNotNone(active_take)
        self.assertEqual(active_take["take_number"], 2)

    def test_09_assets_browser(self):
        """Test /assets browser page rendering."""
        resp = self.client.get("/assets")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Media Asset Management (MAM)", resp.text)

    def test_10_port_configuration_and_discovery(self):
        """Verify port 8050 default, STUDIO_WEB_PORT env override, and range discovery."""
        from src.web.server import get_default_port, find_available_port, DEFAULT_PORT, DEFAULT_MAX_PORT

        # Default fallback
        orig_env = os.environ.get("STUDIO_WEB_PORT")
        try:
            if "STUDIO_WEB_PORT" in os.environ:
                del os.environ["STUDIO_WEB_PORT"]
            self.assertEqual(get_default_port(), 8050)
            self.assertEqual(DEFAULT_PORT, 8050)
            self.assertEqual(DEFAULT_MAX_PORT, 8090)

            # Env override
            os.environ["STUDIO_WEB_PORT"] = "8077"
            self.assertEqual(get_default_port(), 8077)

            # Invalid env fallback
            os.environ["STUDIO_WEB_PORT"] = "not_a_number"
            self.assertEqual(get_default_port(), 8050)

            # find_available_port discovery
            port = find_available_port(host="127.0.0.1", start_port=8050, max_port=8090)
            self.assertGreaterEqual(port, 8050)
            self.assertLessEqual(port, 8090)
        finally:
            if orig_env is not None:
                os.environ["STUDIO_WEB_PORT"] = orig_env
            elif "STUDIO_WEB_PORT" in os.environ:
                del os.environ["STUDIO_WEB_PORT"]

    def test_11_arabic_project_slug_and_scene_creation_ui(self):
        """Test Arabic project ID slug generation, Scene creation via POST in Storyboard, and scene deletion."""
        import re
        container = self.app.state.service_container

        # 1. Create a project with pure Arabic title -> slug must NOT have repeating underscores
        resp_ar = self.client.post(
            "/projects/create",
            data={
                "channel_id": "dreammatter",
                "name": "مغامرة تومي في الغابة السحرية",
                "aspect_ratio": "9:16",
                "target_duration_sec": 30.0,
            },
            follow_redirects=False,
        )
        self.assertEqual(resp_ar.status_code, 303)
        ar_loc = resp_ar.headers["location"]
        ar_proj_id = ar_loc.split("/")[-1]
        self.assertTrue(
            re.match(r"^prj_[a-f0-9]{6}$", ar_proj_id),
            f"Pure Arabic project title should generate clean 'prj_<hex6>' slug, got: {ar_proj_id}",
        )
        self.assertNotIn("__", ar_proj_id)

        # 2. Create a project with explicit custom project_id
        resp_custom = self.client.post(
            "/projects/create",
            data={
                "channel_id": "dreammatter",
                "name": "Custom ID Project",
                "project_id": "prj_explicit_custom_001",
                "aspect_ratio": "16:9",
            },
            follow_redirects=False,
        )
        self.assertEqual(resp_custom.status_code, 303)
        self.assertEqual(resp_custom.headers["location"], "/projects/dreammatter/prj_explicit_custom_001")

        # 3. Test Storyboard GET with the Arabic project
        sb_resp = self.client.get(f"/storyboard/dreammatter/{ar_proj_id}")
        self.assertEqual(sb_resp.status_code, 200)
        self.assertIn("addSceneModal", sb_resp.text)
        self.assertIn("إضافة مشهد جديد", sb_resp.text)

        # 4. POST create scene in Storyboard
        sc_resp = self.client.post(
            f"/storyboard/dreammatter/{ar_proj_id}/scenes/create",
            data={
                "title": "المشهد الأول: الغابة السحرية",
                "duration_sec": "6.5",
                "visual_prompt": "Tommy walking in a glowing enchanted forest with bioluminescent mushrooms.",
                "narration_text": "كان تومي يتجول في الغابة الساحرة بحثاً عن الكنز.",
            },
            follow_redirects=False,
        )
        self.assertEqual(sc_resp.status_code, 303)
        self.assertEqual(sc_resp.headers["location"], f"/storyboard/dreammatter/{ar_proj_id}")

        # Verify scene was actually persisted in repository via SceneService
        scenes = container.scene_service.list_scenes("dreammatter", ar_proj_id)
        self.assertEqual(len(scenes), 1)
        created_sc = scenes[0]
        self.assertEqual(created_sc.sequence_number, 1)
        self.assertEqual(created_sc.duration_seconds, 6.5)
        self.assertEqual(created_sc.title, "المشهد الأول: الغابة السحرية")
        self.assertEqual(created_sc.visual_prompt, "Tommy walking in a glowing enchanted forest with bioluminescent mushrooms.")
        self.assertEqual(created_sc.narration_text, "كان تومي يتجول في الغابة الساحرة بحثاً عن الكنز.")

        # 5. POST delete scene
        del_resp = self.client.post(
            f"/storyboard/dreammatter/{ar_proj_id}/scenes/{created_sc.id}/delete",
            follow_redirects=False,
        )
        self.assertEqual(del_resp.status_code, 303)
        self.assertEqual(del_resp.headers["location"], f"/storyboard/dreammatter/{ar_proj_id}")
        remaining_scenes = container.scene_service.list_scenes("dreammatter", ar_proj_id)
        self.assertEqual(len(remaining_scenes), 0)

    def test_12_owner_license_approval_and_preview_support(self):
        """Verify OBS-18:
        1. PREVIEW render succeeds when project has an asset with 'pending_review' license status.
        2. EXPORT render fails before approval (ADR-008 strict enforcement).
        3. POST /assets/{asset_id}/verify-license approves license to 'verified'.
        4. EXPORT render succeeds after approval.
        """
        from src.core.constants import VerificationStatus, LegalBasis
        from src.core.exceptions import LicenseVerificationException

        container = self.app.state.service_container
        channel_id = "dreammatter"
        project_id = "prj_obs18_test"

        # Create test project via WebUI POST
        create_resp = self.client.post(
            "/projects/create",
            data={
                "channel_id": channel_id,
                "project_id": project_id,
                "name": "OBS-18 License Approval Test Project",
                "aspect_ratio": "9:16",
            },
            follow_redirects=False,
        )
        self.assertEqual(create_resp.status_code, 303)

        # Create dummy media file
        dummy_file = Path(self.test_dir) / "obs18_video.mp4"
        dummy_file.write_bytes(b"dummy mp4 data for license testing")
        dummy_sha256 = "11223344556677889900aabbccddeeff11223344556677889900aabbccddeeff"

        # Create scene
        sc = container.scene_service.create_scene(
            channel_id=channel_id,
            project_id=project_id,
            scene_input={
                "id": "sc_obs18_01",
                "sequence_number": 1,
                "title": "OBS-18 Scene",
                "duration_seconds": 4.0,
                "visual_prompt": "Crystal river at sunset",
                "metadata": {"asset_id": "ast_obs18_pending"},
            },
        )

        # Register MAM asset
        container.asset_repo.create(
            MediaAssetCreate(
                id="ast_obs18_pending",
                channel_id=channel_id,
                project_id=project_id,
                scene_id=sc.id,
                file_name="obs18_video.mp4",
                file_path=str(dummy_file),
                file_sha256=dummy_sha256,
                file_size_bytes=len(dummy_file.read_bytes()),
                mime_type="video/mp4",
                asset_type=AssetType.VIDEO,
            )
        )

        # Register license in pending_review status (default)
        lic = container.license_service.register_license(
            channel_id=channel_id,
            license_input={
                "asset_id": "ast_obs18_pending",
                "legal_basis": LegalBasis.EXPLICIT_USER_OVERRIDE_EXCEPTION,
                "source_uri": str(dummy_file),
                "notes": "Initial pending review take from Flow Inbound",
            },
        )
        self.assertEqual(lic.verification_status, VerificationStatus.PENDING_REVIEW)

        # 1. PREVIEW render succeeds with pending_review asset (OBS-18)
        preview_task = container.render_service.submit_render(
            channel_id=channel_id,
            project_id=project_id,
            mode="preview",
        )
        self.assertIsNotNone(preview_task)
        self.assertEqual(preview_task.project_id, project_id)

        # 2. EXPORT render fails before approval (ADR-008 strictly blocks pending_review)
        with self.assertRaises(LicenseVerificationException):
            container.render_service.submit_render(
                channel_id=channel_id,
                project_id=project_id,
                mode="export",
            )

        # 3. Check /assets GET page shows pending_review and Approve License button
        assets_get = self.client.get(f"/assets?channel_id={channel_id}")
        self.assertEqual(assets_get.status_code, 200)
        self.assertIn("ast_obs18_pending", assets_get.text)
        self.assertIn("Pending Review", assets_get.text)
        self.assertIn("/assets/ast_obs18_pending/verify-license", assets_get.text)

        # 4. POST /assets/{asset_id}/verify-license approves license
        post_resp = self.client.post(
            "/assets/ast_obs18_pending/verify-license",
            data={
                "channel_id": channel_id,
                "verified_by": "channel_owner",
                "notes": "Explicit owner license verification for OBS-18",
            },
            follow_redirects=False,
        )
        self.assertEqual(post_resp.status_code, 303)

        # Verify license is now VERIFIED
        updated_lic = container.license_service.get_license_by_asset(channel_id, "ast_obs18_pending")
        self.assertIsNotNone(updated_lic)
        self.assertEqual(updated_lic.verification_status, VerificationStatus.VERIFIED)
        self.assertTrue(updated_lic.commercial_use_allowed)

        # 5. EXPORT render now succeeds
        export_task = container.render_service.submit_render(
            channel_id=channel_id,
            project_id=project_id,
            mode="export",
        )
        self.assertIsNotNone(export_task)
        self.assertEqual(export_task.project_id, project_id)

        # 6. Verify /assets GET page now shows Verified badge and Approved indicator
        assets_after = self.client.get(f"/assets?channel_id={channel_id}")
        self.assertEqual(assets_after.status_code, 200)
        self.assertIn("Verified", assets_after.text)
        self.assertIn("Approved", assets_after.text)


if __name__ == "__main__":
    unittest.main()
