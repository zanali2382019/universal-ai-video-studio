"""Technical media validator for Google Flow Inbound Integration (Task 3.13).

Provides:
- Streaming SHA-256 checksum calculation
- ffprobe media inspection for video and audio (container, streams, duration, resolution, fps)
- Pillow inspection for images (format, dimensions, validity)
- Graceful error handling for corrupted or invalid files
"""

import hashlib
from pathlib import Path
from typing import Optional, Union
from PIL import Image

from src.core.logger import get_logger
from src.core.exceptions import FFprobeException
from src.rendering.probe_service import MediaProbeService
from src.flow.inbound.schemas import MediaValidationResult

logger = get_logger("studio.flow.inbound.validator")

VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".avi"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".aac", ".flac", ".m4a"}


def compute_streaming_sha256(file_path: Union[str, Path], chunk_size: int = 65536) -> str:
    """Compute SHA-256 checksum in streaming chunks."""
    p = Path(file_path)
    hasher = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


class MediaValidator:
    """Technical validator for incoming Google Flow media assets."""

    def __init__(self, probe_service: Optional[MediaProbeService] = None) -> None:
        self._probe = probe_service or MediaProbeService()

    def validate_file(self, file_path: Union[str, Path]) -> MediaValidationResult:
        """Validate technical integrity and extract parameters of a media file.
        
        Args:
            file_path: Path to target media file.
            
        Returns:
            MediaValidationResult: Validation outcome and technical metadata.
        """
        p = Path(file_path).resolve()
        if not p.is_file():
            return MediaValidationResult(
                is_valid=False,
                media_type="unknown",
                sha256="",
                file_size_bytes=0,
                error_message=f"File not found: {p}",
            )

        file_size = p.stat().st_size
        if file_size == 0:
            return MediaValidationResult(
                is_valid=False,
                media_type="unknown",
                sha256=hashlib.sha256(b"").hexdigest(),
                file_size_bytes=0,
                error_message=f"Empty file (0 bytes): {p.name}",
            )

        try:
            sha256_hash = compute_streaming_sha256(p)
        except Exception as e:
            return MediaValidationResult(
                is_valid=False,
                media_type="unknown",
                sha256="",
                file_size_bytes=file_size,
                error_message=f"Failed to read file for hash: {e}",
            )

        ext = p.suffix.lower()

        # 1. Image validation via Pillow
        if ext in IMAGE_EXTENSIONS:
            return self._validate_image(p, sha256_hash, file_size)

        # 2. Video validation via ffprobe
        if ext in VIDEO_EXTENSIONS:
            return self._validate_video(p, sha256_hash, file_size)

        # 3. Audio validation via ffprobe
        if ext in AUDIO_EXTENSIONS:
            return self._validate_audio(p, sha256_hash, file_size)

        # 4. Unknown extension: attempt probe first, then fallback
        try:
            return self._validate_video(p, sha256_hash, file_size)
        except Exception:
            return MediaValidationResult(
                is_valid=False,
                media_type="unknown",
                sha256=sha256_hash,
                file_size_bytes=file_size,
                error_message=f"Unsupported media format or unknown extension: '{ext}'",
            )

    def _validate_image(self, path: Path, sha256: str, size: int) -> MediaValidationResult:
        """Validate an image using Pillow."""
        try:
            with Image.open(path) as img:
                img.verify()
            
            # Reopen to read dimensions and format
            with Image.open(path) as img:
                width, height = img.size
                fmt = img.format

            return MediaValidationResult(
                is_valid=True,
                media_type="image",
                format_name=fmt,
                sha256=sha256,
                file_size_bytes=size,
                width=width,
                height=height,
                duration_sec=0.0,
            )
        except Exception as e:
            logger.warning(f"Image validation failed for '{path.name}': {e}")
            return MediaValidationResult(
                is_valid=False,
                media_type="image",
                sha256=sha256,
                file_size_bytes=size,
                error_message=f"Corrupt or unreadable image file: {e}",
            )

    def _validate_video(self, path: Path, sha256: str, size: int) -> MediaValidationResult:
        """Validate video container and streams using ffprobe."""
        try:
            probe = self._probe.probe_file(path)
            if not probe.has_video:
                return MediaValidationResult(
                    is_valid=False,
                    media_type="video",
                    sha256=sha256,
                    file_size_bytes=size,
                    error_message=f"No video streams found in file: {path.name}",
                )

            v_stream = probe.primary_video_stream
            return MediaValidationResult(
                is_valid=True,
                media_type="video",
                format_name=probe.format_name,
                sha256=sha256,
                file_size_bytes=size,
                duration_sec=probe.duration_sec,
                width=v_stream.width if v_stream else None,
                height=v_stream.height if v_stream else None,
                fps=v_stream.fps if v_stream else None,
            )
        except (FFprobeException, Exception) as e:
            logger.warning(f"Video validation probe failed for '{path.name}': {e}")
            return MediaValidationResult(
                is_valid=False,
                media_type="video",
                sha256=sha256,
                file_size_bytes=size,
                error_message=f"Corrupt or unprobeable video file: {e}",
            )

    def _validate_audio(self, path: Path, sha256: str, size: int) -> MediaValidationResult:
        """Validate audio container and streams using ffprobe."""
        try:
            probe = self._probe.probe_file(path)
            if not probe.has_audio:
                return MediaValidationResult(
                    is_valid=False,
                    media_type="audio",
                    sha256=sha256,
                    file_size_bytes=size,
                    error_message=f"No audio streams found in file: {path.name}",
                )

            a_stream = probe.primary_audio_stream
            return MediaValidationResult(
                is_valid=True,
                media_type="audio",
                format_name=probe.format_name,
                sha256=sha256,
                file_size_bytes=size,
                duration_sec=probe.duration_sec,
                sample_rate=a_stream.sample_rate if a_stream else None,
                channels=a_stream.channels if a_stream else None,
            )
        except (FFprobeException, Exception) as e:
            logger.warning(f"Audio validation probe failed for '{path.name}': {e}")
            return MediaValidationResult(
                is_valid=False,
                media_type="audio",
                sha256=sha256,
                file_size_bytes=size,
                error_message=f"Corrupt or unprobeable audio file: {e}",
            )

