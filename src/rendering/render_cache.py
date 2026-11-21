"""Incremental rendering cache manager (Task 3.14).

Provides:
- Content-based fingerprinting for scenes (input SHA-256 + duration + geometry + fit mode + subtitles)
- Persistent caching of normalized scene segments in STUDIO_DATA_DIR/cache/
- Cache hit retrieval avoiding redundant FFmpeg re-encoding
- Atomic caching to prevent partial or corrupted cache entries
"""

import os
import shutil
import hashlib
from pathlib import Path
from typing import Optional, Union, Any, Dict

from src.core.config import get_settings
from src.core.logger import get_logger
from src.rendering.profiles import RenderProfile

logger = get_logger("studio.rendering.cache")


class RenderCacheManager:
    """Manages cached intermediate video renders for incremental rendering."""

    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        if cache_dir:
            self.cache_dir = Path(cache_dir).resolve()
        else:
            settings = get_settings()
            base = settings.studio_data_dir or settings.workspace_root
            self.cache_dir = base / "cache" / "render_segments"

        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def compute_fingerprint(
        self,
        asset_sha256: str,
        duration_sec: float,
        profile: Any,
        aspect_mode: str = "contain",
        subtitle_text: str = "",
        transition: str = "cut",
    ) -> str:
        """Calculate a deterministic content hash for a normalized scene segment."""
        width = profile.get("width", 1080) if isinstance(profile, dict) else getattr(profile, "width", 1080)
        height = profile.get("height", 1920) if isinstance(profile, dict) else getattr(profile, "height", 1920)
        fps = profile.get("fps", 30) if isinstance(profile, dict) else getattr(profile, "fps", 30)
        pix_fmt = profile.get("pix_fmt", "yuv420p") if isinstance(profile, dict) else getattr(profile, "pix_fmt", "yuv420p")
        video_codec = profile.get("video_codec", "libx264") if isinstance(profile, dict) else getattr(profile, "video_codec", "libx264")

        tokens = [
            asset_sha256.strip(),
            f"{duration_sec:.3f}",
            str(width),
            str(height),
            f"{float(fps):.2f}",
            str(pix_fmt),
            str(video_codec),
            aspect_mode.strip().lower(),
            transition.strip().lower(),
            subtitle_text.strip(),
        ]
        hasher = hashlib.sha256()
        hasher.update(":".join(tokens).encode("utf-8"))
        return hasher.hexdigest()

    def compute_scene_fingerprint(
        self,
        item: Any,
        profile: Any,
        aspect_mode: str = "contain",
    ) -> str:
        """Compute fingerprint for a SceneRenderItem."""
        asset_sha = getattr(item, "asset_sha256", None)
        if not asset_sha and getattr(item, "video_path", None) and Path(item.video_path).exists():
            st = Path(item.video_path).stat()
            asset_sha = f"{item.video_path}:{st.st_mtime}:{st.st_size}"
        elif not asset_sha:
            asset_sha = getattr(item, "scene_id", "scene")

        return self.compute_fingerprint(
            asset_sha256=asset_sha,
            duration_sec=getattr(item, "duration", 0.0),
            profile=profile,
            aspect_mode=aspect_mode,
            subtitle_text=getattr(item, "subtitle_text", "") or "",
            transition=getattr(item, "transition_out", "cut"),
        )

    def get_cached_scene(
        self,
        item: Any,
        profile: Any,
        aspect_mode: str = "contain",
    ) -> Optional[Path]:
        """Look up cached segment for a scene item."""
        fp = self.compute_scene_fingerprint(item, profile, aspect_mode)
        return self.get_cached_segment(fp)

    def store_cached_scene(
        self,
        item: Any,
        profile: Any,
        rendered_file: Path,
        aspect_mode: str = "contain",
    ) -> Path:
        """Store rendered segment for a scene item."""
        fp = self.compute_scene_fingerprint(item, profile, aspect_mode)
        return self.store_cached_segment(fp, rendered_file)

    def get_cached_segment(self, fingerprint: str) -> Optional[Path]:
        """Retrieve path to a cached normalized segment if it exists and is non-empty."""
        candidate = self.cache_dir / f"{fingerprint}.mp4"
        if candidate.is_file() and candidate.stat().st_size > 0:
            logger.debug(f"Render cache HIT for fingerprint '{fingerprint[:12]}...'.")
            return candidate
        return None

    def store_cached_segment(self, fingerprint: str, source_file: Path) -> Path:
        """Store a rendered normalized segment into cache atomically."""
        source = Path(source_file).resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Source file for caching does not exist: {source}")

        target = self.cache_dir / f"{fingerprint}.mp4"
        temp_target = self.cache_dir / f"{fingerprint}.tmp.{os.getpid()}.mp4"

        shutil.copy2(source, temp_target)
        os.replace(temp_target, target)

        logger.debug(f"Stored segment in render cache with fingerprint '{fingerprint[:12]}...'.")
        return target
