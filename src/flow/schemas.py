"""Pydantic data contracts for Google Flow Outbound integration (Task 3.11).

Defines structured, versioned schemas for:
- Flow Project References
- Character & Visual Style References
- Camera, Lighting & Motion Instructions
- Scene Prompt Specifications & Versioning
- Complete Prompt Packages for Google Flow
"""

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class FlowGenerationMode(str, Enum):
    """Generation modes supported within Google Flow."""
    TEXT_TO_VIDEO = "text_to_video"
    FRAMES_TO_VIDEO = "frames_to_video"
    INGREDIENTS_TO_VIDEO = "ingredients_to_video"


class FlowProjectReference(BaseModel):
    """Reference linking studio projects to Google Flow workspaces."""
    flow_project_name: str = Field(..., description="Project name as labeled in Google Flow UI")
    flow_workspace_ref: Optional[str] = Field(default=None, description="Workspace folder or URL identifier in Flow")
    notes: Optional[str] = Field(default=None, description="Operator workflow notes or reminders")


class GenerationSpecification(BaseModel):
    """Technical generation parameters expected by Google Flow."""
    aspect_ratio: str = Field(default="9:16", description="Aspect ratio (e.g. '9:16' or '16:9')")
    resolution: str = Field(default="1080p", description="Target video resolution (e.g. '1080p')")
    target_duration_sec: float = Field(default=5.0, description="Target clip duration in seconds")
    fps: int = Field(default=30, description="Framerate")
    seed: Optional[int] = Field(default=None, description="Deterministic seed if fixed")
    motion_strength: Optional[float] = Field(default=None, description="Motion strength multiplier (0.1 - 1.0)")


class CharacterReference(BaseModel):
    """Character identity and visual anchors for continuity across scenes."""
    character_id: str = Field(..., description="Unique character identifier (e.g. 'char_tommy')")
    name: str = Field(..., description="Character name (e.g. 'Tommy')")
    description: str = Field(..., description="Character narrative role and personality")
    visual_anchor: str = Field(
        ...,
        description="Core immutable visual tokens (e.g. 'cute 5yo boy, unruly brown curls, bright yellow t-shirt, blue denim overalls, red sneakers')"
    )
    reference_asset_storage_key: Optional[str] = Field(
        default=None,
        description="Relative storage key to character reference portrait in StorageService"
    )
    voice_id: Optional[str] = Field(default=None, description="Associated voice ID for dialogue/narration")


class VisualStyleReference(BaseModel):
    """Visual aesthetic and world-building guidance for Flow generation."""
    style_name: str = Field(..., description="Name of visual aesthetic (e.g. 'DreamMatter 3D Whimsical')")
    style_prompt: str = Field(
        ...,
        description="Art style prompt tokens (e.g. 'Pixar-inspired 3D animation, soft volumetric lighting, vibrant saturated pastels, Octane render aesthetic, high detail, child-friendly')"
    )
    lighting_profile: str = Field(
        default="warm sunlight, soft ambient rim light, gentle volumetric glow",
        description="Default lighting guidelines"
    )
    camera_profile: str = Field(
        default="35mm lens, shallow depth of field, cinematic child-eye level framing",
        description="Default camera guidelines"
    )
    color_palette: List[str] = Field(
        default_factory=lambda: ["#FFD166", "#06D6A0", "#118AB2", "#EF476F"],
        description="Key color hex tokens"
    )


