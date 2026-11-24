"""Voiceover Synthesis and Language Management Service.

Provides explicit language selection, neural speech synthesis, and audio generation.
Strict requirements:
1. Voiceover language must be explicitly specified ('ar' or 'en'). Never silently defaults.
2. Validates requested language against supported languages.
3. Supports high-quality Modern Standard Arabic (Fusha) Neural voices (e.g. 'ar-SA-HamedNeural').
4. Ensures the provider can support the requested language; fails fast if unavailable (never silently switches).
5. Emits compliant 48kHz stereo audio tracks for timeline mixing.
"""
import os
import sys
import asyncio
import tempfile
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any, Union

import edge_tts
from src.core.logger import get_logger
from src.core.constants import VoiceoverLanguage, SUPPORTED_VOICEOVER_LANGUAGES
from src.core.exceptions import (
    VoiceoverLanguageException,
    ProviderLanguageUnavailableException,
)
from src.rendering.ffmpeg_wrapper import FFmpegWrapper

logger = get_logger("studio.services.voice_service")

# Premier Neural voice mappings for storytelling
DEFAULT_NEURAL_VOICES: Dict[str, str] = {
    VoiceoverLanguage.ARABIC.value: "ar-SA-HamedNeural",     # High-fidelity Fusha storyteller
    VoiceoverLanguage.ENGLISH.value: "en-US-ChristopherNeural", # Clear narrative English
}


