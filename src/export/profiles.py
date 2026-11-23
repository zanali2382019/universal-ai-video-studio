"""Versioned, platform-specific export profiles for digital publishing (Task 3.16).

Implements Guardrail B:
- Independent profiles for YouTube Shorts, Instagram Reels, TikTok, and standard formats.
- Explicit parameters (resolution, fps, bitrate, audio sample rate, duration limits).
- Documentation of review dates and compliance disclaimers without unsubstantiated guarantees.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class ExportPlatform(str, Enum):
    """Supported digital publishing platforms."""
    YOUTUBE_SHORTS = "youtube_shorts"
    INSTAGRAM_REELS = "instagram_reels"
    TIKTOK = "tiktok"
    YOUTUBE_STANDARD = "youtube_standard"
    GENERIC_VERTICAL = "generic_vertical"


@dataclass(frozen=True)
class PlatformExportProfile:
    """Specification of target platform encoding parameters and constraints."""
    platform_id: str
    name: str
    version: str = "1.0.0"
    width: int = 1080
    height: int = 1920
    fps: float = 30.0
    aspect_ratio: str = "9:16"
    aspect_mode: str = "cover"
    video_codec: str = "libx264"
    pix_fmt: str = "yuv420p"
    video_bitrate: str = "10M"
    audio_codec: str = "aac"
    audio_sample_rate: int = 48000
    audio_channels: int = 2
    audio_bitrate: str = "192k"
    max_duration_sec: float = 60.0
    preferred_duration_sec: float = 60.0
    target_lufs: float = -14.0
    lufs_tolerance: float = 1.5
    true_peak_ceiling: float = -1.0
    thumbnail_width: int = 1080
    thumbnail_height: int = 1920
    last_reviewed: str = "2026-11-23"
    documentation_source: str = ""
    notes: str = ""
    disclaimer: str = (
        "Technical guidelines are based on platform documentation as of last review date. "
        "Acceptance and transcoding are governed by platform-side server ingest pipelines."
    )

    @property
    def true_peak_db(self) -> float:
        return self.true_peak_ceiling

    @property
    def profile_id(self) -> str:
        return self.platform_id

    @property
    def platform(self) -> ExportPlatform:
        try:
            return ExportPlatform(self.platform_id)
        except ValueError:
            return ExportPlatform.GENERIC_VERTICAL

    @property
    def name_en(self) -> str:
        return self.name

    @property
    def name_ar(self) -> str:
        translations = {
            "youtube_shorts": "يوتيوب شورتس (YouTube Shorts)",
            "instagram_reels": "إنستغرام ريلز (Instagram Reels)",
            "tiktok": "تيك توك (TikTok)",
            "youtube_standard": "يوتيوب قياسي 16:9 (YouTube Standard)",
            "generic_vertical": "فيديو رأسي عام (Generic 9:16)",
        }
        return translations.get(self.platform_id, self.name)

    @property
    def description_en(self) -> str:
        return self.notes or self.name

    @property
    def description_ar(self) -> str:
        translations = {
            "youtube_shorts": "مخصص لرف يوتيوب شورتس بحد تقني أقصى 180 ثانية (تحديث أكتوبر 2024) ومدة إنتاج موصى بها حتى 60 ثانية مع صوت 48 كيلوهرتز.",
            "instagram_reels": "مخصص لخلاصة ريلز مع دعم حتى 90 ثانية وصوت 44.1/48 كيلوهرتز.",
            "tiktok": "مخصص لخلاصة تيك توك الرأسية مع دعم حتى 180 ثانية.",
            "youtube_standard": "فيديو عريض 16:9 لشاشات الكمبيوتر والتلفاز.",
            "generic_vertical": "حزمة فيديو رأسي قياسية متعددة الاستخدامات.",
        }
        return translations.get(self.platform_id, self.notes)

    @property
    def max_duration_seconds(self) -> float:
        return self.max_duration_sec

    @property
    def max_true_peak_dbtp(self) -> float:
        return self.true_peak_ceiling

    @property
    def platform_disclaimer(self) -> str:
        return self.disclaimer

    @property
    def last_reviewed_date(self) -> str:
        return self.last_reviewed

    @property
    def allowed_video_codecs(self) -> List[str]:
        return ["h264", "libx264", "avc1"]

    @property
    def allowed_audio_codecs(self) -> List[str]:
        return ["aac", "mp4a-40-2"]

    @property
    def allowed_sample_rates(self) -> List[int]:
        return [44100, 48000]

    @property
    def resolution_str(self) -> str:
        return f"{self.width}x{self.height}"


YOUTUBE_SHORTS = PlatformExportProfile(
    platform_id="youtube_shorts",
    name="YouTube Shorts",
    version="1.1.0",
    width=1080,
    height=1920,
    fps=30.0,
    aspect_ratio="9:16",
    aspect_mode="cover",
    video_bitrate="10M",
    audio_sample_rate=48000,
    audio_bitrate="192k",
    max_duration_sec=180.0,
    preferred_duration_sec=60.0,
    target_lufs=-14.0,
    true_peak_ceiling=-1.0,
    last_reviewed="2026-11-23",
    documentation_source="YouTube Official Announcement (October 15, 2024): Shorts duration expanded up to 3 minutes for vertical 9:16 / square videos.",
    notes="YouTube platform maximum duration expanded to 180s (3 minutes) since Oct 2024. Studio default recommended duration remains <= 60s for optimal audience retention.",
)

INSTAGRAM_REELS = PlatformExportProfile(
    platform_id="instagram_reels",
    name="Instagram Reels",
    version="1.0.0",
    width=1080,
    height=1920,
    fps=30.0,
    aspect_ratio="9:16",
    aspect_mode="cover",
    video_bitrate="8M",
    audio_sample_rate=44100,
    audio_bitrate="192k",
    max_duration_sec=90.0,
    target_lufs=-14.0,
    true_peak_ceiling=-1.0,
    last_reviewed="2026-11-20",
    notes="Optimized for Meta Reels feed. Supports up to 90s. 44.1/48kHz audio.",
)

TIKTOK = PlatformExportProfile(
    platform_id="tiktok",
    name="TikTok",
    version="1.0.0",
    width=1080,
    height=1920,
    fps=30.0,
    aspect_ratio="9:16",
    aspect_mode="cover",
    video_bitrate="8M",
    audio_sample_rate=44100,
    audio_bitrate="192k",
    max_duration_sec=180.0,
    target_lufs=-14.0,
    true_peak_ceiling=-1.0,
    last_reviewed="2026-11-20",
    notes="Optimized for TikTok mobile feed. Supports standard vertical orientation.",
)

YOUTUBE_STANDARD = PlatformExportProfile(
    platform_id="youtube_standard",
    name="YouTube Standard (16:9)",
    version="1.0.0",
    width=1920,
    height=1080,
    fps=30.0,
    aspect_ratio="16:9",
    aspect_mode="contain",
    video_bitrate="12M",
    audio_sample_rate=48000,
    audio_bitrate="256k",
    max_duration_sec=3600.0,
    target_lufs=-14.0,
    true_peak_ceiling=-1.0,
    thumbnail_width=1280,
    thumbnail_height=720,
    last_reviewed="2026-11-20",
    notes="Widescreen 16:9 landscape video for desktop and TV display.",
)

GENERIC_VERTICAL = PlatformExportProfile(
    platform_id="generic_vertical",
    name="Generic Vertical (9:16)",
    version="1.0.0",
    width=1080,
    height=1920,
    fps=30.0,
    aspect_ratio="9:16",
    aspect_mode="cover",
    video_bitrate="8M",
    audio_sample_rate=48000,
    audio_bitrate="192k",
    max_duration_sec=300.0,
    target_lufs=-14.0,
    true_peak_ceiling=-1.0,
    last_reviewed="2026-11-20",
    notes="Universal vertical MP4 package.",
)

_PLATFORM_REGISTRY: Dict[str, PlatformExportProfile] = {
    "youtube_shorts": YOUTUBE_SHORTS,
    "shorts": YOUTUBE_SHORTS,
    "yt_shorts": YOUTUBE_SHORTS,
    "instagram_reels": INSTAGRAM_REELS,
    "reels": INSTAGRAM_REELS,
    "instagram": INSTAGRAM_REELS,
    "tiktok": TIKTOK,
    "youtube_standard": YOUTUBE_STANDARD,
    "youtube_16_9": YOUTUBE_STANDARD,
    "16:9": YOUTUBE_STANDARD,
    "generic_vertical": GENERIC_VERTICAL,
    "9:16": GENERIC_VERTICAL,
    "generic": GENERIC_VERTICAL,
}


def get_export_profile(identifier: str) -> PlatformExportProfile:
    """Retrieve platform profile by key or fallback to Generic Vertical."""
    clean_id = (identifier or "").strip().lower()
    if clean_id in _PLATFORM_REGISTRY:
        return _PLATFORM_REGISTRY[clean_id]
    raise ValueError(
        f"Unknown export profile '{identifier}'. Available profiles: {list(set(_PLATFORM_REGISTRY.keys()))}"
    )


def list_export_profiles() -> List[PlatformExportProfile]:
    """List distinct canonical platform profiles."""
    return [YOUTUBE_SHORTS, INSTAGRAM_REELS, TIKTOK, YOUTUBE_STANDARD, GENERIC_VERTICAL]


# Convenience aliases
get_profile_by_id = get_export_profile
get_all_profiles = list_export_profiles
DEFAULT_PROFILE_ID = "youtube_shorts"
YOUTUBE_SHORTS_PROFILE = YOUTUBE_SHORTS
INSTAGRAM_REELS_PROFILE = INSTAGRAM_REELS
TIKTOK_PROFILE = TIKTOK
YOUTUBE_STANDARD_PROFILE = YOUTUBE_STANDARD
