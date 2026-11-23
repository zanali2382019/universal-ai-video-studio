"""Test Suite for Task 3.15b: Studio GUI - Render Queue, Batch Production, Video Preview & Monitoring.

Verifies:
1. Architectural compliance: AST scan ensuring ZERO sqlite3 imports in src/web/.
2. Page rendering via FastAPI TestClient (Render Queue, Batch, Preview, Monitor).
3. Render dispatch and lifecycle actions (Preview vs Export with ADR-008 license gate).
4. Batch production workflow with error isolation.
5. HTTP Range Requests (200, 206 Partial Content, 416 Range Not Satisfiable, path traversal security).
6. Real-time monitoring and diagnostic status endpoints.
"""

import ast
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.core.config import get_settings
from src.core.constants import TaskStatus, TaskType, VerificationStatus, LegalBasis
from src.database.connection import DatabaseManager
from src.database.migrations import MigrationManager
from src.database.models import ChannelModel, ProjectStatus, AssetLicenseUpdate
from src.storage.service import StorageService
from src.web.app import create_app
from src.web.dependencies import reset_service_container, init_service_container


class TestStudioWebUIRenderArchitecture(unittest.TestCase):
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
                        offending_files.append(
                            (py_file.name, f"from sqlite3 import {', '.join(a.name for a in node.names)}")
                        )

        self.assertEqual(
            offending_files,
            [],
            f"Direct sqlite3 usage is strictly prohibited in src/web/. Violations: {offending_files}",
        )