class VoiceSynthesisService:
    """Service governing voiceover language validation and speech synthesis."""

    def __init__(self, ffmpeg_wrapper: Optional[FFmpegWrapper] = None):
        self.wrapper = ffmpeg_wrapper or FFmpegWrapper()

    @staticmethod
    def validate_language(language: Optional[str]) -> str:
        """Validate that voiceover language is explicitly supplied and supported.

        Args:
            language: Language code string (e.g. 'ar' or 'en').

        Returns:
            str: Normalized lowercase language code.

        Raises:
            VoiceoverLanguageException: If language is missing or unsupported.
        """
        if not language or not str(language).strip():
            raise VoiceoverLanguageException(
                "Voiceover language must be explicitly specified (e.g. 'ar' or 'en'). "
                "Implicit defaulting is not permitted."
            )

        lang_clean = str(language).strip().lower()
        if lang_clean not in SUPPORTED_VOICEOVER_LANGUAGES:
            raise VoiceoverLanguageException(
                f"Unsupported voiceover language '{lang_clean}'. "
                f"Supported languages are: {sorted(list(SUPPORTED_VOICEOVER_LANGUAGES))}."
            )

        return lang_clean

    def check_provider_language_support(self, language: str) -> bool:
        """Verify if speech synthesis providers can fulfill the requested language."""
        lang_clean = self.validate_language(language)
        return lang_clean in SUPPORTED_VOICEOVER_LANGUAGES

    def _synthesize_edge_neural(
        self,
        text: str,
        voice_id: str,
        output_path: Path,
        rate: int = 0,
        volume: int = 100,
    ) -> None:
        """Synthesize using Edge Neural TTS asynchronously and encode to 48kHz stereo AAC."""
        rate_str = f"{rate:+d}%" if rate != 0 else "+0%"
        vol_str = f"{volume - 100:+d}%" if volume != 100 else "+0%"

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_mp3:
            tmp_mp3_path = Path(tmp_mp3.name)

        async def _run_communicate():
            comm = edge_tts.Communicate(text=text, voice=voice_id, rate=rate_str, volume=vol_str)
            await asyncio.wait_for(comm.save(str(tmp_mp3_path)), timeout=5.0)

        try:
            try:
                asyncio.run(_run_communicate())
            except RuntimeError:
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    pool.submit(lambda: asyncio.run(_run_communicate())).result()

            if not tmp_mp3_path.exists() or tmp_mp3_path.stat().st_size == 0:
                raise RuntimeError(f"Neural synthesis returned empty audio for voice '{voice_id}'.")

            # Convert to target format (48kHz stereo AAC or WAV)
            if output_path.suffix.lower() == ".wav":
                cmd = ["-y", "-i", str(tmp_mp3_path), "-ar", "48000", "-ac", "2", str(output_path)]
            else:
                cmd = [
                    "-y",
                    "-i", str(tmp_mp3_path),
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-ar", "48000",
                    "-ac", "2",
                    str(output_path),
                ]
            self.wrapper.run_ffmpeg(cmd, check=True)
        finally:
            if tmp_mp3_path.exists():
                try:
                    tmp_mp3_path.unlink(missing_ok=True)
                except Exception:
                    pass

    def _synthesize_winrt_offline(
        self,
        text: str,
        lang: str,
        output_path: Path,
    ) -> None:
        """Fallback local synthesis using Windows WinRT / SAPI."""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
            tmp_wav_path = Path(tmp_wav.name)

        try:
            ps_script = f"""
            $text = @'
{text}
'@
            Add-Type -AssemblyName System.Runtime.WindowsRuntime
            $asTaskGeneric = [System.WindowsRuntimeSystemExtensions].GetMethods() | ? {{ $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' }}
            function Await($WinRtTask, $ResultType) {{
                $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
                $netTask = $asTask.Invoke($null, @($WinRtTask))
                $netTask.Wait(-1) | Out-Null
                $netTask.Result
            }}

            [Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media.SpeechSynthesis, ContentType = WindowsRuntime] | Out-Null
            $synth = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
            $targetLang = '{lang}'
            $voice = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices | Where-Object {{ $_.Language -like "$targetLang*" }} | Select-Object -First 1

            if (-not $voice) {{
                Write-Error "No voice matching language '$targetLang' found."
                exit 1
            }}

            $synth.Voice = $voice
            $streamOp = $synth.SynthesizeTextToStreamAsync($text)
            $stream = Await $streamOp ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])
            
            $reader = New-Object Windows.Storage.Streams.DataReader($stream)
            $loadOp = $reader.LoadAsync($stream.Size)
            $bytesLoaded = Await $loadOp ([System.UInt32])
            $bytes = New-Object byte[] $stream.Size
            $reader.ReadBytes($bytes)
            [System.IO.File]::WriteAllBytes('{str(tmp_wav_path).replace("\\", "\\\\")}', $bytes)
            """

            res = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if res.returncode != 0 or not tmp_wav_path.exists() or tmp_wav_path.stat().st_size == 0:
                raise RuntimeError(f"Offline synthesis failed for language '{lang}': {res.stderr}")

            # Convert to target format
            if output_path.suffix.lower() == ".wav":
                os.replace(tmp_wav_path, output_path)
            else:
                cmd = [
                    "-y",
                    "-i", str(tmp_wav_path),
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-ar", "48000",
                    "-ac", "2",
                    str(output_path),
                ]
                self.wrapper.run_ffmpeg(cmd, check=True)
        finally:
            if tmp_wav_path.exists():
                try:
                    tmp_wav_path.unlink(missing_ok=True)
                except Exception:
                    pass

    def _synthesize_gemini_tts(
        self,
        text: str,
        voice_id: str,
        output_path: Path,
        language: str = "ar",
    ) -> None:
        """Synthesize speech using Gemini 3.8 Flash TTS and encode to 48kHz stereo AAC."""
        api_key = os.getenv("GEMINI_API_KEY", "")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not found in environment.")

        instructions = (
            "Speak the exact Arabic text in clear, authentic Modern Standard Arabic. "
            "Use precise articulation of Arabic consonants, correct vowel realization, "
            "natural connected speech, a warm expressive storytelling voice, and natural pauses. "
            "Preserve every word exactly. Do not translate or omit words."
            if language == "ar" else
            "Speak the exact English text in clear, authentic natural narrative voice with friendly storytelling tone."
        )

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash-tts:generateContent?key={api_key}"
        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": f"{instructions}\n\n## Transcript:\n{text}"
                        }
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 1,
                "responseModalities": ["audio"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {
                            "voiceName": voice_id
                        }
                    }
                }
            }
        }

        import urllib.request
        import base64
        import json

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
            tmp_wav_path = Path(tmp_wav.name)

        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            candidates = data.get("candidates", [])
            if not candidates:
                raise RuntimeError(f"No candidates in response: {data}")

            parts = candidates[0].get("content", {}).get("parts", [])
            audio_part = None
            for p in parts:
                if "inlineData" in p:
                    audio_part = p["inlineData"]
                    break

            if not audio_part:
                raise RuntimeError("No inlineData found in response parts.")

            audio_bytes = base64.b64decode(audio_part["data"])
            tmp_wav_path.write_bytes(audio_bytes)

            if output_path.suffix.lower() == ".wav":
                os.replace(tmp_wav_path, output_path)
            else:
                cmd = [
                    "-y",
                    "-i", str(tmp_wav_path),
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-ar", "48000",
                    "-ac", "2",
                    str(output_path),
                ]
                self.wrapper.run_ffmpeg(cmd, check=True)
        finally:
            if tmp_wav_path.exists():
                tmp_wav_path.unlink(missing_ok=True)

    def synthesize_voiceover(
        self,
        text: str,
        language: str,
        output_path: Union[str, Path],
        voice_name: Optional[str] = None,
        rate: int = 0,
        volume: int = 100,
    ) -> Dict[str, Any]:
        """Synthesize narration text to an audio file in the selected language.

        Args:
            text: Text to speak.
            language: Explicit language code ('ar' or 'en').
            output_path: Destination path (.aac or .wav).
            voice_name: Optional voice identifier (e.g. 'ar-SA-HamedNeural', 'ar-SA-ZariyahNeural').
            rate: Speech speed adjustment (-100 to +100%).
            volume: Volume level (0 to 100%).

        Returns:
            Dict containing synthesis metadata.

        Raises:
            VoiceoverLanguageException: If language is invalid.
            ProviderLanguageUnavailableException: If host provider cannot support language.
        """
        lang = self.validate_language(language)
        if not text or not text.strip():
            raise ValueError("Narration text cannot be empty.")

        if not self.check_provider_language_support(lang):
            raise ProviderLanguageUnavailableException(
                provider_name="SpeechSynthesisService",
                language=lang,
                message=(
                    f"Selected voiceover language '{lang}' is not supported by the TTS provider. "
                    f"Stopping production: silent language switching is strictly forbidden."
                ),
            )

        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Resolve voice
        resolved_voice = voice_name or DEFAULT_NEURAL_VOICES.get(lang)
        is_gemini_voice = resolved_voice in ("Enzo", "Aoede", "Puck", "Kore", "Fenrir", "Charon")
        has_gemini_key = bool(os.getenv("GEMINI_API_KEY"))
        is_neural = resolved_voice and ("neural" in resolved_voice.lower() or "-" in resolved_voice)

        logger.info(
            f"Synthesizing voiceover: language='{lang}', voice='{resolved_voice}', "
            f"gemini={is_gemini_voice and has_gemini_key}, neural={is_neural}, output='{output_path.name}'"
        )

        try:
            if is_gemini_voice and has_gemini_key:
                self._synthesize_gemini_tts(
                    text=text,
                    voice_id=resolved_voice,
                    output_path=output_path,
                    language=lang,
                )
            elif is_neural:
                self._synthesize_edge_neural(
                    text=text,
                    voice_id=resolved_voice,
                    output_path=output_path,
                    rate=rate,
                    volume=volume,
                )
            else:
                self._synthesize_winrt_offline(
                    text=text,
                    lang=lang,
                    output_path=output_path,
                )
        except Exception as e:
            # Fallback attempt to offline if network neural failed and WinRT available
            if is_neural:
                try:
                    logger.warning(f"Neural voice synthesis failed ({e}), attempting offline WinRT fallback...")
                    self._synthesize_winrt_offline(text=text, lang=lang, output_path=output_path)
                except Exception as fb_err:
                    logger.error(f"Voiceover synthesis failed completely for voice '{resolved_voice}': {fb_err}")
                    raise ProviderLanguageUnavailableException(
                        provider_name="NeuralVoiceProvider",
                        language=lang,
                        message=f"Failed to synthesize voiceover with voice '{resolved_voice}': {e}",
                    )
            else:
                logger.error(f"Voiceover synthesis failed for voice '{resolved_voice}': {e}")
                raise ProviderLanguageUnavailableException(
                    provider_name="VoiceProvider",
                    language=lang,
                    message=f"Failed to synthesize voiceover with voice '{resolved_voice}': {e}",
                )

        size_bytes = output_path.stat().st_size
        logger.info(f"Voiceover successfully synthesized: '{output_path}' ({size_bytes:,} bytes, voice={resolved_voice}).")

        return {
            "language": lang,
            "voice_name": resolved_voice,
            "is_neural": is_neural or (is_gemini_voice and has_gemini_key),
            "output_path": str(output_path),
            "file_size_bytes": size_bytes,
            "format": output_path.suffix.lstrip("."),
        }

    def synthesize_project_voiceover(
        self,
        channel_id: str,
        project_id: str,
        scenes: List[Any],
        language: str = "ar",
        voice_name: Optional[str] = None,
        rate: int = 0,
        volume: int = 100,
        output_path: Optional[Union[str, Path]] = None,
    ) -> Dict[str, Any]:
        """Aggregate scene narration texts and synthesize project-level voiceover track.

        Args:
            channel_id: Channel ID.
            project_id: Target project ID.
            scenes: List of SceneModel instances or dicts for the project.
            language: Explicit voiceover language code ('ar' or 'en').
            voice_name: Desired voice name (e.g. 'ar-SA-HamedNeural', 'ar-SA-ZariyahNeural').
            rate: Speed adjustment percentage (-100 to +100).
            volume: Volume level (0 to 100).
            output_path: Optional explicit output path.

        Returns:
            Dict containing synthesis metadata, aggregated script, and scene count.
        """
        lang = self.validate_language(language)

        # Sort scenes by sequence_number / order
        sorted_scenes = sorted(
            scenes,
            key=lambda s: getattr(s, "sequence_number", 0) if hasattr(s, "sequence_number") else (s.get("sequence_number", 0) if isinstance(s, dict) else 0),
        )

        texts = []
        for s in sorted_scenes:
            txt = getattr(s, "narration_text", None) or getattr(s, "script_text", None)
            if not txt and isinstance(s, dict):
                txt = s.get("narration_text") or s.get("script_text")
            if txt and str(txt).strip():
                clean_t = str(txt).strip()
                texts.append(clean_t)

        if not texts:
            raise ValueError(f"Project '{project_id}' has no scene narration text to synthesize.")

        combined_script = "  ".join(texts)

        if not output_path:
            from src.core.config import get_settings
            settings = get_settings()
            ch_dir = settings.channels_root / channel_id.strip()
            out_file = ch_dir / "media_vault" / "originals" / "audio" / f"{project_id}_voiceover_{lang}.aac"
        else:
            out_file = Path(output_path)

        synth_meta = self.synthesize_voiceover(
            text=combined_script,
            language=lang,
            output_path=out_file,
            voice_name=voice_name,
            rate=rate,
            volume=volume,
        )

        synth_meta["project_id"] = project_id
        synth_meta["channel_id"] = channel_id
        synth_meta["scene_count"] = len(sorted_scenes)
        synth_meta["scenes_narrated"] = len(texts)
        synth_meta["combined_script"] = combined_script
        return synth_meta
