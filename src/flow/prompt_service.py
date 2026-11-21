"""FlowPromptService: Coordinates Google Flow Outbound package generation, prompt versioning, and storage (Task 3.11)."""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, Union

from src.core.logger import get_logger
from src.core.exceptions import StudioException, ProjectNotFoundException, SceneNotFoundException
from src.database.connection import DatabaseManager, get_db_manager
from src.database.repositories.flow_prompt_repository import FlowPromptRepository
from src.database.repositories.channel_repository import ChannelRepository
from src.database.repositories.project_repository import ProjectRepository
from src.database.repositories.scene_repository import SceneRepository
from src.database.repositories.asset_repository import AssetRepository
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

logger = get_logger("studio.flow.prompt_service")


class FlowPromptService:
    """Core service for building, versioning, and exporting Google Flow prompt packages."""

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        storage_service: Optional[StorageService] = None,
        channel_profile_service: Optional[Any] = None,
        character_bible_service: Optional[Any] = None,
        rules_engine: Optional[Any] = None,
    ):
        self.db_manager = db_manager or get_db_manager()
        self.storage_service = storage_service or StorageService()
        self.prompt_repo = FlowPromptRepository(self.db_manager)
        self.channel_repo = ChannelRepository(self.db_manager)
        self.project_repo = ProjectRepository(self.db_manager)
        self.scene_repo = SceneRepository(self.db_manager)
        self.asset_repo = AssetRepository(self.db_manager)

        from src.creative.channel_profile_service import ChannelProfileService
        from src.creative.character_bible_service import CharacterBibleService
        from src.creative.rules_engine import CreativeRulesEngine

        self.channel_profile_service = channel_profile_service or ChannelProfileService(self.storage_service)
        self.character_bible_service = character_bible_service or CharacterBibleService(self.storage_service)
        self.rules_engine = rules_engine or CreativeRulesEngine()

    # -------------------------------------------------------------------------
    # Package Construction (Outbound)
    # -------------------------------------------------------------------------

    def build_package_from_episode_manifest(
        self,
        channel_id: str,
        manifest_data_or_path: Union[Dict[str, Any], Path, str],
        flow_project_name: Optional[str] = None,
        flow_workspace_ref: Optional[str] = None,
        package_version: Optional[int] = None,
        outfit_id: Optional[str] = None,
    ) -> PromptPackage:
        """Construct a complete, versioned Google Flow PromptPackage from an episode manifest."""
        manifest: Dict[str, Any]
        if isinstance(manifest_data_or_path, (str, Path)):
            p = Path(manifest_data_or_path)
            if not p.is_file():
                # Try relative to studio storage or channels
                rel_path = self.storage_service.get_local_path(str(manifest_data_or_path))
                if rel_path.is_file():
                    p = rel_path
                else:
                    raise StudioException(f"Manifest file not found: {manifest_data_or_path}")
            with open(p, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        else:
            manifest = manifest_data_or_path

        project_id = manifest.get("project_id", f"proj_{uuid.uuid4().hex[:8]}")
        title = manifest.get("title", f"Episode {project_id}")
        description = manifest.get("description", "")
        aspect_ratio = manifest.get("aspect_ratio", "9:16")
        target_duration = float(manifest.get("target_duration_sec", 60.0))

        # 1. Flow Project Reference
        flow_proj_name = flow_project_name or f"{channel_id} - {title[:30]}"
        flow_ref = FlowProjectReference(
            flow_project_name=flow_proj_name,
            flow_workspace_ref=flow_workspace_ref,
            notes=f"Generated for project {project_id}",
        )

        # 2. Episode Specification
        ep_spec = EpisodeSpecification(
            episode_id=project_id,
            title=title,
            summary=description,
            channel_id=channel_id,
            aspect_ratio=aspect_ratio,
            target_duration_sec=target_duration,
        )

        # 3. Channel Default Style & Character Anchors via Creative Services
        master_style = self._resolve_channel_style(channel_id)
        character_refs = self._resolve_channel_characters(channel_id, manifest, outfit_id=outfit_id)
        primary_char = self._resolve_primary_character_bible_entry(channel_id, manifest)

        # 4. Master Episode Prompt
        master_prompt = (
            f"A whimsical 3D animated scene set in a colorful world. {description}. "
            f"Vibrant aesthetic, rich volumetric lighting, cinematic animation."
        )

        # 5. Scene Prompt Specifications
        scenes_data = manifest.get("scenes", [])
        scene_specs: List[ScenePromptSpecification] = []
        total_scenes = len(scenes_data)

        for idx, sc in enumerate(scenes_data, start=1):
            sc_id = sc.get("id", f"sc_{project_id}_{idx:02d}")
            sc_title = sc.get("title", f"Scene {idx}")
            narration = sc.get("narration_text", "")
            duration = float(sc.get("duration_seconds", 5.0))
            asset_info = sc.get("asset", {})

            # Determine generation mode: if asset image exists, default to ingredients_to_video
            gen_mode = FlowGenerationMode.TEXT_TO_VIDEO
            ref_asset_key = None
            if asset_info and asset_info.get("rel_path"):
                ref_asset_key = asset_info.get("rel_path")
                gen_mode = FlowGenerationMode.INGREDIENTS_TO_VIDEO

            # Formulate visual action prompt
            visual_prompt = sc.get("visual_prompt")
            if not visual_prompt:
                visual_prompt = self._synthesize_scene_visual_prompt(
                    title=sc_title,
                    narration=narration,
                    scene_index=idx,
                    total_scenes=total_scenes,
                    character_refs=character_refs,
                )

            # Directives via Rules Engine
            camera_dir = self.rules_engine.resolve_camera_directive(idx, total_scenes)
            lighting_dir = self.rules_engine.resolve_lighting_directive(idx, total_scenes)
            motion_dir = self.rules_engine.resolve_motion_directive(idx, total_scenes)
            continuity_dir = self.rules_engine.resolve_continuity_directive(
                idx, character=primary_char, outfit_id=outfit_id
            )

            # Check existing version in SQLite or record version 1
            existing = self.prompt_repo.get_scene_prompt(sc_id)
            if existing:
                current_ver = existing["version"]
                visual_prompt = existing["visual_prompt"]
                camera_dir = existing["camera_instructions"] or camera_dir
                lighting_dir = existing["lighting_instructions"] or lighting_dir
                motion_dir = existing["motion_instructions"] or motion_dir
                continuity_dir = existing["continuity_instructions"] or continuity_dir
                gen_mode = FlowGenerationMode(existing["generation_mode"])
            else:
                current_ver = 1
                prompt_record_id = f"prp_{uuid.uuid4().hex[:12]}"
                self.prompt_repo.save_scene_prompt(
                    id=prompt_record_id,
                    scene_id=sc_id,
                    channel_id=channel_id,
                    project_id=project_id,
                    version=current_ver,
                    visual_prompt=visual_prompt,
                    generation_mode=gen_mode.value,
                    master_prompt=master_prompt,
                    camera_instructions=camera_dir,
                    lighting_instructions=lighting_dir,
                    motion_instructions=motion_dir,
                    continuity_instructions=continuity_dir,
                    generation_specs_json={"aspect_ratio": aspect_ratio, "target_duration_sec": duration},
                    character_refs_json=[c.model_dump() for c in character_refs],
                    style_refs_json=[master_style.model_dump()],
                )

            scene_spec = ScenePromptSpecification(
                scene_id=sc_id,
                scene_index=idx,
                prompt_version=current_ver,
                title=sc_title,
                generation_mode=gen_mode,
                visual_prompt=visual_prompt,
                camera_instructions=camera_dir,
                lighting_instructions=lighting_dir,
                motion_instructions=motion_dir,
                continuity_instructions=continuity_dir,
                voiceover_cue=narration,
                specs=GenerationSpecification(
                    aspect_ratio=aspect_ratio,
                    target_duration_sec=duration,
                ),
                character_refs=character_refs,
                style_refs=[master_style],
            )
            scene_specs.append(scene_spec)

        # 6. Assemble PromptPackage
        pkg_version = package_version or self.prompt_repo.get_next_package_version(project_id)
        pkg_id = f"pkg_{project_id}_v{pkg_version}"

        package = PromptPackage(
            package_id=pkg_id,
            package_version=pkg_version,
            channel_id=channel_id,
            project_id=project_id,
            episode_spec=ep_spec,
            flow_project_ref=flow_ref,
            master_prompt=master_prompt,
            scene_specs=scene_specs,
            global_character_refs=character_refs,
            global_style_ref=master_style,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        package.content_hash = package.compute_hash()
        return package

    def build_package_from_project_id(
        self,
        channel_id: str,
        project_id: str,
        flow_project_name: Optional[str] = None,
        flow_workspace_ref: Optional[str] = None,
    ) -> PromptPackage:
        """Construct a PromptPackage querying database tables (`projects` and `scenes`)."""
        project = self.project_repo.get_by_id(project_id)
        if not project:
            raise ProjectNotFoundException(project_id)

        scenes = self.scene_repo.list_by_project(project_id)
        manifest_data = {
            "channel_id": channel_id,
            "project_id": project_id,
            "title": project.title,
            "description": project.description,
            "aspect_ratio": getattr(project.aspect_ratio, "value", str(project.aspect_ratio)),
            "target_duration_sec": project.target_duration_sec,
            "scenes": [
                {
                    "id": sc.id,
                    "sequence_number": sc.sequence_number,
                    "title": sc.title,
                    "narration_text": sc.narration_text,
                    "visual_prompt": sc.visual_prompt,
                    "duration_seconds": sc.duration_seconds,
                }
                for sc in scenes
            ],
        }
        return self.build_package_from_episode_manifest(
            channel_id=channel_id,
            manifest_data_or_path=manifest_data,
            flow_project_name=flow_project_name,
            flow_workspace_ref=flow_workspace_ref,
        )

    # -------------------------------------------------------------------------
    # Prompt Editing & Version History Preservation
    # -------------------------------------------------------------------------

    def modify_scene_prompt(
        self,
        scene_id: str,
        visual_prompt: str,
        generation_mode: Optional[FlowGenerationMode] = None,
        camera_instructions: Optional[str] = None,
        lighting_instructions: Optional[str] = None,
        motion_instructions: Optional[str] = None,
        continuity_instructions: Optional[str] = None,
        negative_prompt: Optional[str] = None,
    ) -> ScenePromptSpecification:
        """Modify a scene prompt by creating an incremented new version row in SQLite.

        Preserves 100% of previous version history without overwriting or deleting historical rows.
        """
        current = self.prompt_repo.get_scene_prompt(scene_id)
        if not current:
            raise SceneNotFoundException(scene_id)

        new_version = current["version"] + 1
        new_prompt_id = f"prp_{uuid.uuid4().hex[:12]}"

        gen_mode_val = generation_mode.value if generation_mode else current["generation_mode"]
        cam_val = camera_instructions if camera_instructions is not None else current["camera_instructions"]
        light_val = lighting_instructions if lighting_instructions is not None else current["lighting_instructions"]
        mot_val = motion_instructions if motion_instructions is not None else current["motion_instructions"]
        cont_val = continuity_instructions if continuity_instructions is not None else current["continuity_instructions"]
        neg_val = negative_prompt if negative_prompt is not None else current["negative_prompt"]

        saved_row = self.prompt_repo.save_scene_prompt(
            id=new_prompt_id,
            scene_id=scene_id,
            channel_id=current["channel_id"],
            project_id=current["project_id"],
            version=new_version,
            visual_prompt=visual_prompt,
            generation_mode=gen_mode_val,
            master_prompt=current["master_prompt"],
            negative_prompt=neg_val,
            camera_instructions=cam_val,
            lighting_instructions=light_val,
            motion_instructions=mot_val,
            continuity_instructions=cont_val,
            generation_specs_json=current["generation_specs_json"],
            character_refs_json=current["character_refs_json"],
            style_refs_json=current["style_refs_json"],
        )

        logger.info(f"Promoted scene '{scene_id}' to prompt version {new_version} (prompt_id={new_prompt_id}).")

        char_models = [CharacterReference(**c) for c in saved_row["character_refs_json"]]
        style_models = [VisualStyleReference(**s) for s in saved_row["style_refs_json"]]

        return ScenePromptSpecification(
            scene_id=scene_id,
            scene_index=1,
            prompt_version=new_version,
            title="",
            generation_mode=FlowGenerationMode(gen_mode_val),
            visual_prompt=visual_prompt,
            negative_prompt=neg_val,
            camera_instructions=cam_val,
            lighting_instructions=light_val,
            motion_instructions=mot_val,
            continuity_instructions=cont_val,
            specs=GenerationSpecification(**saved_row["generation_specs_json"]),
            character_refs=char_models,
            style_refs=style_models,
        )

    def get_scene_prompt_history(self, scene_id: str) -> List[Dict[str, Any]]:
        """Query complete version history for a scene's prompts."""
        return self.prompt_repo.list_scene_prompt_history(scene_id)

    # -------------------------------------------------------------------------
    # Package Export & StorageService Persistence
    # -------------------------------------------------------------------------

    def export_package(self, package: PromptPackage) -> PromptPackageExportResult:
        """Export PromptPackage to JSON and Markdown via StorageService exclusively."""
        package.content_hash = package.compute_hash()

        # Build canonical storage keys
        ch_id = package.channel_id
        proj_id = package.project_id
        pkg_id = package.package_id

        json_key = f"channels/{ch_id}/projects/{proj_id}/flow_packages/{pkg_id}.json"
        md_key = f"channels/{ch_id}/projects/{proj_id}/flow_packages/{pkg_id}.md"

        # 1. Save JSON manifest
        json_bytes = package.model_dump_json(indent=2).encode("utf-8")
        self.storage_service.save_file(
            key=json_key,
            data=json_bytes,
            mime_type="application/json",
            custom_metadata={"content_hash": package.content_hash, "version": package.package_version},
        )

        # 2. Save Markdown presentation
        md_content = package.generate_markdown()
        md_bytes = md_content.encode("utf-8")
        self.storage_service.save_file(
            key=md_key,
            data=md_bytes,
            mime_type="text/markdown",
            custom_metadata={"content_hash": package.content_hash, "version": package.package_version},
        )

        # 3. Register in SQLite database
        self.prompt_repo.save_prompt_package(
            id=pkg_id,
            channel_id=ch_id,
            project_id=proj_id,
            package_version=package.package_version,
            title=package.episode_spec.title,
            flow_project_name=package.flow_project_ref.flow_project_name,
            flow_workspace_ref=package.flow_project_ref.flow_workspace_ref or "",
            content_hash=package.content_hash,
            json_storage_key=json_key,
            markdown_storage_key=md_key,
            scene_count=len(package.scene_specs),
            metadata_json={"aspect_ratio": package.episode_spec.aspect_ratio},
        )

        logger.info(
            f"Successfully exported Flow PromptPackage '{pkg_id}' (v{package.package_version}) "
            f"with {len(package.scene_specs)} scenes to StorageService."
        )

        return PromptPackageExportResult(
            package_id=pkg_id,
            package_version=package.package_version,
            content_hash=package.content_hash,
            channel_id=ch_id,
            project_id=proj_id,
            json_storage_key=json_key,
            markdown_storage_key=md_key,
            scene_count=len(package.scene_specs),
            created_at=package.created_at,
        )

    def load_package_from_storage(self, json_storage_key: str) -> PromptPackage:
        """Load and deserialize a PromptPackage from StorageService."""
        data_bytes = self.storage_service.load_bytes(json_storage_key)
        data_dict = json.loads(data_bytes.decode("utf-8"))
        return PromptPackage(**data_dict)

    def load_package_by_id(self, package_id: str) -> PromptPackage:
        """Look up package in SQLite, then load payload from StorageService."""
        record = self.prompt_repo.get_prompt_package(package_id)
        if not record:
            raise StudioException(f"Prompt package record '{package_id}' not found in database.")
        return self.load_package_from_storage(record["json_storage_key"])

    # -------------------------------------------------------------------------
    # Internal Heuristic Synthesizers (Powered by Task 3.12 Creative Services)
    # -------------------------------------------------------------------------

    def _resolve_channel_style(self, channel_id: str) -> VisualStyleReference:
        """Resolve visual aesthetic for the channel via ChannelProfileService."""
        profile = self.channel_profile_service.get_profile(channel_id)
        return profile.visual_style

    def _resolve_channel_characters(
        self, channel_id: str, manifest: Dict[str, Any], outfit_id: Optional[str] = None
    ) -> List[CharacterReference]:
        """Resolve character visual anchors from CharacterBibleService and manifest context."""
        chars: List[CharacterReference] = []
        text_corpus = (
            manifest.get("title", "")
            + " "
            + manifest.get("description", "")
            + " "
            + " ".join(sc.get("narration_text", "") for sc in manifest.get("scenes", []))
        ).lower()

        # Check all registered characters for this channel
        available = self.character_bible_service.list_characters(channel_id)
        for char_entry in available:
            name_lower = char_entry.character_name.lower()
            id_lower = char_entry.character_id.lower()
            if name_lower in text_corpus or id_lower in text_corpus:
                chars.append(char_entry.to_character_reference(outfit_id=outfit_id))

        # Fallback to Tommy if none matched and channel is DreamMatter
        if not chars and "dreammatter" in channel_id.lower():
            tommy = self.character_bible_service.get_character("char_tommy", channel_id)
            if tommy:
                chars.append(tommy.to_character_reference(outfit_id=outfit_id))

        return chars

    def _resolve_primary_character_bible_entry(
        self, channel_id: str, manifest: Dict[str, Any]
    ) -> Optional[Any]:
        """Resolve primary CharacterBibleEntry for continuity directives."""
        text_corpus = (
            manifest.get("title", "")
            + " "
            + manifest.get("description", "")
            + " "
            + " ".join(sc.get("narration_text", "") for sc in manifest.get("scenes", []))
        ).lower()

        for char_id in ["char_tommy", "char_zara"]:
            if char_id[5:] in text_corpus or char_id in text_corpus:
                return self.character_bible_service.get_character(char_id, channel_id)

        if "dreammatter" in channel_id.lower():
            return self.character_bible_service.get_character("char_tommy", channel_id)
        return None

    @staticmethod
    def _synthesize_scene_visual_prompt(
        title: str,
        narration: str,
        scene_index: int,
        total_scenes: int,
        character_refs: List[CharacterReference],
    ) -> str:
        """Formulate a vivid descriptive action prompt for Google Flow."""
        char_name = character_refs[0].name if character_refs else "Character"
        if narration:
            return (
                f"{char_name} in an enchanting garden. Action: {narration.strip()} "
                f"Dynamic scene composition with delightful whimsical details and expressive emotion."
            )
        return f"{char_name} engaging in scene '{title}' with lively joyful presence."

    @staticmethod
    def _synthesize_camera_instruction(scene_index: int, total_scenes: int) -> str:
        """Select appropriate cinematic camera framing based on narrative arc."""
        if scene_index == 1:
            return "Wide establishing shot smoothly transitioning to medium close-up, slow dolly in."
        elif scene_index == total_scenes:
            return "Slow crane pull-out reveal shot, widening field of view to capture full garden celebration."
        else:
            return "Medium intimate eye-level shot with subtle gentle panning following character hand movement."

