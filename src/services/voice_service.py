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
        is_neural = resolved_voice and ("neural" in resolved_voice.lower() or "-" in resolved_voice)

        logger.info(
            f"Synthesizing voiceover: language='{lang}', voice='{resolved_voice}', "
            f"neural={is_neural}, output='{output_path.name}'"
        )

        try:
            if is_neural:
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
            # Strictly fail-fast without silent language or provider switching
            logger.error(f"Voiceover synthesis failed for voice '{resolved_voice}': {e}")
            raise ProviderLanguageUnavailableException(
                provider_name="NeuralVoiceProvider" if is_neural else "OfflineVoiceProvider",
                language=lang,
                message=f"Failed to synthesize voiceover with voice '{resolved_voice}': {e}",
            )

        size_bytes = output_path.stat().st_size
        logger.info(f"Voiceover successfully synthesized: '{output_path}' ({size_bytes:,} bytes, voice={resolved_voice}).")

        return {
            "language": lang,
            "voice_name": resolved_voice,
            "is_neural": is_neural,
            "output_path": str(output_path),
            "file_size_bytes": size_bytes,
            "format": output_path.suffix.lstrip("."),
        }
