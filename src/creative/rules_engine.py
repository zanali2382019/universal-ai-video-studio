"""CreativeRulesEngine: Deterministic, local rules and prompt template processor (Task 3.12).

Provides rule-based directives for:
- 3-Act Story Structure and Scene Pacing
- Cinematic Camera Framing and Dynamics
- Volumetric & Atmospheric Lighting Profiles
- Child-Safe and Whimsical Motion Guidance
- Character Consistency & Continuity Anchoring
"""

from typing import Optional, List, Dict, Any

from src.creative.schemas import (
    ChannelProfile,
    CharacterBibleEntry,
    CameraRule,
    LightingRule,
    StoryActTemplate,
)
from src.flow.schemas import (
    FlowGenerationMode,
    GenerationSpecification,
    ScenePromptSpecification,
    VisualStyleReference,
    CharacterReference,
)


class CreativeRulesEngine:
    """Deterministic rules engine composing cinematic scene directives and prompt templates."""

    CAMERA_RULES: Dict[str, CameraRule] = {
        "establishing_wide": CameraRule(
            rule_id="establishing_wide",
            act_phase="establishing",
            framing="Wide establishing shot smoothly transitioning to medium close-up",
            lens_type="35mm cinematic lens",
            movement="Slow smooth dolly push-in toward character",
            prompt_snippet="Wide establishing shot smoothly transitioning to medium close-up, slow dolly in.",
        ),
        "intimate_action": CameraRule(
            rule_id="intimate_action",
            act_phase="interaction",
            framing="Medium intimate eye-level shot",
            lens_type="50mm portrait lens with shallow depth of field",
            movement="Gentle panning tracking character hands and facial reactions",
            prompt_snippet="Medium intimate eye-level shot with subtle gentle panning following character hand movement.",
        ),
        "wonder_low_angle": CameraRule(
            rule_id="wonder_low_angle",
            act_phase="interaction",
            framing="Low-angle child perspective looking upward",
            lens_type="28mm wide lens",
            movement="Slow upwards tilt revealing magical phenomenon",
            prompt_snippet="Low-angle child-perspective shot with gentle upward tilt emphasizing magical height.",
        ),
        "reveal_crane": CameraRule(
            rule_id="reveal_crane",
            act_phase="reveal",
            framing="Dramatic slow crane pull-out reveal shot",
            lens_type="35mm cinematic lens",
            movement="Slow rising and widening crane movement capturing full scene wonder",
            prompt_snippet="Slow crane pull-out reveal shot, widening field of view to capture full environment celebration.",
        ),
    }

    LIGHTING_RULES: Dict[str, LightingRule] = {
        "morning_golden": LightingRule(
            rule_id="morning_golden",
            lighting_type="volumetric_golden",
            time_of_day="morning",
            atmosphere="warm, soft, welcoming",
            prompt_snippet="Warm golden morning sunlight, soft ambient rim light, gentle volumetric haze and subtle dust motes.",
        ),
        "magical_sparkle": LightingRule(
            rule_id="magical_sparkle",
            lighting_type="bioluminescent_pastels",
            time_of_day="afternoon_magical",
            atmosphere="enchanted, glowing, sparkly",
            prompt_snippet="Soft pastel rim lighting, gentle bioluminescent cloud glow, swirling luminous sparkle particles.",
        ),
        "celebration_blooming": LightingRule(
            rule_id="celebration_blooming",
            lighting_type="vibrant_radiant",
            time_of_day="daylight_celebration",
            atmosphere="exuberant, bright, joyous",
            prompt_snippet="Radiant sunny illumination, multi-colored pastel petal reflections, sparkling highlights.",
        ),
    }

    DEFAULT_STORY_TEMPLATES: List[StoryActTemplate] = [
        StoryActTemplate(
            act_number=1,
            title="Act 1: Discovery & Hook",
            narrative_function="Establish character world and introduce magical wonder",
            target_duration_sec=7.5,
            recommended_transition="cut",
            camera_rule_id="establishing_wide",
            lighting_rule_id="morning_golden",
        ),
        StoryActTemplate(
            act_number=2,
            title="Act 2: Interaction & Complication",
            narrative_function="Playful engagement, discovery, comedic or awe-inspiring reaction",
            target_duration_sec=8.0,
            recommended_transition="fade",
            camera_rule_id="intimate_action",
            lighting_rule_id="magical_sparkle",
        ),
        StoryActTemplate(
            act_number=3,
            title="Act 3: Payoff & Celebration",
            narrative_function="Climactic wonder, blooming payoff, heartwarming resolution",
            target_duration_sec=8.0,
            recommended_transition="cut",
            camera_rule_id="reveal_crane",
            lighting_rule_id="celebration_blooming",
        ),
    ]

    def resolve_camera_directive(self, scene_index: int, total_scenes: int) -> str:
        """Deterministically choose camera movement based on narrative scene position."""
        if scene_index == 1:
            return self.CAMERA_RULES["establishing_wide"].prompt_snippet
        elif scene_index == total_scenes:
            return self.CAMERA_RULES["reveal_crane"].prompt_snippet
        else:
            return self.CAMERA_RULES["intimate_action"].prompt_snippet

    def resolve_lighting_directive(self, scene_index: int, total_scenes: int) -> str:
        """Deterministically choose lighting profile based on narrative act."""
        if scene_index == 1:
            return self.LIGHTING_RULES["morning_golden"].prompt_snippet
        elif scene_index == total_scenes:
            return self.LIGHTING_RULES["celebration_blooming"].prompt_snippet
        else:
            return self.LIGHTING_RULES["magical_sparkle"].prompt_snippet

    def resolve_motion_directive(self, scene_index: int, total_scenes: int) -> str:
        """Deterministically provide motion physics directives."""
        if scene_index == 1:
            return "Smooth, natural character entry and curious discovery gestures, soft physics."
        elif scene_index == total_scenes:
            return "Dynamic joyful celebration, swirling dancing particles, expressive happy animation."
        else:
            return "Lively playful character interaction, responsive gentle hand movements and bouncy giggles."

    def resolve_continuity_directive(
        self,
        scene_index: int,
        character: Optional[CharacterBibleEntry] = None,
        outfit_id: Optional[str] = None,
    ) -> str:
        """Formulate strict continuity rules preserving character anchors across consecutive cuts."""
        if not character:
            return "Maintain visual continuity, color harmony, and background environment stability."

        outfit = character.get_outfit(outfit_id)
        outfit_name = outfit.name if outfit else "signature attire"
        prev_scene_num = max(1, scene_index - 1)

        return (
            f"Maintain {character.character_name} character visual consistency from Scene {prev_scene_num}: "
            f"{character.hair_and_face_description.split(',')[0].strip()}, wearing {outfit_name}. "
            f"Preserve consistent lighting angle and emotional arc."
        )

    def build_scene_specification(
        self,
        scene_id: str,
        scene_index: int,
        total_scenes: int,
        title: str,
        narration: str,
        duration_sec: float,
        character: Optional[CharacterBibleEntry] = None,
        outfit_id: Optional[str] = None,
        channel_profile: Optional[ChannelProfile] = None,
        generation_mode: FlowGenerationMode = FlowGenerationMode.TEXT_TO_VIDEO,
        custom_visual_prompt: Optional[str] = None,
    ) -> ScenePromptSpecification:
        """Synthesize a complete ScenePromptSpecification applying all deterministic rules."""
        # 1. Resolve directives
        cam = self.resolve_camera_directive(scene_index, total_scenes)
        light = self.resolve_lighting_directive(scene_index, total_scenes)
        motion = self.resolve_motion_directive(scene_index, total_scenes)
        continuity = self.resolve_continuity_directive(scene_index, character, outfit_id)

        # 2. Formulate Visual Prompt
        if custom_visual_prompt:
            visual = custom_visual_prompt
        else:
            char_name = character.character_name if character else "Character"
            visual = (
                f"{char_name} in an enchanting garden. Action: {narration.strip()} "
                f"Dynamic scene composition with delightful whimsical details and expressive emotion."
            )

        # 3. References
        char_refs = [character.to_character_reference(outfit_id=outfit_id)] if character else []
        style_refs = [channel_profile.visual_style] if channel_profile else []

        aspect_ratio = channel_profile.default_aspect_ratio if channel_profile else "9:16"

        return ScenePromptSpecification(
            scene_id=scene_id,
            scene_index=scene_index,
            prompt_version=1,
            title=title,
            generation_mode=generation_mode,
            visual_prompt=visual,
            camera_instructions=cam,
            lighting_instructions=light,
            motion_instructions=motion,
            continuity_instructions=continuity,
            voiceover_cue=narration,
            specs=GenerationSpecification(
                aspect_ratio=aspect_ratio,
                target_duration_sec=duration_sec,
            ),
            character_refs=char_refs,
            style_refs=style_refs,
        )

