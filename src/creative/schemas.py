"""Data models and schemas for Creative Rules, Character Bible, and Channel Profiles (Task 3.12).

Defines deterministic, structured schemas for:
- Outfit Profiles and Character Consistency Rules
- Character Bible Entries with immutable vs mutable attributes
- Channel Profiles with Branding, Style, and Production Preferences
- Story, Camera, Lighting, and Prompt Templates
"""

from typing import Optional, List, Dict, Any
from enum import Enum
from pydantic import BaseModel, Field

from src.flow.schemas import (
    FlowGenerationMode,
    GenerationSpecification,
    CharacterReference,
    VisualStyleReference,
)


class OutfitProfile(BaseModel):
    """Specific clothing and styling configuration for a character in an episode or scene."""
    outfit_id: str = Field(..., description="Unique outfit identifier (e.g. 'outfit_default', 'outfit_gardening')")
    name: str = Field(..., description="Display name for the outfit")
    description: str = Field(..., description="Narrative description of the attire")
    top: str = Field(default="", description="Shirt, sweater, or upper body garment")
    bottom: str = Field(default="", description="Pants, shorts, skirt, or overalls")
    footwear: str = Field(default="", description="Shoes, boots, or sneakers")
    accessories: List[str] = Field(default_factory=list, description="Hats, belts, glasses, or badges")
    signature_colors: List[str] = Field(default_factory=list, description="Primary hex or color tokens")
    prompt_snippet: str = Field(
        ...,
        description="Concise prompt fragment describing this attire (e.g. 'wearing bright yellow short-sleeve t-shirt, blue denim overalls with brass clasps, red canvas sneakers with white laces')"
    )


class CharacterBibleEntry(BaseModel):
    """Authoritative Character Bible specification anchoring identity across episodes."""
    character_id: str = Field(..., description="Unique immutable character ID (e.g. 'char_tommy')")
    character_name: str = Field(..., description="Character canonical name")
    identity_description: str = Field(..., description="Core narrative role, personality, and age")
    status: str = Field(default="active", description="'active', 'draft', or 'partially_documented'")
    
    # Immutable Core Attributes (Never change between outfits or episodes)
    immutable_attributes: Dict[str, Any] = Field(
        default_factory=dict,
        description="Physical traits: age, skin tone, eye color/shape, facial structure, body build"
    )
    hair_and_face_description: str = Field(
        ...,
        description="Detailed facial and hair features (e.g. 'unruly brown curls with bouncy texture, big expressive warm brown eyes, button nose, rosy chubby cheeks, cheerful smile')"
    )
    
    # Movement & Expression Characteristics
    movement_characteristics: str = Field(
        default="energetic bouncy toddler steps, spontaneous giggles, wide arm gestures",
        description="Movement dynamics and physical energy"
    )
    expressions: Dict[str, str] = Field(
        default_factory=lambda: {
            "happy": "wide infectious grin, sparkling eyes, crinkled cheeks",
            "curious": "head tilted 15 degrees, wide wonder-filled eyes, slightly parted lips",
            "surprised": "raised eyebrows, rounded mouth, hands raised in delight",
            "laughing": "joyful open-mouth laugh, closed happy eyes, hands on tummy",
        },
        description="Defined facial expressions mapped to emotional states"
    )

    # Wardrobe & Outfits
    outfit_profiles: Dict[str, OutfitProfile] = Field(
        default_factory=dict,
        description="Collection of approved outfit variants keyed by outfit_id"
    )
    default_outfit_id: str = Field(default="default", description="Fallback outfit ID")

    # References & Consistency Guards
    reference_images: List[str] = Field(
        default_factory=list,
        description="Relative storage keys to approved reference portraits in StorageService"
    )
    character_consistency_rules: List[str] = Field(
        default_factory=list,
        description="Deterministic negative or guard tokens (e.g. 'Never add adult proportions, keep curly brown hair')"
    )
    missing_fields: List[str] = Field(
        default_factory=list,
        description="Explicit documentation flags for unconfirmed attributes (e.g. '[PENDING_OWNER_INPUT]')"
    )
    notes: Optional[str] = None

    def get_outfit(self, outfit_id: Optional[str] = None) -> Optional[OutfitProfile]:
        """Resolve specific outfit profile or default."""
        target = outfit_id or self.default_outfit_id
        return self.outfit_profiles.get(target) or self.outfit_profiles.get("default")

    def compile_anchor_prompt(
        self,
        outfit_id: Optional[str] = None,
        expression_key: Optional[str] = None,
    ) -> str:
        """Compose the complete character anchor prompt combining immutable traits, outfit, and expression."""
        outfit = self.get_outfit(outfit_id)
        outfit_desc = outfit.prompt_snippet if outfit else ""
        expr_desc = self.expressions.get(expression_key or "happy", "")

        parts = [
            f"cute 3D cartoon {self.identity_description.strip()}",
            self.hair_and_face_description.strip(),
            outfit_desc.strip(),
        ]
        if expr_desc:
            parts.append(f"expression is {expr_desc.strip()}")

        return ", ".join(p for p in parts if p)

    def to_character_reference(self, outfit_id: Optional[str] = None) -> CharacterReference:
        """Convert to Flow CharacterReference data contract."""
        visual_anchor = self.compile_anchor_prompt(outfit_id=outfit_id)
        ref_image = self.reference_images[0] if self.reference_images else None
        return CharacterReference(
            character_id=self.character_id,
            name=self.character_name,
            description=self.identity_description,
            visual_anchor=visual_anchor,
            reference_asset_storage_key=ref_image,
        )


