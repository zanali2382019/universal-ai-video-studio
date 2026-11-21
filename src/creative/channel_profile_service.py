"""ChannelProfileService: Manages channel identity, branding rules, and visual style profiles (Task 3.12)."""

import json
from pathlib import Path
from typing import Optional, List, Dict, Any

from src.core.config import get_settings
from src.core.logger import get_logger
from src.core.exceptions import ChannelNotFoundException
from src.storage.service import StorageService
from src.creative.schemas import ChannelProfile, VisualStyleReference

logger = get_logger("studio.creative.channel_profile_service")


class ChannelProfileService:
    """Service for managing, retrieving, and persisting ChannelProfile configurations."""

    def __init__(self, storage_service: Optional[StorageService] = None):
        self.storage_service = storage_service or StorageService()
        self.settings = get_settings()

    def get_profile(self, channel_id: str) -> ChannelProfile:
        """Retrieve the ChannelProfile for a given channel ID.

        Attempts to load from channels/<channel_id>/config/channel_profile.json via StorageService.
        Falls back to deterministic defaults if file does not exist.
        """
        clean_ch = channel_id.strip()
        storage_key = f"channels/{clean_ch}/config/channel_profile.json"

        if self.storage_service.exists(storage_key):
            try:
                data_bytes = self.storage_service.load_bytes(storage_key)
                data = json.loads(data_bytes.decode("utf-8"))
                logger.info(f"Loaded ChannelProfile from storage for '{clean_ch}'.")
                return ChannelProfile(**data)
            except Exception as e:
                logger.warning(f"Failed to parse ChannelProfile for '{clean_ch}': {e}. Falling back to default.")

        # Default fallback
        return self._create_default_profile(clean_ch)

    def save_profile(self, profile: ChannelProfile) -> None:
        """Persist a ChannelProfile into StorageService."""
        clean_ch = profile.channel_id.strip()
        storage_key = f"channels/{clean_ch}/config/channel_profile.json"
        json_bytes = profile.model_dump_json(indent=2).encode("utf-8")

        self.storage_service.save_file(
            key=storage_key,
            data=json_bytes,
            mime_type="application/json",
            custom_metadata={"channel_id": clean_ch},
        )
        logger.info(f"Persisted ChannelProfile for '{clean_ch}' at storage key '{storage_key}'.")

    def _create_default_profile(self, channel_id: str) -> ChannelProfile:
        """Synthesize default profile based on channel niche and name."""
        is_dreammatter = "dreammatter" in channel_id.lower()

        if is_dreammatter:
            return ChannelProfile(
                channel_id=channel_id,
                name="DreamMatter",
                handle="@DreamMatterWorld",
                brand_description="Heartwarming, whimsical 3D animated shorts celebrating curiosity and everyday wonder.",
                visual_style=VisualStyleReference(
                    style_name="DreamMatter Whimsical 3D Animation",
                    style_prompt=(
                        "Whimsical Pixar-inspired 3D animation, soft volumetric morning light, "
                        "saturated vibrant pastel palette, highly detailed textures, gentle shadows, Octane render quality"
                    ),
                    lighting_profile="Warm golden sunlight, soft ambient rim light, gentle volumetric haze and sparkle reflections",
                    camera_profile="Cinematic child-eye level 35mm lens, gentle shallow depth of field, clear centered framing",
                    color_palette=["#FFD166", "#06D6A0", "#118AB2", "#EF476F", "#F8F9FA"],
                ),
                character_ids=["char_tommy", "char_zara"],
            )

        return ChannelProfile(
            channel_id=channel_id,
            name=channel_id,
            handle=f"@{channel_id}",
            brand_description=f"Automated video channel for {channel_id}",
            visual_style=VisualStyleReference(
                style_name="Universal Studio 3D",
                style_prompt="High quality 3D animation, natural cinematic lighting, rich textures, balanced composition",
            ),
        )

