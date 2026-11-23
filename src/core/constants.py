"""Core constants and enumerations for Universal AI Video Production Studio."""
from enum import Enum


class AspectRatio(str, Enum):
    """Supported aspect ratios across target platforms."""
    VERTICAL_9_16 = "9:16"    # Standard YouTube Shorts, TikTok, Reels (1080x1920)
    SQUARE_1_1 = "1:1"        # Square YouTube Shorts & Social Feeds (1080x1080)
    HORIZONTAL_16_9 = "16:9"  # Standard YouTube Horizontal & Compilations (1920x1080, 3840x2160)

    def __str__(self) -> str:
        return self.value


class GenerationMode(str, Enum):
    """Supported multimodal generation pathways."""
    TEXT_TO_VIDEO = "text_to_video"
    IMAGE_TO_VIDEO = "image_to_video"
    DIRECT_MEDIA_INGEST = "direct_media_ingest"
    VIDEO_TO_VIDEO = "video_to_video"  # Deferred post-MVP

    def __str__(self) -> str:
        return self.value


class ProjectStatus(str, Enum):
    """Lifecycle statuses for a video production project."""
    DRAFT = "draft"
    SCRIPTING = "scripting"
    GENERATING_ASSETS = "generating_assets"
    RENDERING = "rendering"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    def __str__(self) -> str:
        return self.value


class TaskStatus(str, Enum):
    """Execution status for queue background tasks."""
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    RETRY_PENDING = "retry_pending"
    RETRY_PENDING_APPROVAL = "retry_pending_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"

    def __str__(self) -> str:
        return self.value


class TaskType(str, Enum):
    """Categorization of background tasks."""
    SCRIPT_GENERATION = "script_generation"
    VOICE_SYNTHESIS = "voice_synthesis"
    IMAGE_GENERATION = "image_generation"
    VIDEO_GENERATION = "video_generation"
    VIDEO_RENDERING = "video_rendering"
    COMPLIANCE_CHECK = "compliance_check"
    GENERAL = "general"

    def __str__(self) -> str:
        return self.value


class TaskPriority(int, Enum):
    """Priority weights for task scheduling (higher number = higher priority)."""
    LOW = 5
    NORMAL = 10
    HIGH = 20
    URGENT = 50


class LegalBasis(str, Enum):
    """Documented legal basis for asset usage (ADR-008)."""
    PROPRIETARY_OWNERSHIP = "proprietary_ownership"
    COMMERCIAL_LICENSE = "commercial_license"
    DIRECT_WRITTEN_PERMISSION = "direct_written_permission"
    VERIFIED_PUBLIC_DOMAIN_CC0 = "verified_public_domain_cc0"
    EXPLICIT_USER_OVERRIDE_EXCEPTION = "explicit_user_override_exception"


class LicenseType(str, Enum):
    """Common license classifications for media assets."""
    PROPRIETARY = "Proprietary"
    COMMERCIAL_PAID = "Commercial Paid"
    CREATIVE_COMMONS_BY = "CC-BY"
    CREATIVE_COMMONS_ZERO = "CC0"
    AI_GENERATED_COMMERCIAL = "AI Generated (Commercial Rights)"
    UNKNOWN = "Unknown / Pending Review"


class VerificationStatus(str, Enum):
    """License verification status for an asset (ADR-008)."""
    VERIFIED = "verified"
    PENDING_REVIEW = "pending_review"
    RESTRICTED = "restricted"


class AssetType(str, Enum):
    """Types of media assets managed by MAM."""
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    FONT = "font"


class AudioRole(str, Enum):
    """Semantic role of an audio asset in studio production."""
    VOICEOVER = "voiceover"
    BACKGROUND_MUSIC = "background_music"
    SOUND_EFFECT = "sound_effect"


class VoiceoverLanguage(str, Enum):
    """Supported voiceover narration languages."""
    ARABIC = "ar"
    ENGLISH = "en"


SUPPORTED_VOICEOVER_LANGUAGES = {VoiceoverLanguage.ARABIC.value, VoiceoverLanguage.ENGLISH.value}


class MusicStyle(str, Enum):
    """Designated musical style for background music."""
    GENTLE_CINEMATIC = "gentle_cinematic"
    ACOUSTIC_LULLABY = "acoustic_lullaby"
    UPLIFTING_WHIMSICAL = "uplifting_whimsical"


class MusicMood(str, Enum):
    """Designated emotional mood for background music."""
    WARM_CALM = "warm_calm"
    PLAYFUL_LIGHT = "playful_light"
    SERENE_BEDTIME = "serene_bedtime"


class SceneType(str, Enum):
    """Semantic type of a scene within a video project."""
    STANDARD = "standard"
    INTRO = "intro"
    OUTRO = "outro"
    TRANSITION = "transition"


class SceneStatus(str, Enum):
    """Lifecycle status of a scene."""
    DRAFT = "draft"
    READY = "ready"
    RENDERING = "rendering"
    RENDERED = "rendered"
    FAILED = "failed"


class FileOperationState(str, Enum):
    """States of the persistent crash recovery journal."""
    PENDING = "PENDING"
    FILE_WRITTEN = "FILE_WRITTEN"
    DB_COMMITTED = "DB_COMMITTED"
    FILE_FINALIZED = "FILE_FINALIZED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class FileOperationType(str, Enum):
    """Types of file operations tracked by the recovery journal."""
    IMPORT = "IMPORT"
    RENDER = "RENDER"
    EXPORT = "EXPORT"
    PURGE = "PURGE"


class CheckpointStepPhase(str, Enum):
    """Phases of the 5-step checkpoint protocol."""
    PRE_STEP_INTENT = "PRE_STEP_INTENT"
    EXECUTE_WORK = "EXECUTE_WORK"
    VERIFY_OUTPUT = "VERIFY_OUTPUT"
    ATOMIC_COMMIT = "ATOMIC_COMMIT"
    AUDIT_FLUSH = "AUDIT_FLUSH"


# Audio Reference Standards (ADR-003)
DEFAULT_TRUE_PEAK_DBTP = -1.0  # Decibels relative to True Peak (configurable)
DEFAULT_TARGET_LUFS = -14.0     # Reference integrated loudness for YouTube
DEFAULT_DUCKING_ATTENUATION_DB = -14.0  # Ducking reduction during speech

# Supported Output Resolutions
ASPECT_RATIO_DIMENSIONS = {
    AspectRatio.VERTICAL_9_16: (1080, 1920),
    AspectRatio.SQUARE_1_1: (1080, 1080),
    AspectRatio.HORIZONTAL_16_9: (1920, 1080),
}

