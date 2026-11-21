"""Application configuration management with environment variable loading."""
import os
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field, model_validator
from dotenv import load_dotenv

# Automatically load .env.local or .env from project root if present
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_LOCAL = _PROJECT_ROOT / ".env.local"
_ENV_DEFAULT = _PROJECT_ROOT / ".env"

if _ENV_LOCAL.exists():
    load_dotenv(_ENV_LOCAL)
elif _ENV_DEFAULT.exists():
    load_dotenv(_ENV_DEFAULT)


def _resolve_data_dir() -> Path:
    env_val = os.getenv("STUDIO_DATA_DIR")
    if env_val and env_val.strip():
        p = Path(env_val.strip())
        return p if p.is_absolute() else (_PROJECT_ROOT / p).resolve()
    return _PROJECT_ROOT


class Settings(BaseModel):
    """Platform configuration settings and path manager."""

    # Environment and Execution Mode
    env: str = Field(default_factory=lambda: os.getenv("STUDIO_ENV", "development"))
    debug: bool = Field(
        default_factory=lambda: os.getenv("STUDIO_DEBUG", "true").lower() in ("true", "1", "yes")
    )
    log_level: str = Field(default_factory=lambda: os.getenv("STUDIO_LOG_LEVEL", "INFO").upper())
    log_format: str = Field(default_factory=lambda: os.getenv("STUDIO_LOG_FORMAT", "console").lower())

    # Path Configuration (Task 3.9: Independent Runtime Data Directory)
    project_root: Path = Field(default=_PROJECT_ROOT)
    studio_data_dir: Optional[Path] = Field(default=None)
    workspace_root: Path = Field(
        default_factory=lambda: (
            Path(os.getenv("STUDIO_WORKSPACE_ROOT"))
            if os.getenv("STUDIO_WORKSPACE_ROOT") and Path(os.getenv("STUDIO_WORKSPACE_ROOT")).is_absolute()
            else (_resolve_data_dir() / os.getenv("STUDIO_WORKSPACE_ROOT", "workspace")).resolve()
        )
    )
    channels_root: Path = Field(
        default_factory=lambda: (
            Path(os.getenv("STUDIO_CHANNELS_ROOT"))
            if os.getenv("STUDIO_CHANNELS_ROOT") and Path(os.getenv("STUDIO_CHANNELS_ROOT")).is_absolute()
            else (_resolve_data_dir() / os.getenv("STUDIO_CHANNELS_ROOT", "channels")).resolve()
        )
    )
    global_templates_root: Path = Field(
        default_factory=lambda: _PROJECT_ROOT / os.getenv("STUDIO_GLOBAL_TEMPLATES_ROOT", "global_templates")
    )

    # Database & Queue Configuration
    db_path: Path = Field(
        default_factory=lambda: (
            Path(os.getenv("STUDIO_DB_PATH"))
            if os.getenv("STUDIO_DB_PATH") and Path(os.getenv("STUDIO_DB_PATH")).is_absolute()
            else (_resolve_data_dir() / os.getenv("STUDIO_DB_PATH", "workspace/runtime/studio.db")).resolve()
        )
    )
    db_busy_timeout_ms: int = Field(
        default_factory=lambda: int(os.getenv("STUDIO_DB_BUSY_TIMEOUT_MS", "10000"))
    )

    @model_validator(mode="after")
    def _apply_studio_data_dir(self) -> "Settings":
        if self.studio_data_dir is None:
            self.studio_data_dir = _resolve_data_dir()

        data_dir = Path(self.studio_data_dir).resolve()
        if not os.getenv("STUDIO_CHANNELS_ROOT"):
            self.channels_root = (data_dir / "channels").resolve()
        if not os.getenv("STUDIO_WORKSPACE_ROOT"):
            self.workspace_root = (data_dir / "workspace").resolve()
        if not os.getenv("STUDIO_DB_PATH"):
            self.db_path = (self.workspace_root / "runtime" / "studio.db").resolve()
        return self

    # Audio Engineering Reference Defaults (ADR-003)
    default_true_peak_dbtp: float = Field(
        default_factory=lambda: float(os.getenv("STUDIO_DEFAULT_TRUE_PEAK_DBTP", "-1.0"))
    )
    default_target_lufs: float = Field(
        default_factory=lambda: float(os.getenv("STUDIO_DEFAULT_TARGET_LUFS", "-14.0"))
    )
    default_ducking_db: float = Field(
        default_factory=lambda: float(os.getenv("STUDIO_DEFAULT_DUCKING_DB", "-14.0"))
    )

    # External Provider Keys (Loaded from environment, never hardcoded)
    openai_api_key: Optional[str] = Field(default_factory=lambda: os.getenv("OPENAI_API_KEY") or None)
    anthropic_api_key: Optional[str] = Field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY") or None)
    gemini_api_key: Optional[str] = Field(default_factory=lambda: os.getenv("GEMINI_API_KEY") or None)
    elevenlabs_api_key: Optional[str] = Field(default_factory=lambda: os.getenv("ELEVENLABS_API_KEY") or None)
    kling_api_key: Optional[str] = Field(default_factory=lambda: os.getenv("KLING_API_KEY") or None)
    fal_key: Optional[str] = Field(default_factory=lambda: os.getenv("FAL_KEY") or None)
    replicate_api_token: Optional[str] = Field(default_factory=lambda: os.getenv("REPLICATE_API_TOKEN") or None)

    # Binary Overrides
    ffmpeg_binary: Optional[str] = Field(default_factory=lambda: os.getenv("FFMPEG_BINARY_PATH") or None)
    ffprobe_binary: Optional[str] = Field(default_factory=lambda: os.getenv("FFPROBE_BINARY_PATH") or None)

    @property
    def runtime_dir(self) -> Path:
        """Directory for database files, active locks, and local queues."""
        return self.workspace_root / "runtime"

    @property
    def temp_dir(self) -> Path:
        """Directory for intermediate video processing and temporary clips."""
        return self.workspace_root / "temp"

    @property
    def logs_dir(self) -> Path:
        """Directory for persistent structured log files."""
        return self.workspace_root / "logs"

    @property
    def backups_dir(self) -> Path:
        """Directory for database and project backups."""
        return self.studio_data_dir / "backups"

    def ensure_directories(self) -> None:
        """Create baseline directories safely if they do not exist."""
        for path in [
            self.studio_data_dir,
            self.workspace_root,
            self.channels_root,
            self.global_templates_root,
            self.runtime_dir,
            self.temp_dir,
            self.logs_dir,
            self.backups_dir,
        ]:
            path.mkdir(parents=True, exist_ok=True)


# Singleton instance accessor
_settings_instance: Optional[Settings] = None


def get_settings(reload: bool = False) -> Settings:
    """Retrieve the global settings singleton."""
    global _settings_instance
    if _settings_instance is None or reload:
        _settings_instance = Settings()
        _settings_instance.ensure_directories()
    return _settings_instance