class ScenePromptSpecification(BaseModel):
    """Complete, self-contained prompt specification for a single scene in Google Flow."""
    scene_id: str = Field(..., description="Immutable scene identifier (e.g. 'sc_tommy3_01')")
    scene_index: int = Field(..., description="1-based sequence index in the episode")
    prompt_version: int = Field(default=1, description="Incrementing version of this scene's prompt")
    title: str = Field(default="", description="Scene descriptive title")
    generation_mode: FlowGenerationMode = Field(
        default=FlowGenerationMode.TEXT_TO_VIDEO,
        description="Google Flow generation mode for this scene"
    )
    visual_prompt: str = Field(..., description="Primary visual action prompt for Flow")
    negative_prompt: str = Field(
        default="blurry, distorted anatomy, morphing hands, flickers, text, watermark, photorealistic human uncanny valley",
        description="Negative prompt exclusions"
    )
    camera_instructions: str = Field(
        default="",
        description="Specific camera directive (e.g. 'Slow push-in dolly toward Tommy, eye-level framing')"
    )
    lighting_instructions: str = Field(
        default="",
        description="Lighting directive (e.g. 'Golden hour shafts through cartoon leaves, soft rim light on cloud')"
    )
    motion_instructions: str = Field(
        default="",
        description="Dynamic motion guidance (e.g. 'Gentle rhythmic bobbing motion, whimsical soft physics')"
    )
    continuity_instructions: str = Field(
        default="",
        description="Continuity anchor from previous scene (e.g. 'Preserve Tommy position from Scene 1, yellow shirt, denim overalls')"
    )
    voiceover_cue: str = Field(
        default="",
        description="Narration or dialogue timing cue corresponding to this scene"
    )
    specs: GenerationSpecification = Field(default_factory=GenerationSpecification)
    character_refs: List[CharacterReference] = Field(default_factory=list)
    style_refs: List[VisualStyleReference] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def compile_full_flow_prompt(self, master_style: Optional[VisualStyleReference] = None) -> str:
        """Compose the full, ready-to-paste prompt string for Google Flow."""
        parts = []

        # 1. Subject & Action
        parts.append(self.visual_prompt.strip())

        # 2. Character Anchors
        for char in self.character_refs:
            parts.append(f"Featuring {char.name}: {char.visual_anchor.strip()}.")

        # 3. Camera & Motion
        if self.camera_instructions:
            parts.append(f"Camera: {self.camera_instructions.strip()}.")
        if self.motion_instructions:
            parts.append(f"Motion: {self.motion_instructions.strip()}.")

        # 4. Lighting & Atmosphere
        if self.lighting_instructions:
            parts.append(f"Lighting: {self.lighting_instructions.strip()}.")

        # 5. Continuity
        if self.continuity_instructions:
            parts.append(f"Continuity: {self.continuity_instructions.strip()}.")

        # 6. Style Anchor
        style = master_style or (self.style_refs[0] if self.style_refs else None)
        if style:
            parts.append(f"Style: {style.style_prompt.strip()}.")

        return " ".join(part for part in parts if part)


class EpisodeSpecification(BaseModel):
    """Episode metadata anchoring the prompt package."""
    episode_id: str
    title: str
    summary: str
    channel_id: str
    language: str = "en"
    target_duration_sec: float = 60.0
    aspect_ratio: str = "9:16"


