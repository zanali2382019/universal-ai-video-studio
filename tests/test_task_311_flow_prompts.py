"""Unit and Integration Tests for Task 3.11: Google Flow Outbound Package Generator.

Verifies:
1. Pydantic schemas (Data Contracts, generation specs, character & style refs, prompt compilation).
2. Deterministic SHA-256 content hashing and Markdown export formatting.
3. FlowPromptRepository versioned storage and history retrieval without loss.
4. FlowPromptService package synthesis from manifests and database projects.
5. In-place prompt editing creating new versions (v1 -> v2) with zero history loss.
6. Unified StorageService export (JSON + Markdown) and round-trip deserialization.
7. Complete isolation in temporary STUDIO_DATA_DIR (100% offline & CI safe).
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from src.core.config import get_settings
from src.database.connection import DatabaseManager
from src.database.migrations import MigrationManager
from src.database.models import ChannelCreate
from src.database.repositories.channel_repository import ChannelRepository
from src.database.repositories.project_repository import ProjectRepository
from src.database.repositories.scene_repository import SceneRepository
from src.database.repositories.flow_prompt_repository import FlowPromptRepository
from src.storage.service import StorageService
from src.flow.schemas import (
    FlowGenerationMode,
    FlowProjectReference,
    GenerationSpecification,
    CharacterReference,
    VisualStyleReference,
    ScenePromptSpecification,
    EpisodeSpecification,
    PromptPackage,
    PromptPackageExportResult,
)
from src.flow.prompt_service import FlowPromptService


class TestTask311FlowPrompts(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="studio_test_flow_")
        self.temp_path = Path(self.temp_dir)

        # Isolated database & storage
        self.db_path = self.temp_path / "runtime" / "test_flow.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_manager = DatabaseManager(db_path=self.db_path)
        MigrationManager(self.db_manager).run_migrations()

        self.storage_service = StorageService(local_base_dir=self.temp_path)
        self.flow_service = FlowPromptService(
            db_manager=self.db_manager,
            storage_service=self.storage_service,
        )

        # Set up test channel
        self.channel_repo = ChannelRepository(self.db_manager)
        self.channel_id = "ch_test_dream"
        self.channel_repo.create(
            ChannelCreate(
                id=self.channel_id,
                name="Test Dream Channel",
                handle="@TestDreamWorld",
                niche="children_animation",
                default_language="en",
                default_generation_mode="image_to_video",
            )
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 1. Schema & Data Contract Tests
    # -------------------------------------------------------------------------

    def test_01_generation_specs_and_character_reference(self):
        """Scenario 1: CharacterReference and GenerationSpecification validate accurately."""
        specs = GenerationSpecification(
            aspect_ratio="9:16",
            resolution="1080p",
            target_duration_sec=7.5,
            fps=30,
            seed=42,
            motion_strength=0.7,
        )
        self.assertEqual(specs.aspect_ratio, "9:16")
        self.assertEqual(specs.target_duration_sec, 7.5)
        self.assertEqual(specs.seed, 42)

        char = CharacterReference(
            character_id="char_tommy",
            name="Tommy",
            description="5yo curious boy",
            visual_anchor="yellow shirt, denim overalls, curly hair",
            voice_id="narrator_enzo",
        )
        self.assertEqual(char.name, "Tommy")
        self.assertIn("denim overalls", char.visual_anchor)

    def test_02_scene_prompt_compilation_with_style_and_character(self):
        """Scenario 2: compile_full_flow_prompt assembles full directive string."""
        char = CharacterReference(
            character_id="char_tommy",
            name="Tommy",
            description="Joyful boy",
            visual_anchor="curly brown hair, yellow t-shirt",
        )
        style = VisualStyleReference(
            style_name="DreamMatter 3D",
            style_prompt="Pixar-inspired 3D animation, vibrant pastels",
        )
        scene_spec = ScenePromptSpecification(
            scene_id="sc_01",
            scene_index=1,
            prompt_version=1,
            title="Meeting the Cloud",
            generation_mode=FlowGenerationMode.TEXT_TO_VIDEO,
            visual_prompt="Tommy waving excitedly at a tiny glowing cloud.",
            camera_instructions="Slow push-in dolly shot.",
            motion_instructions="Playful jumping gesture.",
            lighting_instructions="Warm golden volumetric lighting.",
            continuity_instructions="Maintain yellow shirt from opening.",
            character_refs=[char],
            style_refs=[style],
        )

        compiled = scene_spec.compile_full_flow_prompt(master_style=style)
        self.assertIn("Tommy waving excitedly", compiled)
        for expected in [
            "Featuring Tommy: curly brown hair, yellow t-shirt.",
            "Camera: Slow push-in dolly shot.",
            "Motion: Playful jumping gesture.",
            "Lighting: Warm golden volumetric lighting.",
            "Continuity: Maintain yellow shirt from opening.",
            "Style: Pixar-inspired 3D animation, vibrant pastels.",
        ]:
            self.assertIn(expected, compiled)

    def test_03_prompt_package_deterministic_hash_and_markdown(self):
        """Scenario 3: Content hash is deterministic and Markdown renders complete sections."""
        char = CharacterReference(
            character_id="char_tommy",
            name="Tommy",
            description="Joyful boy",
            visual_anchor="curly brown hair, yellow t-shirt",
        )
        style = VisualStyleReference(
            style_name="DreamMatter 3D",
            style_prompt="Pixar-inspired 3D animation",
        )
        scene = ScenePromptSpecification(
            scene_id="sc_01",
            scene_index=1,
            prompt_version=1,
            visual_prompt="Tommy playing in garden.",
        )
        pkg = PromptPackage(
            package_id="pkg_test_01",
            channel_id="ch_test",
            project_id="proj_test",
            episode_spec=EpisodeSpecification(
                episode_id="proj_test",
                title="Tommy's Magic Cloud",
                summary="Whimsical short",
                channel_id="ch_test",
            ),
            flow_project_ref=FlowProjectReference(flow_project_name="Flow Tommy Ep 1"),
            master_prompt="A magical sunny afternoon in Tommy's garden.",
            scene_specs=[scene],
            global_character_refs=[char],
            global_style_ref=style,
        )

        h1 = pkg.compute_hash()
        h2 = pkg.compute_hash()
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)

        md = pkg.generate_markdown()
        self.assertIn("# Google Flow Production Package: Tommy's Magic Cloud", md)
        self.assertIn("Flow Tommy Ep 1", md)
        self.assertIn("## 🌟 Master Episode Prompt", md)
        self.assertIn("## 👤 Character Identity Anchors", md)
        self.assertIn("## 🎬 Scene Generation Specifications", md)
        self.assertIn("## 📝 Manual Flow Execution Checklist", md)

    # -------------------------------------------------------------------------
    # 2. FlowPromptRepository Tests
    # -------------------------------------------------------------------------

    def test_04_repository_scene_prompt_versioning(self):
        """Scenario 4: FlowPromptRepository correctly tracks incrementing versions."""
        repo = FlowPromptRepository(self.db_manager)

        # Version 1
        r1 = repo.save_scene_prompt(
            id="prp_01",
            scene_id="sc_test_01",
            channel_id=self.channel_id,
            project_id="proj_01",
            version=1,
            visual_prompt="Prompt version 1",
            generation_mode="text_to_video",
        )
        self.assertEqual(r1["version"], 1)
        self.assertEqual(r1["visual_prompt"], "Prompt version 1")

        # Version 2
        r2 = repo.save_scene_prompt(
            id="prp_02",
            scene_id="sc_test_01",
            channel_id=self.channel_id,
            project_id="proj_01",
            version=2,
            visual_prompt="Prompt version 2 (improved)",
            generation_mode="ingredients_to_video",
        )
        self.assertEqual(r2["version"], 2)

        # Default query returns highest active version (v2)
        latest = repo.get_scene_prompt("sc_test_01")
        self.assertEqual(latest["version"], 2)
        self.assertEqual(latest["visual_prompt"], "Prompt version 2 (improved)")

        # Querying specific version returns historical version (v1)
        v1 = repo.get_scene_prompt("sc_test_01", version=1)
        self.assertEqual(v1["version"], 1)
        self.assertEqual(v1["visual_prompt"], "Prompt version 1")

        # History list contains both in descending order
        history = repo.list_scene_prompt_history("sc_test_01")
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["version"], 2)
        self.assertEqual(history[1]["version"], 1)

    # -------------------------------------------------------------------------
    # 3. FlowPromptService Integration Tests
    # -------------------------------------------------------------------------

    def test_05_build_package_from_manifest_dict(self):
        """Scenario 5: FlowPromptService builds package from episode manifest dictionary."""
        manifest = {
            "channel_id": self.channel_id,
            "project_id": "proj_manifest_01",
            "title": "Tommy Episode One",
            "description": "Tommy meets a dancing bubble.",
            "aspect_ratio": "9:16",
            "target_duration_sec": 15.0,
            "scenes": [
                {
                    "id": "sc_m_01",
                    "sequence_number": 1,
                    "title": "Act 1: The Bubble",
                    "narration_text": "Tommy saw a shiny floating bubble.",
                    "duration_seconds": 5.0,
                    "asset": {"rel_path": "media_vault/originals/images/bubble.jpg"},
                },
                {
                    "id": "sc_m_02",
                    "sequence_number": 2,
                    "title": "Act 2: The Chase",
                    "narration_text": "He chased it through the blooming flowerbeds.",
                    "duration_seconds": 5.0,
                },
            ],
        }

        package = self.flow_service.build_package_from_episode_manifest(
            channel_id=self.channel_id,
            manifest_data_or_path=manifest,
            flow_project_name="Flow Project Bubble",
        )

        self.assertEqual(package.project_id, "proj_manifest_01")
        self.assertEqual(len(package.scene_specs), 2)
        self.assertEqual(package.scene_specs[0].scene_id, "sc_m_01")
        # Scene 1 had an asset image -> ingredients_to_video
        self.assertEqual(package.scene_specs[0].generation_mode, FlowGenerationMode.INGREDIENTS_TO_VIDEO)
        # Scene 2 had no asset image -> text_to_video
        self.assertEqual(package.scene_specs[1].generation_mode, FlowGenerationMode.TEXT_TO_VIDEO)

        # Character Tommy was automatically identified and linked
        self.assertTrue(any(c.character_id == "char_tommy" for c in package.global_character_refs))

    def test_06_modify_scene_prompt_preserves_version_history(self):
        """Scenario 6: Modifying prompt creates v2 without loss of v1."""
        manifest = {
            "channel_id": self.channel_id,
            "project_id": "proj_edit_01",
            "title": "Edit Test",
            "scenes": [
                {
                    "id": "sc_edit_01",
                    "sequence_number": 1,
                    "title": "Scene 1",
                    "narration_text": "Initial narration",
                }
            ],
        }
        self.flow_service.build_package_from_episode_manifest(self.channel_id, manifest)

        # Verify initial prompt is v1
        v1 = self.flow_service.prompt_repo.get_scene_prompt("sc_edit_01")
        self.assertEqual(v1["version"], 1)

        # Edit prompt -> should become v2
        updated = self.flow_service.modify_scene_prompt(
            scene_id="sc_edit_01",
            visual_prompt="Completely revamped visual action with dazzling light effects.",
            camera_instructions="Fast whip pan to center.",
        )
        self.assertEqual(updated.prompt_version, 2)
        self.assertEqual(updated.camera_instructions, "Fast whip pan to center.")

        # Verify historical versions in SQLite
        history = self.flow_service.get_scene_prompt_history("sc_edit_01")
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["version"], 2)
        self.assertEqual(history[1]["version"], 1)
        self.assertIn("Completely revamped", history[0]["visual_prompt"])
        self.assertIn("Initial narration", history[1]["visual_prompt"])

    def test_07_export_and_storage_service_round_trip(self):
        """Scenario 7: Export package via StorageService and reload identical data."""
        manifest = {
            "channel_id": self.channel_id,
            "project_id": "proj_export_01",
            "title": "Export Roundtrip Test",
            "description": "Roundtrip test description",
            "aspect_ratio": "9:16",
            "target_duration_sec": 10.0,
            "scenes": [
                {
                    "id": "sc_exp_01",
                    "sequence_number": 1,
                    "title": "Scene 1",
                    "narration_text": "Roundtrip narration",
                    "duration_seconds": 5.0,
                }
            ],
        }
        package = self.flow_service.build_package_from_episode_manifest(self.channel_id, manifest)

        result = self.flow_service.export_package(package)
        self.assertIsInstance(result, PromptPackageExportResult)
        self.assertEqual(result.scene_count, 1)

        # Verify relative storage keys
        self.assertTrue(result.json_storage_key.startswith(f"channels/{self.channel_id}/projects/"))
        self.assertTrue(result.json_storage_key.endswith(".json"))
        self.assertTrue(result.markdown_storage_key.endswith(".md"))

        # Verify StorageService persistence
        self.assertTrue(self.storage_service.exists(result.json_storage_key))
        self.assertTrue(self.storage_service.exists(result.markdown_storage_key))

        # Reload from StorageService
        reloaded = self.flow_service.load_package_from_storage(result.json_storage_key)
        self.assertEqual(reloaded.package_id, package.package_id)
        self.assertEqual(reloaded.content_hash, package.content_hash)
        self.assertEqual(len(reloaded.scene_specs), 1)
        self.assertEqual(reloaded.scene_specs[0].scene_id, "sc_exp_01")

        # Reload by ID from DB lookup
        db_reloaded = self.flow_service.load_package_by_id(result.package_id)
        self.assertEqual(db_reloaded.package_id, package.package_id)


if __name__ == "__main__":
    unittest.main()
