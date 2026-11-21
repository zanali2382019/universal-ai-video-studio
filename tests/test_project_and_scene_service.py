"""Unit and integration tests for ProjectService and SceneService (Task 3.4)."""
import unittest
import tempfile
import gc
from pathlib import Path

from src.database.connection import DatabaseManager
from src.database.migrations import MigrationManager
from src.database.repositories.channel_repository import ChannelRepository
from src.database.repositories.project_repository import ProjectRepository
from src.database.repositories.scene_repository import SceneRepository
from src.database.models import ChannelCreate, ProjectCreate, ProjectUpdate, SceneCreate, SceneUpdate
from src.services.project_service import ProjectService
from src.services.scene_service import SceneService
from src.core.config import get_settings
from src.core.constants import AspectRatio, ProjectStatus, SceneType, SceneStatus
from src.core.exceptions import (
    ProjectNotFoundException,
    SceneNotFoundException,
    ChannelNotFoundException,
    ValidationException,
)


class TestProjectAndSceneService(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root_path = Path(self.temp_dir.name)
        self.db_path = self.root_path / "test_studio.db"
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

        self.project_service = ProjectService(
            project_repository=self.project_repo,
            channel_repository=self.channel_repo,
        )
        self.scene_service = SceneService(
            scene_repository=self.scene_repo,
            project_repository=self.project_repo,
        )

        # Create two channels for isolation tests
        self.channel_repo.create(
            ChannelCreate(id="ch_alpha", name="Alpha Studio", handle="@alpha", niche="animation")
        )
        self.channel_repo.create(
            ChannelCreate(id="ch_beta", name="Beta Docs", handle="@beta", niche="documentary")
        )

    def tearDown(self):
        self.settings.channels_root = self._orig_channels_root
        gc.collect()
        self.temp_dir.cleanup()

    def test_project_lifecycle_and_channel_scoping(self):
        """Test full project CRUD, state transitions, archiving, and deletion."""
        # 1. Create project under Alpha
        project = self.project_service.create_project(
            "ch_alpha",
            {
                "id": "proj_ep01",
                "title": "Episode 1: The Beginning",
                "description": "Pilot episode",
                "aspect_ratio": AspectRatio.VERTICAL_9_16,
            },
        )
        self.assertEqual(project.id, "proj_ep01")
        self.assertEqual(project.channel_id, "ch_alpha")
        self.assertEqual(project.status, ProjectStatus.DRAFT)
        self.assertFalse(project.is_archived)

        # Verify physical directories under channels/ch_alpha/projects/proj_ep01/
        proj_dir = self.channels_dir / "ch_alpha" / "projects" / "proj_ep01"
        self.assertTrue((proj_dir / "working").exists())
        self.assertTrue((proj_dir / "output").exists())

        # 2. Channel Beta cannot access Alpha's project
        with self.assertRaises(ProjectNotFoundException):
            self.project_service.get_project("ch_beta", "proj_ep01")

        # 3. Update project
        updated = self.project_service.update_project(
            "ch_alpha",
            "proj_ep01",
            ProjectUpdate(title="Episode 1: Remastered", status=ProjectStatus.SCRIPTING),
        )
        self.assertEqual(updated.title, "Episode 1: Remastered")
        self.assertEqual(updated.status, ProjectStatus.SCRIPTING)

        # 4. Archive project
        archived = self.project_service.archive_project("ch_alpha", "proj_ep01")
        self.assertTrue(archived.is_archived)

        # Active list excludes archived by default
        active_projects = self.project_service.list_projects("ch_alpha", include_archived=False)
        self.assertEqual(len(active_projects), 0)

        all_projects = self.project_service.list_projects("ch_alpha", include_archived=True)
        self.assertEqual(len(all_projects), 1)

        # 5. Delete project
        deleted = self.project_service.delete_project("ch_alpha", "proj_ep01")
        self.assertTrue(deleted)
        self.assertFalse(proj_dir.exists())

    def test_scene_lifecycle_and_atomic_reordering(self):
        """Test scene creation, ordering, updates, and atomic reordering."""
        # Create project
        self.project_service.create_project(
            "ch_alpha",
            {"id": "proj_scenes", "title": "Scenes Test"},
        )

        # 1. Add 3 scenes
        s1 = self.scene_service.create_scene(
            "ch_alpha",
            "proj_scenes",
            {
                "id": "sc_01",
                "sequence_number": 1,
                "title": "Intro Scene",
                "scene_type": SceneType.INTRO,
                "duration_seconds": 3.5,
            },
        )
        s2 = self.scene_service.create_scene(
            "ch_alpha",
            "proj_scenes",
            {
                "id": "sc_02",
                "sequence_number": 2,
                "title": "Climax Scene",
                "duration_seconds": 10.0,
            },
        )
        s3 = self.scene_service.create_scene(
            "ch_alpha",
            "proj_scenes",
            {
                "id": "sc_03",
                "sequence_number": 3,
                "title": "Outro Scene",
                "scene_type": SceneType.OUTRO,
                "duration_seconds": 4.0,
            },
        )

        scenes = self.scene_service.list_scenes("ch_alpha", "proj_scenes")
        self.assertEqual([s.id for s in scenes], ["sc_01", "sc_02", "sc_03"])

        # 2. Cannot access scene from different channel
        with self.assertRaises(SceneNotFoundException):
            self.scene_service.get_scene("ch_beta", "sc_01")

        # 3. Duplicate sequence number within same project raises ValidationException
        with self.assertRaises(ValidationException):
            self.scene_service.create_scene(
                "ch_alpha",
                "proj_scenes",
                {"id": "sc_dup_seq", "sequence_number": 1},
            )

        # 4. Atomic reordering (swap sc_03 and sc_01: [sc_03, sc_02, sc_01])
        reordered = self.scene_service.reorder_scenes(
            "ch_alpha",
            "proj_scenes",
            ["sc_03", "sc_02", "sc_01"],
        )
        self.assertEqual([s.id for s in reordered], ["sc_03", "sc_02", "sc_01"])
        self.assertEqual([s.sequence_number for s in reordered], [1, 2, 3])

        # 5. Reordering with invalid IDs raises ValidationException
        with self.assertRaises(ValidationException):
            self.scene_service.reorder_scenes("ch_alpha", "proj_scenes", ["sc_03", "sc_unknown"])

