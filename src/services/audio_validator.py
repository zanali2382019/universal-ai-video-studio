"""Audio Experience and Role Validation Service.

Enforces:
1. Explicit voiceover language selection ('ar' or 'en') without implicit defaulting.
2. Distinct audio roles: 'voiceover', 'background_music', and optional 'sound_effects'.
3. Prevention of role mixing (e.g., sound effects or voiceovers miscategorized as music).
4. Validation of music style and mood definitions for background music.
"""
from typing import Dict, Any, List, Optional

from src.core.logger import get_logger
from src.core.constants import (
    AudioRole,
    VoiceoverLanguage,
    SUPPORTED_VOICEOVER_LANGUAGES,
    MusicStyle,
    MusicMood,
)
from src.core.exceptions import (
    ValidationException,
    VoiceoverLanguageException,
    AudioRoleMismatchException,
)

logger = get_logger("studio.services.audio_validator")


class AudioManifestValidator:
    """Validates audio role separation, explicit language selection, and music parameters."""

    @staticmethod
    def validate_manifest_audio(audio_def: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Validate the 'audio' section of an episode manifest.

        Args:
            audio_def: Dictionary containing 'voiceover', 'background_music', and optional 'sound_effects'.

        Returns:
            Dict[str, Any]: Validated audio specification dictionary.

        Raises:
            VoiceoverLanguageException: If language is missing or unsupported.
            AudioRoleMismatchException: If roles are mixed or malformed.
            ValidationException: If mandatory fields are absent.
        """
        if not audio_def or not isinstance(audio_def, dict):
            raise ValidationException(
                "Episode manifest missing mandatory 'audio' specification block. "
                "Must supply 'voiceover' (with explicit language) and 'background_music'."
            )

        # -------------------------------------------------------------
        # 1. Voiceover Validation
        # -------------------------------------------------------------
        vo = audio_def.get("voiceover")
        if not vo or not isinstance(vo, dict):
            raise ValidationException(
                "Episode manifest audio configuration missing mandatory 'voiceover' block."
            )

        lang_raw = vo.get("language")
        if not lang_raw or not str(lang_raw).strip():
            raise VoiceoverLanguageException(
                "Voiceover language must be explicitly specified (e.g. 'ar' or 'en') in manifest['audio']['voiceover']['language']. "
                "Implicit defaulting to English is not permitted."
            )

        lang_clean = str(lang_raw).strip().lower()
        if lang_clean not in SUPPORTED_VOICEOVER_LANGUAGES:
            raise VoiceoverLanguageException(
                f"Unsupported voiceover language '{lang_clean}'. "
                f"Supported languages are: {sorted(list(SUPPORTED_VOICEOVER_LANGUAGES))}."
            )

        # -------------------------------------------------------------
        # 2. Background Music Validation
        # -------------------------------------------------------------
        bg = audio_def.get("background_music")
        if bg is not None:
            if not isinstance(bg, dict):
                raise AudioRoleMismatchException(
                    "Background music configuration must be a valid dictionary with 'style', 'mood', and 'file_name'."
                )

            # Ensure not designated as voiceover or sfx
            role = bg.get("role", AudioRole.BACKGROUND_MUSIC.value)
            if role != AudioRole.BACKGROUND_MUSIC.value:
                raise AudioRoleMismatchException(
                    f"Background music block cannot have role '{role}' (expected '{AudioRole.BACKGROUND_MUSIC.value}')."
                )

            # Check style and mood if provided
            style = bg.get("style", MusicStyle.GENTLE_CINEMATIC.value)
            mood = bg.get("mood", MusicMood.WARM_CALM.value)
            bg["style"] = style
            bg["mood"] = mood

        # -------------------------------------------------------------
        # 3. Sound Effects Validation (Optional)
        # -------------------------------------------------------------
        sfx = audio_def.get("sound_effects")
        if sfx is not None:
            if not isinstance(sfx, list):
                raise AudioRoleMismatchException(
                    "Sound effects block must be a list of individual effect definitions if provided."
                )
            for sfx_item in sfx:
                if not isinstance(sfx_item, dict):
                    raise AudioRoleMismatchException("Each sound effect entry must be a dictionary.")
                sfx_role = sfx_item.get("role", AudioRole.SOUND_EFFECT.value)
                if sfx_role != AudioRole.SOUND_EFFECT.value:
                    raise AudioRoleMismatchException(
                        f"Sound effect item cannot have role '{sfx_role}' (expected '{AudioRole.SOUND_EFFECT.value}')."
                    )

        logger.debug(f"Audio manifest validated successfully (language='{lang_clean}').")
        return audio_def

    @staticmethod
    def validate_audio_asset_roles(
        assets: List[Dict[str, Any]],
        expected_language: str,
    ) -> None:
        """Validate that all audio assets match their designated production roles and language.

        Args:
            assets: List of asset information dictionaries.
            expected_language: Required voiceover language code.

        Raises:
            AudioRoleMismatchException: If an asset is miscategorized or roles are swapped.
            VoiceoverLanguageException: If voiceover asset language conflicts with selected language.
        """
        for a in assets:
            category = a.get("asset_category", a.get("category", "general"))
            role = a.get("metadata", {}).get("role") or a.get("role") or category

            # Check Voiceover
            if category == AudioRole.VOICEOVER.value or role == AudioRole.VOICEOVER.value:
                if role != AudioRole.VOICEOVER.value:
                    raise AudioRoleMismatchException(
                        f"Asset '{a.get('file_name')}' has mismatched role '{role}' vs category '{category}'."
                    )
                # Verify language metadata if recorded
                ast_lang = a.get("metadata", {}).get("language") or a.get("language")
                if ast_lang and str(ast_lang).lower() != expected_language.lower():
                    raise VoiceoverLanguageException(
                        f"Voiceover asset '{a.get('file_name')}' is tagged with language '{ast_lang}', "
                        f"which conflicts with selected project language '{expected_language}'."
                    )

            # Check Background Music
            elif category == AudioRole.BACKGROUND_MUSIC.value or role == AudioRole.BACKGROUND_MUSIC.value:
                if role != AudioRole.BACKGROUND_MUSIC.value:
                    raise AudioRoleMismatchException(
                        f"Music asset '{a.get('file_name')}' has mismatched role '{role}' vs category '{category}'."
                    )

            # Check Sound Effects
            elif category == AudioRole.SOUND_EFFECT.value or role == AudioRole.SOUND_EFFECT.value:
                if role != AudioRole.SOUND_EFFECT.value:
                    raise AudioRoleMismatchException(
                        f"Sound effect asset '{a.get('file_name')}' has mismatched role '{role}' vs category '{category}'."
                    )
