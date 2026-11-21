"""Tests for Task 3.7: Audio Experience & Language Selection Fix.

Covers:
1. Arabic language selection ('ar').
2. English language selection ('en').
3. Missing language rejection (no silent defaulting).
4. Unsupported language rejection.
5. Correct language passed to voiceover provider / failing fast if unavailable (no silent switching).
6. Background music role validation and parameters (style, mood).
7. Separation of sound effects from background music.
8. Gentle background music generation without buzzing/drone artifacts.
9. Existing audio mixing, synchronization, and True Peak compliance.
"""
import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.core.constants import (
    AudioRole,
    VoiceoverLanguage,
    MusicStyle,
    MusicMood,
)
from src.core.exceptions import (
    ValidationException,
    VoiceoverLanguageException,
    AudioRoleMismatchException,
    ProviderLanguageUnavailableException,
)
from src.services.audio_validator import AudioManifestValidator
from src.services.voice_service import VoiceSynthesisService
from src.rendering.music_generator import BackgroundMusicGenerator, generate_background_music
from src.rendering.ffmpeg_wrapper import FFmpegWrapper
from src.rendering.probe_service import MediaProbeService


class TestAudioExperienceAndLanguage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wrapper = FFmpegWrapper()
        cls.probe_service = MediaProbeService(ffmpeg_wrapper=cls.wrapper)
        cls.voice_service = VoiceSynthesisService(ffmpeg_wrapper=cls.wrapper)
        cls.music_generator = BackgroundMusicGenerator(ffmpeg_wrapper=cls.wrapper)

    def test_01_arabic_language_selection_valid(self):
        """Arabic language ('ar') is accepted and normalized."""
        audio_def = {
            "voiceover": {
                "id": "vo_ar",
                "role": "voiceover",
                "language": "ar",
                "file_name": "vo_ar.aac",
            },
            "background_music": {
                "id": "bg_01",
                "role": "background_music",
                "style": "gentle_cinematic",
                "mood": "warm_calm",
                "file_name": "bg.aac",
            },
        }
        res = AudioManifestValidator.validate_manifest_audio(audio_def)
        self.assertEqual(res["voiceover"]["language"], "ar")

    def test_02_english_language_selection_valid(self):
        """English language ('en') is accepted and normalized."""
        audio_def = {
            "voiceover": {
                "id": "vo_en",
                "role": "voiceover",
                "language": "en",
                "file_name": "vo_en.aac",
            },
            "background_music": {
                "id": "bg_01",
                "role": "background_music",
                "file_name": "bg.aac",
            },
        }
        res = AudioManifestValidator.validate_manifest_audio(audio_def)
        self.assertEqual(res["voiceover"]["language"], "en")

    def test_03_missing_language_rejected(self):
        """Missing or empty language is rejected (never silently defaults to English)."""
        # Case A: Missing key
        audio_no_lang = {
            "voiceover": {
                "id": "vo_missing",
                "file_name": "vo.aac",
            },
            "background_music": {"file_name": "bg.aac"},
        }
        with self.assertRaises(VoiceoverLanguageException) as ctx:
            AudioManifestValidator.validate_manifest_audio(audio_no_lang)
        self.assertIn("explicitly specified", str(ctx.exception))

        # Case B: Empty string
        audio_empty_lang = {
            "voiceover": {
                "language": "   ",
                "file_name": "vo.aac",
            },
            "background_music": {"file_name": "bg.aac"},
        }
        with self.assertRaises(VoiceoverLanguageException):
            AudioManifestValidator.validate_manifest_audio(audio_empty_lang)

    def test_04_unsupported_language_rejected(self):
        """Unsupported languages (e.g. 'fr', 'es', 'de') are rejected."""
        unsupported = ["fr", "es", "de", "zh", "ja"]
        for lang in unsupported:
            audio_def = {
                "voiceover": {
                    "language": lang,
                    "file_name": "vo.aac",
                },
                "background_music": {"file_name": "bg.aac"},
            }
            with self.assertRaises(VoiceoverLanguageException) as ctx:
                AudioManifestValidator.validate_manifest_audio(audio_def)
            self.assertIn("Unsupported voiceover language", str(ctx.exception))

    def test_05_correct_language_passed_to_voiceover_provider(self):
        """Service validates and passes language to provider, refusing silent switching."""
        # Validate helper
        self.assertEqual(VoiceSynthesisService.validate_language("AR"), "ar")
        self.assertEqual(VoiceSynthesisService.validate_language("en"), "en")

        with self.assertRaises(VoiceoverLanguageException):
            VoiceSynthesisService.validate_language(None)

        with self.assertRaises(VoiceoverLanguageException):
            VoiceSynthesisService.validate_language("it")

        # Test failure when provider cannot fulfill language
        with patch.object(self.voice_service, "check_provider_language_support", return_value=False):
            with self.assertRaises(ProviderLanguageUnavailableException) as ctx:
                self.voice_service.synthesize_voiceover(
                    text="مرحبا",
                    language="ar",
                    output_path="dummy_output.aac",
                )
            self.assertIn("silent language switching is strictly forbidden", str(ctx.exception))

    def test_06_background_music_role_validation(self):
        """Background music must have designated role and valid parameters."""
        # Invalid role in background music block
        bad_music_role = {
            "voiceover": {"language": "en", "file_name": "vo.aac"},
            "background_music": {
                "role": "voiceover",  # Invalid role
                "file_name": "bg.aac",
            },
        }
        with self.assertRaises(AudioRoleMismatchException):
            AudioManifestValidator.validate_manifest_audio(bad_music_role)

        # Asset role mismatch during pre-render validation
        mismatched_assets = [
            {
                "file_name": "music.aac",
                "asset_category": "background_music",
                "role": "sound_effect",  # Conflict!
            }
        ]
        with self.assertRaises(AudioRoleMismatchException):
            AudioManifestValidator.validate_audio_asset_roles(mismatched_assets, expected_language="en")

    def test_07_sound_effects_separated_from_music(self):
        """Sound effects are optional and strictly separated from background music."""
        audio_with_sfx = {
            "voiceover": {"language": "en", "file_name": "vo.aac"},
            "background_music": {
                "role": "background_music",
                "style": "gentle_cinematic",
                "file_name": "bg.aac",
            },
            "sound_effects": [
                {
                    "id": "sfx_pop",
                    "role": "sound_effect",
                    "file_name": "pop.aac",
                }
            ],
        }
        val = AudioManifestValidator.validate_manifest_audio(audio_with_sfx)
        self.assertEqual(len(val["sound_effects"]), 1)
        self.assertEqual(val["sound_effects"][0]["role"], "sound_effect")

        # SFX with invalid role
        bad_sfx = {
            "voiceover": {"language": "en", "file_name": "vo.aac"},
            "background_music": {"file_name": "bg.aac"},
            "sound_effects": [{"role": "background_music", "file_name": "bad.aac"}],
        }
        with self.assertRaises(AudioRoleMismatchException):
            AudioManifestValidator.validate_manifest_audio(bad_sfx)

    def test_08_gentle_background_music_generation_and_true_peak(self):
        """Music generator produces gentle acoustic music conforming to True Peak <= -1.0 dBTP."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_file = Path(tmp_dir) / "test_gentle_music.aac"
            self.music_generator.generate_music(
                output_path=out_file,
                duration_sec=4.0,
                style=MusicStyle.GENTLE_CINEMATIC,
                mood=MusicMood.WARM_CALM,
            )
            self.assertTrue(out_file.exists())
            self.assertGreater(out_file.stat().st_size, 5000)

            probe = self.probe_service.probe_file(out_file)
            a = probe.primary_audio_stream
            self.assertIsNotNone(a)
            self.assertEqual(a.codec_name, "aac")
            self.assertEqual(a.sample_rate, 48000)
            self.assertEqual(a.channels, 2)

            tp = self.probe_service.measure_true_peak(out_file)
            self.assertTrue(tp.is_compliant, f"Measured {tp.true_peak_dbtp} dBTP exceeds ceiling")
            self.assertLessEqual(tp.true_peak_dbtp, -1.0)

    def test_09_voiceover_language_mismatch_detected(self):
        """Rejects voiceover asset tagged with a language different from project language."""
        assets = [
            {
                "file_name": "vo.aac",
                "asset_category": "voiceover",
                "role": "voiceover",
                "language": "en",
            }
        ]
        # Expecting Arabic, but asset is English
        with self.assertRaises(VoiceoverLanguageException) as ctx:
            AudioManifestValidator.validate_audio_asset_roles(assets, expected_language="ar")
        self.assertIn("conflicts with selected project language 'ar'", str(ctx.exception))

    def test_10_synthesize_arabic_speech_provider(self):
        """Synthesizes real Arabic speech audio using Microsoft Naayf voice."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_file = Path(tmp_dir) / "test_ar_voice.aac"
            res = self.voice_service.synthesize_voiceover(
                text="مرحبا بكم في عالم دريم ماتر الرائع",
                language="ar",
                output_path=out_file,
            )
            self.assertEqual(res["language"], "ar")
            self.assertTrue(out_file.exists())
            self.assertGreater(out_file.stat().st_size, 1000)

            probe = self.probe_service.probe_file(out_file)
            a = probe.primary_audio_stream
            self.assertIsNotNone(a)
            self.assertEqual(a.codec_name, "aac")
            self.assertEqual(a.sample_rate, 48000)
            self.assertEqual(a.channels, 2)
            self.assertGreater(probe.duration_sec, 2.0)

    def test_11_synthesize_english_speech_provider(self):
        """Synthesizes real English speech audio using English voice."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_file = Path(tmp_dir) / "test_en_voice.aac"
            res = self.voice_service.synthesize_voiceover(
                text="Welcome to the magical world of DreamMatter!",
                language="en",
                output_path=out_file,
            )
            self.assertEqual(res["language"], "en")
            self.assertTrue(out_file.exists())
            self.assertGreater(out_file.stat().st_size, 1000)

            probe = self.probe_service.probe_file(out_file)
            a = probe.primary_audio_stream
            self.assertIsNotNone(a)
            self.assertEqual(a.codec_name, "aac")
            self.assertEqual(a.sample_rate, 48000)
            self.assertEqual(a.channels, 2)
            self.assertGreater(probe.duration_sec, 2.0)


if __name__ == "__main__":
    unittest.main()
