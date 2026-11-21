"""Standard video rendering profiles and presets for multi-format video production."""
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class RenderProfile:
    """Immutable rendering profile specification defining output geometry, framerate, and codecs."""
    name: str
    width: int
    height: int
    fps: float = 30.0
    aspect_ratio: str = "9:16"
    video_codec: str = "libx264"
    pix_fmt: str = "yuv420p"
    preset: str = "fast"
    crf: int = 20
    audio_codec: str = "aac"
    audio_sample_rate: int = 48000
    audio_channels: int = 2
    audio_bitrate: str = "192k"
    description: str = ""

    @property
    def resolution_str(self) -> str:
        return f"{self.width}x{self.height}"

    def get_scaling_filter(self) -> str:
        """FFmpeg video filter string to scale and pad input while preserving aspect ratio."""
        return (
            f"scale={self.width}:{self.height}:force_original_aspect_ratio=decrease,"
            f"pad={self.width}:{self.height}:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1,"
            f"fps={self.fps},"
            f"format={self.pix_fmt}"
        )


# Predefined Standard Render Profiles
VERTICAL_9_16 = RenderProfile(
    name="vertical_9_16",
    width=1080,
    height=1920,
    fps=30.0,
    aspect_ratio="9:16",
    preset="fast",
    crf=20,
    description="Vertical 9:16 format optimized for YouTube Shorts, TikTok, and Instagram Reels (1080x1920 @ 30fps).",
)

HORIZONTAL_16_9 = RenderProfile(
    name="horizontal_16_9",
    width=1920,
    height=1080,
    fps=30.0,
    aspect_ratio="16:9",
    preset="fast",
    crf=20,
    description="Horizontal 16:9 format standard for YouTube and widescreen displays (1920x1080 @ 30fps).",
)

SQUARE_1_1 = RenderProfile(
    name="square_1_1",
    width=1080,
    height=1080,
    fps=30.0,
    aspect_ratio="1:1",
    preset="fast",
    crf=20,
    description="Square 1:1 format for Instagram feeds and social carousels (1080x1080 @ 30fps).",
)

_REGISTRY: Dict[str, RenderProfile] = {
    "vertical_9_16": VERTICAL_9_16,
    "9:16": VERTICAL_9_16,
    "vertical": VERTICAL_9_16,
    "horizontal_16_9": HORIZONTAL_16_9,
    "16:9": HORIZONTAL_16_9,
    "horizontal": HORIZONTAL_16_9,
    "square_1_1": SQUARE_1_1,
    "1:1": SQUARE_1_1,
    "square": SQUARE_1_1,
}


def get_profile(identifier: str) -> RenderProfile:
    """Retrieve standard rendering profile by name or aspect ratio key."""
    clean_id = (identifier or "").strip().lower()
    if clean_id in _REGISTRY:
        return _REGISTRY[clean_id]
    raise ValueError(
        f"Unknown render profile '{identifier}'. Available profiles: {list(_REGISTRY.keys())}"
    )