class PromptPackage(BaseModel):
    """Top-level exportable package bundle for Google Flow."""
    package_id: str = Field(..., description="Unique package ID (e.g. 'pkg_flow_dm_ep3_v1')")
    package_version: int = Field(default=1, description="Package iteration version")
    channel_id: str
    project_id: str
    episode_spec: EpisodeSpecification
    flow_project_ref: FlowProjectReference
    master_prompt: str = Field(..., description="High-level episode narrative anchor prompt")
    scene_specs: List[ScenePromptSpecification] = Field(default_factory=list)
    global_character_refs: List[CharacterReference] = Field(default_factory=list)
    global_style_ref: Optional[VisualStyleReference] = None
    content_hash: str = Field(default="", description="SHA-256 fingerprint of prompt contents")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    created_by: str = Field(default="system")
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def compute_hash(self) -> str:
        """Compute deterministic SHA-256 fingerprint over all scene prompts and references."""
        payload = {
            "channel_id": self.channel_id,
            "project_id": self.project_id,
            "master_prompt": self.master_prompt,
            "scene_specs": [
                {
                    "scene_id": s.scene_id,
                    "scene_index": s.scene_index,
                    "prompt_version": s.prompt_version,
                    "visual_prompt": s.visual_prompt,
                    "generation_mode": s.generation_mode.value,
                    "camera": s.camera_instructions,
                    "lighting": s.lighting_instructions,
                    "motion": s.motion_instructions,
                    "continuity": s.continuity_instructions,
                }
                for s in self.scene_specs
            ],
            "characters": [c.character_id for c in self.global_character_refs],
            "style": self.global_style_ref.style_name if self.global_style_ref else None,
        }
        encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def generate_markdown(self) -> str:
        """Generate human-readable, beautifully structured Markdown for Google Flow manual handoff."""
        md = []
        md.append(f"# Google Flow Production Package: {self.episode_spec.title}")
        md.append(f"**Package ID:** `{self.package_id}` (Version {self.package_version})  ")
        md.append(f"**Channel ID:** `{self.channel_id}` | **Project ID:** `{self.project_id}`  ")
        md.append(f"**Flow Project Name:** `{self.flow_project_ref.flow_project_name}`  ")
        if self.flow_project_ref.flow_workspace_ref:
            md.append(f"**Flow Workspace:** `{self.flow_project_ref.flow_workspace_ref}`  ")
        md.append(f"**Content Hash (SHA-256):** `{self.content_hash}`  ")
        md.append(f"**Generated At:** `{self.created_at}`  ")
        md.append("\n---\n")

        # Master Prompt
        md.append("## 🌟 Master Episode Prompt")
        md.append(f"> {self.master_prompt}\n")

        # Global Visual Style
        if self.global_style_ref:
            md.append("## 🎨 Global Visual Style Reference")
            md.append(f"- **Style Name:** {self.global_style_ref.style_name}")
            md.append(f"- **Prompt Tokens:** `{self.global_style_ref.style_prompt}`")
            md.append(f"- **Lighting Profile:** {self.global_style_ref.lighting_profile}")
            md.append(f"- **Camera Profile:** {self.global_style_ref.camera_profile}")
            md.append(f"- **Color Palette:** {', '.join(self.global_style_ref.color_palette)}")
            md.append("\n---\n")

        # Character References
        if self.global_character_refs:
            md.append("## 👤 Character Identity Anchors")
            for char in self.global_character_refs:
                md.append(f"### {char.name} (`{char.character_id}`)")
                md.append(f"- **Description:** {char.description}")
                md.append(f"- **Visual Anchor:** `{char.visual_anchor}`")
                if char.reference_asset_storage_key:
                    md.append(f"- **Reference Image Key:** `{char.reference_asset_storage_key}`")
            md.append("\n---\n")

        # Scenes
        md.append("## 🎬 Scene Generation Specifications (Google Flow Ready)")
        md.append("Copy each compiled prompt directly into Google Flow according to the indicated generation mode:\n")

        for s in self.scene_specs:
            compiled = s.compile_full_flow_prompt(master_style=self.global_style_ref)
            md.append(f"### Scene {s.scene_index}: {s.title or s.scene_id} (`v{s.prompt_version}`)")
            md.append(f"- **Scene ID:** `{s.scene_id}`")
            md.append(f"- **Target Duration:** {s.specs.target_duration_sec}s | **Aspect Ratio:** `{s.specs.aspect_ratio}`")
            md.append(f"- **Flow Generation Mode:** **`{s.generation_mode.value.upper()}`**")
            if s.voiceover_cue:
                md.append(f"- **Voiceover Timing Cue:** *\"{s.voiceover_cue}\"*")
            md.append("\n**📋 Compiled Flow Prompt (Direct Copy):**")
            md.append("```text")
            md.append(compiled)
            md.append("```")
            md.append("\n**🚫 Negative Prompt:**")
            md.append("```text")
            md.append(s.negative_prompt)
            md.append("```")
            md.append("\n**Technical Directives:**")
            if s.camera_instructions:
                md.append(f"- **Camera:** {s.camera_instructions}")
            if s.motion_instructions:
                md.append(f"- **Motion:** {s.motion_instructions}")
            if s.lighting_instructions:
                md.append(f"- **Lighting:** {s.lighting_instructions}")
            if s.continuity_instructions:
                md.append(f"- **Continuity:** {s.continuity_instructions}")
            md.append("\n")

        md.append("---\n")
        md.append("## 📝 Manual Flow Execution Checklist")
        md.append("1. Open Google Flow and navigate to project `{}`.".format(self.flow_project_ref.flow_project_name))
        md.append("2. Set output aspect ratio to `{}` and resolution to `{}`.".format(self.episode_spec.aspect_ratio, "1080p"))
        md.append("3. For each scene above, set the specified Generation Mode (`Text-to-Video`, `Frames-to-Video`, or `Ingredients-to-Video`).")
        md.append("4. Paste the **Compiled Flow Prompt** and **Negative Prompt** into the prompt fields.")
        md.append("5. Review character consistency anchors against the first frame before rendering.")
        md.append("6. Download rendered video clips and save with corresponding Scene IDs (`{scene_id}.mp4`) for Task 3.13 Inbound Import.")
        md.append("\n*Generated by Universal AI Video Production Studio (Task 3.11 Outbound)*\n")

        return "\n".join(md)


class PromptPackageExportResult(BaseModel):
    """Result of exporting a PromptPackage via StorageService."""
    package_id: str
    package_version: int
    content_hash: str
    channel_id: str
    project_id: str
    json_storage_key: str
    markdown_storage_key: str
    scene_count: int
    created_at: str

