"""CharacterBibleService: Authoritative repository for character identity, outfits, and consistency (Task 3.12)."""

import json
from pathlib import Path
from typing import Optional, List, Dict, Any

from src.core.config import get_settings
from src.core.logger import get_logger
from src.storage.service import StorageService
from src.creative.schemas import CharacterBibleEntry, OutfitProfile

logger = get_logger("studio.creative.character_bible_service")


class CharacterBibleService:
    """Service managing Character Bible entries, outfit profiles, and consistency rules."""

    def __init__(self, storage_service: Optional[StorageService] = None):
        self.storage_service = storage_service or StorageService()
        self.settings = get_settings()

    def get_character(
        self, character_id: str, channel_id: Optional[str] = None
    ) -> Optional[CharacterBibleEntry]:
        """Retrieve a character entry by ID, searching channel directory or built-in defaults."""
        clean_char = character_id.strip()

        # 1. Check channel-specific directory if channel_id provided
        if channel_id:
            key = f"channels/{channel_id.strip()}/characters/{clean_char}.json"
            if self.storage_service.exists(key):
                try:
                    data = json.loads(self.storage_service.load_bytes(key).decode("utf-8"))
                    logger.debug(f"Loaded character '{clean_char}' from channel '{channel_id}'.")
                    return CharacterBibleEntry(**data)
                except Exception as e:
                    logger.warning(f"Error loading character '{clean_char}' from '{key}': {e}")

        # 2. Check local filesystem path in studio data dir
        local_path = (self.settings.channels_root / (channel_id or "ch_dreammatter_001") / "characters" / f"{clean_char}.json")
        if local_path.is_file():
            try:
                with open(local_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return CharacterBibleEntry(**data)
            except Exception as e:
                logger.warning(f"Error reading character file '{local_path}': {e}")

        # 3. Built-in fallback definitions (Tommy & Zara)
        return self._get_builtin_character(clean_char)

    def list_characters(self, channel_id: Optional[str] = None) -> List[CharacterBibleEntry]:
        """List all characters registered for a channel or known to the studio."""
        chars: Dict[str, CharacterBibleEntry] = {}

        # Search channel characters directory
        ch = channel_id or "ch_dreammatter_001"
        char_dir = self.settings.channels_root / ch / "characters"
        if char_dir.is_dir():
            for json_file in char_dir.glob("*.json"):
                try:
                    with open(json_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        entry = CharacterBibleEntry(**data)
                        chars[entry.character_id] = entry
                except Exception as e:
                    logger.warning(f"Failed to load character file '{json_file}': {e}")

        # Ensure core built-ins are always present
        for builtin_id in ["char_tommy", "char_zara"]:
            if builtin_id not in chars:
                builtin_char = self._get_builtin_character(builtin_id)
                if builtin_char:
                    chars[builtin_id] = builtin_char

        return list(chars.values())

    def save_character(self, character: CharacterBibleEntry, channel_id: str) -> None:
        """Persist a character entry into the designated channel's character vault."""
        clean_ch = channel_id.strip()
        clean_char = character.character_id.strip()
        key = f"channels/{clean_ch}/characters/{clean_char}.json"
        json_bytes = character.model_dump_json(indent=2).encode("utf-8")

        self.storage_service.save_file(
            key=key,
            data=json_bytes,
            mime_type="application/json",
            custom_metadata={"character_id": clean_char, "channel_id": clean_ch},
        )
        logger.info(f"Saved character '{clean_char}' for channel '{clean_ch}' at '{key}'.")

    def _get_builtin_character(self, character_id: str) -> Optional[CharacterBibleEntry]:
        """Built-in baseline characters for DreamMatter franchise."""
        if character_id in ("char_tommy", "tommy"):
            return CharacterBibleEntry(
                character_id="char_tommy",
                character_name="Tommy",
                identity_description="Curious, cheerful 5-year-old toddler exploring whimsical everyday miracles.",
                status="active",
                immutable_attributes={
                    "age": 5,
                    "gender": "male",
                    "skin_tone": "warm fair with peach undertones",
                    "hair_color": "warm chocolate brown",
                    "hair_texture": "soft curly mop with unruly locks",
                    "eye_color": "sparkling warm chestnut brown",
                    "body_type": "chubby toddler proportions, 3 heads tall silhouette",
                },
                hair_and_face_description="unruly mop of bouncy brown curls, big warm expressive brown eyes, cute button nose, rosy chubby toddler cheeks, cheerful toothy smile",
                movement_characteristics="energetic bouncy toddler steps, spontaneous giggles, wide inquisitive gestures",
                expressions={
                    "happy": "beaming smile with bright eyes and rosy cheeks",
                    "curious": "head tilted sideways, wide inquisitive eyes, mouth slightly open in wonder",
                    "surprised": "wide round eyes, gasping mouth, hands raised to cheeks in delight",
                    "laughing": "scrunched joyful eyes, head thrown back in giggle, open-mouthed laughter",
                },
                outfit_profiles={
                    "default": OutfitProfile(
                        outfit_id="default",
                        name="Classic Overalls",
                        description="Tommy's signature everyday playwear",
                        top="bright lemon yellow short-sleeved cotton t-shirt with ribbed crew neck",
                        bottom="classic indigo blue denim overalls with bronze buckles",
                        footwear="bright red canvas low-top sneakers with white rubber toe caps",
                        signature_colors=["#FFD166", "#118AB2", "#EF476F"],
                        prompt_snippet="wearing bright lemon-yellow short-sleeve cotton t-shirt, classic indigo blue denim overalls with bronze buckles, and bright red canvas sneakers with white rubber toes",
                    ),
                    "gardening": OutfitProfile(
                        outfit_id="gardening",
                        name="Garden Explorer",
                        description="Attire for planting flowers and chasing butterflies",
                        top="light mint green cotton t-shirt",
                        bottom="khaki cargo shorts with deep side pockets",
                        footwear="bright yellow rubber rain boots",
                        signature_colors=["#06D6A0", "#F4A261", "#E76F51"],
                        prompt_snippet="wearing light mint-green t-shirt, khaki cargo shorts, bright yellow rubber rain boots, and tiny green gardening gloves",
                    ),
                },
                default_outfit_id="default",
                reference_images=["channels/ch_dreammatter_001/media_vault/originals/images/tommy_scene_1.jpg"],
                character_consistency_rules=[
                    "Always maintain 3D stylized cartoon toddler proportions (3 heads tall silhouette).",
                    "Never render photorealistic human skin texture, pores, or wrinkles.",
                    "Never omit Tommy's unruly bouncy brown curls or rosy peach cheeks.",
                ],
                missing_fields=[],
            )

        if character_id in ("char_zara", "zara"):
            return CharacterBibleEntry(
                character_id="char_zara",
                character_name="Zara",
                identity_description="Inquisitive, clever 6-year-old girl who loves exploring luminous magical phenomena.",
                status="partially_documented",
                immutable_attributes={
                    "age": 6,
                    "gender": "female",
                    "skin_tone": "warm honey golden with natural radiance",
                    "hair_color": "dark espresso brown [OBSERVED_TENTATIVE]",
                    "hair_texture": "playful space buns with loose front tendrils",
                    "eye_color": "sparkling amber honey eyes",
                    "body_type": "slender stylized 3D cartoon child silhouette, 3.5 heads tall",
                },
                hair_and_face_description="dark espresso curly hair styled in two playful bouncy space buns with wispy front tendrils, wide luminous amber eyes, bright friendly smile, and gentle star-dusted freckles",
                movement_characteristics="graceful nimble footsteps, curious tiptoeing, reaching out hands gently toward glowing lights",
                expressions={
                    "wonder": "wide sparkling amber eyes reflecting magical light, gentle awe-inspired smile",
                    "determined": "confident happy smile, tilted chin, bright intelligent gaze",
                },
                outfit_profiles={
                    "default": OutfitProfile(
                        outfit_id="default",
                        name="Wonders Explorer",
                        description="Zara's exploratory adventure vest and travel trousers",
                        top="lilac purple hoodie-vest over soft cream long-sleeve shirt",
                        bottom="teal rolled-up cotton adventure trousers",
                        footwear="lavender high-top sneakers with glowing pastel soles",
                        signature_colors=["#C77DFF", "#7209B7", "#4CC9F0"],
                        prompt_snippet="wearing lilac purple hoodie-vest over soft cream long-sleeve shirt, teal adventure trousers, lavender sneakers with glowing soles, and a small brass explorer telescope",
                    )
                },
                default_outfit_id="default",
                reference_images=[],
                character_consistency_rules=[
                    "Maintain 3D Pixar/DreamWorks stylized child aesthetic.",
                    "Ensure amber eye reflections match the magical glowing light sources in the scene.",
                ],
                missing_fields=[
                    "[PENDING_OWNER_INPUT: Reference portrait image asset in media_vault]",
                    "[PENDING_OWNER_INPUT: Confirmation of canonical outfit fabrics and accessories]",
                    "[PENDING_OWNER_INPUT: Official voice talent selection]",
                ],
                notes="Documented from Channel Audit 'Zara and the Wonders Spark'. Awaiting official high-res character sheet from channel owner.",
            )

        return None

