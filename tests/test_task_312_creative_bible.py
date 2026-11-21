"""Unit and Integration Tests for Task 3.12: Local Creative Rules & Character Bible.

Verifies:
1. ChannelProfile loading for DreamMatter with brand rules, color palette, and visual style.
2. CharacterBibleEntry retrieval for Tommy (fully documented with immutable attributes and outfits).
3. CharacterBibleEntry retrieval for Zara (with explicitly marked missing fields).
4. CreativeRulesEngine deterministic resolution of camera, lighting, motion, and continuity directives.
5. Integration with FlowPromptService synthesizing PromptPackages via Character Bible.
6. Consistency of character attributes across multiple scenes.
7. Switching OutfitProfile while strictly preserving immutable facial & physical identity.
8. Persistence and reload of custom profiles and character entries across restarts.
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
from src.storage.service import StorageService
from src.creative.schemas import (
    OutfitProfile,
    CharacterBibleEntry,
    ChannelProfile,
    CameraRule,
    LightingRule,
)
from src.creative.channel_profile_service import ChannelProfileService
from src.creative.character_bible_service import CharacterBibleService
from src.creative.rules_engine import CreativeRulesEngine
from src.flow.prompt_service import FlowPromptService
from src.flow.schemas import FlowGenerationMode, PromptPackage


class TestTask312CreativeBible(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="studio_test_creative_")
        self.temp_path = Path(self.temp_dir)

        # Isolated DB & Storage
        self.db_path = self.temp_path / "runtime" / "test_creative.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_manager = DatabaseManager(db_path=self.db_path)
        MigrationManager(self.db_manager).run_migrations()

        self.storage_service = StorageService(local_base_dir=self.temp_path)
        self.profile_service = ChannelProfileService(self.storage_service)
        self.bible_service = CharacterBibleService(self.storage_service)
        self.rules_engine = CreativeRulesEngine()

        self.flow_service = FlowPromptService(
            db_manager=self.db_manager,
            storage_service=self.storage_service,
            channel_profile_service=self.profile_service,
            character_bible_service=self.bible_service,
            rules_engine=self.rules_engine,
        )

        # Register channel
        self.channel_id = "ch_dreammatter_001"
        self.channel_repo = ChannelRepository(self.db_manager)
        self.channel_repo.create(
            ChannelCreate(
                id=self.channel_id,
                name="DreamMatter",
                handle="@DreamMatterWorld",
                niche="children_animation",
                default_language="en",
                default_generation_mode="image_to_video",
            )
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 1. Channel Profile Tests
    # -------------------------------------------------------------------------

    def test_01_load_channel_profile(self):
        """Scenario 1: ChannelProfile for DreamMatter loads with all required brand directives."""
        profile = self.profile_service.get_profile(self.channel_id)

        self.assertEqual(profile.channel_id, self.channel_id)
        self.assertEqual(profile.name, "DreamMatter")
        self.assertEqual(profile.default_aspect_ratio, "9:16")
        self.assertIn("Children 3D Animation", profile.content_categories)
        self.assertIn("#FFD166", profile.color_palette.values())
        self.assertEqual(profile.branding_rules["watermark_position"], "top_right")
        self.assertEqual(profile.visual_style.style_name, "DreamMatter Whimsical 3D Animation")
        self.assertIn("Pixar-inspired", profile.visual_style.style_prompt)

    # -------------------------------------------------------------------------
    # 2. Character Bible Tests (Tommy & Zara)
    # -------------------------------------------------------------------------

    def test_02_retrieve_character_tommy(self):
        """Scenario 2: Tommy is retrieved with full immutable traits and outfit profiles."""
        tommy = self.bible_service.get_character("char_tommy", self.channel_id)
        self.assertIsNotNone(tommy)
        self.assertEqual(tommy.character_name, "Tommy")
        self.assertEqual(tommy.status, "active")

        # Immutable traits
        self.assertEqual(tommy.immutable_attributes["age"], 5)
        self.assertIn("brown curls", tommy.hair_and_face_description)
        self.assertIn("rosy chubby", tommy.hair_and_face_description)

        # Outfits
        self.assertIn("default", tommy.outfit_profiles)
        self.assertIn("gardening", tommy.outfit_profiles)
        default_outfit = tommy.get_outfit("default")
        self.assertIn("denim overalls", default_outfit.prompt_snippet)

        # Compiled prompt
        anchor = tommy.compile_anchor_prompt()
        self.assertIn("cute 3D cartoon", anchor)
        self.assertIn("brown curls", anchor)
        self.assertIn("denim overalls", anchor)

    def test_03_retrieve_character_zara(self):
        """Scenario 3: Zara is retrieved with explicit missing fields documentation."""
        zara = self.bible_service.get_character("char_zara", self.channel_id)
        self.assertIsNotNone(zara)
        self.assertEqual(zara.character_name, "Zara")
        self.assertEqual(zara.status, "partially_documented")

        # Immutable traits
        self.assertEqual(zara.immutable_attributes["age"], 6)
        self.assertIn("space buns", zara.hair_and_face_description)
        self.assertIn("amber", zara.hair_and_face_description)

        # Explicit missing fields
        self.assertTrue(len(zara.missing_fields) > 0)
        self.assertTrue(any("Reference portrait" in mf for mf in zara.missing_fields))
        self.assertTrue(any("voice talent" in mf for mf in zara.missing_fields))

    # -------------------------------------------------------------------------
    # 3. Creative Rules Engine Tests
    # -------------------------------------------------------------------------

    def test_04_rules_engine_deterministic_directives(self):
        """Scenario 4: CreativeRulesEngine provides deterministic camera, lighting, and motion directives."""
        # Scene 1: Establishing
        cam1 = self.rules_engine.resolve_camera_directive(1, 3)
        self.assertIn("Wide establishing shot", cam1)
        light1 = self.rules_engine.resolve_lighting_directive(1, 3)
        self.assertIn("Warm golden morning sunlight", light1)

        # Scene 2: Intimate action
        cam2 = self.rules_engine.resolve_camera_directive(2, 3)
        self.assertIn("Medium intimate eye-level shot", cam2)

        # Scene 3: Reveal / Celebration
        cam3 = self.rules_engine.resolve_camera_directive(3, 3)
        self.assertIn("Slow crane pull-out reveal shot", cam3)
        light3 = self.rules_engine.resolve_lighting_directive(3, 3)
        self.assertIn("Radiant sunny illumination", light3)

    # -------------------------------------------------------------------------
    # 4. Prompt Package Integration & Character Consistency Tests
    # -------------------------------------------------------------------------

    def test_05_build_prompt_package_with_character_bible(self):
        """Scenario 5: FlowPromptService builds package using CharacterBibleService."""
        manifest = {
            "channel_id": self.channel_id,
            "project_id": "proj_cb_test_01",
            "title": "Tommy Meets a Fluffy Cloud",
            "description": "A delightful 3D short.",
            "aspect_ratio": "9:16",
            "target_duration_sec": 15.0,
            "scenes": [
                {
                    "id": "sc_cb_01",
                    "sequence_number": 1,
                    "title": "Act 1: The Cloud Appears",
                    "narration_text": "Tommy looked up and saw a gentle floating cloud.",
                    "duration_seconds": 7.5,
                },
                {
                    "id": "sc_cb_02",
                    "sequence_number": 2,
                    "title": "Act 2: The Sparkle Tickle",
                    "narration_text": "Tommy tickled the cloud and it showered sweet sparks.",
                    "duration_seconds": 7.5,
                },
            ],
        }

        package = self.flow_service.build_package_from_episode_manifest(
            channel_id=self.channel_id,
            manifest_data_or_path=manifest,
        )

        self.assertIsInstance(package, PromptPackage)
        self.assertEqual(len(package.scene_specs), 2)

        # Verify Tommy is linked from Character Bible
        self.assertEqual(len(package.global_character_refs), 1)
        self.assertEqual(package.global_character_refs[0].character_id, "char_tommy")

        # Verify compiled prompts in Scene 1 and Scene 2 contain Tommy's visual anchor
        p1 = package.scene_specs[0].compile_full_flow_prompt(package.global_style_ref)
        p2 = package.scene_specs[1].compile_full_flow_prompt(package.global_style_ref)
        self.assertIn("unruly mop of bouncy brown curls", p1)
        self.assertIn("unruly mop of bouncy brown curls", p2)

    def test_06_character_consistency_across_scenes(self):
        """Scenario 6: Core facial and physical traits remain identical across all scenes."""
        tommy = self.bible_service.get_character("char_tommy", self.channel_id)
        sc1 = self.rules_engine.build_scene_specification(
            scene_id="sc_01",
            scene_index=1,
            total_scenes=2,
            title="Act 1",
            narration="Tommy enters.",
            duration_sec=7.5,
            character=tommy,
        )
        sc2 = self.rules_engine.build_scene_specification(
            scene_id="sc_02",
            scene_index=2,
            total_scenes=2,
            title="Act 2",
            narration="Tommy plays.",
            duration_sec=7.5,
            character=tommy,
        )

        # Both scenes carry identical character anchor
        anchor1 = sc1.character_refs[0].visual_anchor
        anchor2 = sc2.character_refs[0].visual_anchor
        self.assertEqual(anchor1, anchor2)

        # Scene 2 continuity directive explicitly references Tommy
        self.assertIn("Maintain Tommy character visual consistency from Scene 1", sc2.continuity_instructions)

    def test_07_switch_outfit_preserves_immutable_identity(self):
        """Scenario 7: Changing outfit modifies clothes while preserving facial & bodily identity."""
        tommy = self.bible_service.get_character("char_tommy", self.channel_id)

        anchor_default = tommy.compile_anchor_prompt(outfit_id="default")
        anchor_gardening = tommy.compile_anchor_prompt(outfit_id="gardening")

        # Facial and body identity remains identical
        self.assertIn("unruly mop of bouncy brown curls", anchor_default)
        self.assertIn("unruly mop of bouncy brown curls", anchor_gardening)
        self.assertIn("rosy chubby toddler cheeks", anchor_default)
        self.assertIn("rosy chubby toddler cheeks", anchor_gardening)

        # Wardrobe changes accurately
        self.assertIn("denim overalls", anchor_default)
        self.assertNotIn("denim overalls", anchor_gardening)
        self.assertIn("light mint-green t-shirt", anchor_gardening)
        self.assertIn("rubber rain boots", anchor_gardening)

    def test_08_save_and_reload_persistence(self):
        """Scenario 8: Custom character and profile persist to StorageService and reload cleanly."""
        custom_char = CharacterBibleEntry(
            character_id="char_milo",
            character_name="Milo the Puppy",
            identity_description="Playful golden puppy with floppy ears",
            hair_and_face_description="soft golden fur, big wet black nose, expressive floppy ears",
            outfit_profiles={
                "default": OutfitProfile(
                    outfit_id="default",
                    name="Red Collar",
                    description="Standard collar",
                    prompt_snippet="wearing bright red leather collar with small brass bell",
                )
            },
        )
        self.bible_service.save_character(custom_char, self.channel_id)

        # Recreate service and load
        fresh_bible_service = CharacterBibleService(self.storage_service)
        reloaded = fresh_bible_service.get_character("char_milo", self.channel_id)

        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded.character_name, "Milo the Puppy")
        self.assertIn("Red Collar", reloaded.outfit_profiles["default"].name)


if __name__ == "__main__":
    unittest.main()