class TestRenderQueueAndBatchGUIIntegration(unittest.TestCase):
    """Integration test suite for Task 3.15b WebUI endpoints and domain workflows."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="studio_test_task315b_")
        cls.db_path = Path(cls.temp_dir) / "test_studio.db"
        cls.storage_dir = Path(cls.temp_dir) / "storage"
        cls.storage_dir.mkdir(parents=True, exist_ok=True)

        os.environ["STUDIO_DATA_DIR"] = str(cls.temp_dir)
        os.environ["STUDIO_DB_PATH"] = str(cls.db_path)

        cls.db_manager = DatabaseManager(db_path=cls.db_path)
        cls.migrator = MigrationManager(cls.db_manager)
        cls.migrator.run_migrations()

        cls.storage_service = StorageService(local_base_dir=cls.storage_dir)

        cls.app = create_app(db_manager=cls.db_manager, storage_service=cls.storage_service)
        cls.client = TestClient(cls.app)
        cls.services = cls.app.state.service_container

        # Disable auto-worker daemon for deterministic testing
        cls.services.render_service.stop_worker_pool()

        # Seed sample test channel
        cls.channel = cls.services.channel_service.create_channel({
            "id": "ch_render_test",
            "name": "Render Test Channel",
            "handle": "@rendertest",
            "niche": "Testing",
        })

        # Seed sample projects
        cls.proj1 = cls.services.project_service.create_project(
            cls.channel.id,
            {"id": "proj_render_01", "title": "Render Project 1", "aspect_ratio": "9:16"}
        )
        cls.proj2 = cls.services.project_service.create_project(
            cls.channel.id,
            {"id": "proj_render_02", "title": "Render Project 2", "aspect_ratio": "16:9"}
        )

        # Seed a scene for proj1
        cls.scene1 = cls.services.scene_service.create_scene(
            cls.channel.id,
            cls.proj1.id,
            {
                "id": "scene_render_01",
                "prompt_text": "A beautiful cinematic sunrise over mountains",
                "sequence_number": 1,
                "duration_seconds": 3.0,
            }
        )

    @classmethod
    def tearDownClass(cls):
        try:
            cls.services.render_service.stop_worker_pool()
        except Exception:
            pass
        reset_service_container()
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_01_page_routes_rendering(self):
        """Verify all Task 3.15b views render with HTTP 200."""
        routes = ["/render-queue", "/batch", "/preview", "/monitor"]
        for r in routes:
            resp = self.client.get(r)
            self.assertEqual(resp.status_code, 200, f"Failed rendering route {r}")
            self.assertIn("text/html", resp.headers["content-type"])

    def test_02_render_dispatch_preview_mode(self):
        """Verify dispatching a render task in preview mode succeeds and is queued."""
        form_data = {
            "channel_id": self.channel.id,
            "project_id": self.proj1.id,
            "mode": "preview",
            "aspect_ratio": "9:16",
            "aspect_mode": "cover",
            "transition": "fade",
            "burn_subtitles": "true",
        }
        resp = self.client.post("/render-queue/dispatch", data=form_data, headers={"accept": "application/json"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("task_id", data)

        task_id = data["task_id"]
        # Verify status in service
        progress = self.services.render_service.get_render_progress(task_id)
        self.assertIn(progress["status"], ("queued", "running"))
        self.assertIn(progress["phase"], ("queued", "rendering", "unknown"))

    def test_03_render_dispatch_export_mode_adr008_gate(self):
        """Verify export mode enforces ADR-008 commercial license verification gate."""
        # Project 1 has scene1 but no verified commercial asset -> export must fail
        form_data = {
            "channel_id": self.channel.id,
            "project_id": self.proj1.id,
            "mode": "export",
            "aspect_ratio": "9:16",
        }
        resp = self.client.post("/render-queue/dispatch", data=form_data, headers={"accept": "application/json"})
        # Should be rejected with 400 and ADR-008 gate error
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertEqual(data["status"], "error")
        self.assertTrue("ADR-008" in data["error"] or "License" in data["error"])

    def test_04_render_task_lifecycle_cancel_and_retry(self):
        """Verify task cancellation and retry lifecycles."""
        # Create a new preview task
        task = self.services.render_service.submit_render(
            channel_id=self.channel.id,
            project_id=self.proj1.id,
            mode="preview",
        )
        self.assertEqual(task.status, TaskStatus.QUEUED)

        # Cancel the task via route
        cancel_resp = self.client.post(f"/render-queue/{task.id}/cancel", follow_redirects=False)
        self.assertEqual(cancel_resp.status_code, 303)

        updated = self.services.task_service.get_task(task.id)
        self.assertEqual(updated.status, TaskStatus.CANCELLED)

        # Retry the cancelled task
        retry_resp = self.client.post(f"/render-queue/{task.id}/retry", follow_redirects=False)
        self.assertEqual(retry_resp.status_code, 303)

    def test_05_batch_production_workflow_and_isolation(self):
        """Verify batch creation, tracking, and item failure isolation."""
        form_data = {
            "channel_id": self.channel.id,
            "name": "Test Multi-Episode Batch",
            "project_ids": [self.proj1.id, self.proj2.id],
            "mode": "preview",
            "aspect_ratio": "9:16",
        }
        resp = self.client.post("/batch/create", data=form_data, headers={"accept": "application/json"})
        self.assertEqual(resp.status_code, 200)
        batch_data = resp.json()
        self.assertIn("id", batch_data)
        self.assertEqual(batch_data["total_items"], 2)
        self.assertEqual(len(batch_data["tasks"]), 2)

        batch_id = batch_data["id"]

        # View batch detail page
        detail_resp = self.client.get(f"/batch/{batch_id}")
        self.assertEqual(detail_resp.status_code, 200)
        self.assertIn("Test Multi-Episode Batch", detail_resp.text)

        # Verify error isolation: artificially fail one task
        task1_id = batch_data["tasks"][0]["task_id"]
        self.services.task_service.cancel_task(task1_id, reason="Simulated failure for isolation test")

        status = self.services.batch_service.get_batch_status(batch_id)
        # One cancelled, one queued -> batch should still track both
        self.assertEqual(status["total_items"], 2)
        self.assertEqual(status["cancelled_items"], 1)
        self.assertEqual(status["queued_items"], 1)

    def test_06_http_range_streaming_engine(self):
        """Verify HTTP 200, 206 Partial Content, 416 Range Not Satisfiable, and path security."""
        # Create a dummy video file inside channels workspace
        test_video_path = get_settings().channels_root / self.channel.id / "projects" / self.proj1.id / "dummy_test.mp4"
        test_video_path.parent.mkdir(parents=True, exist_ok=True)

        sample_content = b"TEST_VIDEO_STREAMING_CONTENT_BUFFER_0123456789" * 100  # 4500 bytes
        test_video_path.write_bytes(sample_content)
        file_len = len(sample_content)

        stream_url = f"/preview/stream?type=project&channel_id={self.channel.id}&project_id={self.proj1.id}&filename=dummy_test.mp4"

        # 1. Full Stream (No Range header) -> HTTP 200
        r_full = self.client.get(stream_url)
        self.assertEqual(r_full.status_code, 200)
        self.assertEqual(r_full.headers["accept-ranges"], "bytes")
        self.assertEqual(int(r_full.headers["content-length"]), file_len)
        self.assertEqual(r_full.content, sample_content)

        # 2. Range Request (bytes=0-99) -> HTTP 206 Partial Content
        r_partial = self.client.get(stream_url, headers={"Range": "bytes=0-99"})
        self.assertEqual(r_partial.status_code, 206)
        self.assertEqual(r_partial.headers["accept-ranges"], "bytes")
        self.assertEqual(r_partial.headers["content-range"], f"bytes 0-99/{file_len}")
        self.assertEqual(int(r_partial.headers["content-length"]), 100)
        self.assertEqual(r_partial.content, sample_content[0:100])

        # 3. Suffix Range (last 50 bytes) -> HTTP 206
        r_suffix = self.client.get(stream_url, headers={"Range": "bytes=-50"})
        self.assertEqual(r_suffix.status_code, 206)
        self.assertEqual(int(r_suffix.headers["content-length"]), 50)
        self.assertEqual(r_suffix.content, sample_content[-50:])

        # 4. Unsatisfiable Range (bytes=99999-999999) -> HTTP 416
        r_unsat = self.client.get(stream_url, headers={"Range": "bytes=999999-"})
        self.assertEqual(r_unsat.status_code, 416)
        self.assertEqual(r_unsat.headers["content-range"], f"bytes */{file_len}")

        # 5. Path Traversal Rejection
        r_traversal = self.client.get(
            f"/preview/stream?type=project&channel_id={self.channel.id}&project_id={self.proj1.id}&filename=../../../../sensitive.txt"
        )
        self.assertIn(r_traversal.status_code, (403, 404))

    def test_07_monitor_status_json_endpoint(self):
        """Verify real-time JSON status endpoint returns telemetry without hallucinated audio stats."""
        task = self.services.render_service.submit_render(
            channel_id=self.channel.id,
            project_id=self.proj1.id,
            mode="preview",
        )
        resp = self.client.get(f"/monitor/{task.id}/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["task_id"], task.id)
        self.assertEqual(data["status"], "queued")
        self.assertEqual(data["phase"], "queued")
        # Audio stats must be None when not measured
        self.assertIsNone(data["lufs"])
        self.assertIsNone(data["true_peak"])


if __name__ == "__main__":
    unittest.main()