class ChannelProfile(BaseModel):
    """Complete, versioned channel brand identity, visual style, and production profile."""
    channel_id: str = Field(..., description="Unique channel identifier (e.g. 'ch_dreammatter_001')")
    name: str = Field(..., description="Channel public display name")
    handle: str = Field(..., description="Channel YouTube handle")
    brand_description: str = Field(..., description="Brand mission and narrative philosophy")
    brand_philosophy: str = Field(
        default="Whimsical, heartwarming 3D animated shorts celebrating innocence, curiosity, and wonder.",
        description="Core creative tone"
    )
    content_categories: List[str] = Field(
        default_factory=lambda: ["Children 3D Animation", "Shorts", "Whimsical Comedy", "Visual Wonder"],
        description="Primary content niches"
    )
    default_language: str = Field(default="en")
    supported_locales: List[str] = Field(default_factory=lambda: ["en", "ar"])
    default_aspect_ratio: str = Field(default="9:16")
    
    # Visual Style Reference (Compliant with Flow)
    visual_style: VisualStyleReference = Field(..., description="Channel visual aesthetic profile")
    
    # Brand Colors
    color_palette: Dict[str, str] = Field(
        default_factory=lambda: {
            "primary": "#FFD166",
            "secondary": "#06D6A0",
            "accent": "#EF476F",
            "background": "#118AB2",
            "neutral": "#F8F9FA",
        },
        description="Canonical brand color tokens"
    )
    
    # Branding Rules & Overlay Directives
    branding_rules: Dict[str, Any] = Field(
        default_factory=lambda: {
            "watermark_position": "top_right",
            "safe_margin_percent": 10.0,
            "title_card_font": "Fredoka One",
            "end_screen_cta": "Keep dreaming big with DreamMatter!",
        },
        description="Branding placement and typography rules"
    )
    
    # Production Preferences
    production_preferences: Dict[str, Any] = Field(
        default_factory=lambda: {
            "default_scene_duration_sec": 7.5,
            "target_episode_duration_sec": 23.0,
            "enforce_true_peak_dbtp": -1.0,
            "audio_ducking_db": -14.0,
            "default_fps": 30,
            "default_resolution": "1080p",
        },
        description="Engine defaults for video and audio production"
    )
    
    # Characters affiliated with this channel
    character_ids: List[str] = Field(
        default_factory=lambda: ["char_tommy", "char_zara"],
        description="Character IDs officially belonging to this channel franchise"
    )


class CameraRule(BaseModel):
    """Cinematic camera directive rule."""
    rule_id: str
    act_phase: str  # "establishing", "interaction", "reveal"
    framing: str
    lens_type: str
    movement: str
    prompt_snippet: str


class LightingRule(BaseModel):
    """Atmospheric lighting rule."""
    rule_id: str
    lighting_type: str
    time_of_day: str
    atmosphere: str
    prompt_snippet: str


class StoryActTemplate(BaseModel):
    """Template for a 3-act short story structure."""
    act_number: int
    title: str
    narrative_function: str  # e.g. "Hook & Discovery", "Interaction & Complication", "Celebration & Payoff"
    target_duration_sec: float
    recommended_transition: str
    camera_rule_id: str
    lighting_rule_id: str

