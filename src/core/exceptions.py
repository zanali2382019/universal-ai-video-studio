"""Custom exception hierarchy for Universal AI Video Production Studio."""
from typing import Optional, Dict, Any


class StudioException(Exception):
    """Base exception for all domain errors within the studio."""
    def __init__(
        self,
        message: str,
        error_code: str = "STUDIO_ERROR",
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_code": self.error_code,
            "message": self.message,
            "details": self.details,
        }


class ConfigurationException(StudioException):
    """Raised when application or environment configuration is invalid."""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, error_code="CONFIG_ERROR", details=details)


class ChannelNotFoundException(StudioException):
    """Raised when a specified channel workspace or profile cannot be found."""
    def __init__(self, channel_id: str):
        super().__init__(
            f"Channel with ID '{channel_id}' was not found.",
            error_code="CHANNEL_NOT_FOUND",
            details={"channel_id": channel_id},
        )


class ProjectNotFoundException(StudioException):
    """Raised when a specified project cannot be found."""
    def __init__(self, project_id: str):
        super().__init__(
            f"Project with ID '{project_id}' was not found.",
            error_code="PROJECT_NOT_FOUND",
            details={"project_id": project_id},
        )


class LicenseVerificationException(StudioException):
    """Raised when an asset lacks a verified legal basis or commercial license for export."""
    def __init__(self, asset_id: str, reason: str, details: Optional[Dict[str, Any]] = None):
        merged_details = {"asset_id": asset_id, "reason": reason}
        if details:
            merged_details.update(details)
        super().__init__(
            f"Asset '{asset_id}' failed license verification: {reason}",
            error_code="LICENSE_VERIFICATION_FAILED",
            details=merged_details,
        )


class ValidationException(StudioException):
    """Raised when input parameters, schemas, or script models fail validation."""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, error_code="VALIDATION_ERROR", details=details)


class VoiceoverLanguageException(ValidationException):
    """Raised when voiceover language is missing, invalid, or unsupported."""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, details=details)
        self.error_code = "VOICEOVER_LANGUAGE_INVALID"


class AudioRoleMismatchException(ValidationException):
    """Raised when an audio asset does not match its designated role (music vs sfx vs voiceover)."""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, details=details)
        self.error_code = "AUDIO_ROLE_MISMATCH"


class ProviderException(StudioException):
    """Raised when an external AI provider (LLM, Video, Image, TTS) fails."""
    def __init__(
        self,
        provider_name: str,
        message: str,
        status_code: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        merged_details = {"provider": provider_name, "status_code": status_code}
        if details:
            merged_details.update(details)
        super().__init__(
            f"Provider '{provider_name}' error: {message}",
            error_code="PROVIDER_ERROR",
            details=merged_details,
        )


class ProviderLanguageUnavailableException(ProviderException):
    """Raised when the selected voiceover language is not supported by the provider."""
    def __init__(self, provider_name: str, language: str, message: Optional[str] = None):
        msg = message or f"Provider '{provider_name}' does not support requested language '{language}'."
        super().__init__(provider_name=provider_name, message=msg, details={"language": language})
        self.error_code = "PROVIDER_LANGUAGE_UNAVAILABLE"


class RenderingException(StudioException):
    """Raised when FFmpeg or media assembly pipeline encounters a critical error."""
    def __init__(self, message: str, exit_code: Optional[int] = None, details: Optional[Dict[str, Any]] = None):
        merged_details = {"exit_code": exit_code}
        if details:
            merged_details.update(details)
        super().__init__(message, error_code="RENDER_ERROR", details=merged_details)


# Convenient alias
RenderingError = RenderingException


class FFmpegNotFoundException(RenderingException):
    """Raised when the ffmpeg or ffprobe executable binary cannot be found."""
    def __init__(self, binary_name: str, searched_path: Optional[str] = None):
        super().__init__(
            f"Executable binary '{binary_name}' was not found in PATH or configured location: {searched_path or 'PATH'}.",
            details={"binary_name": binary_name, "searched_path": searched_path},
        )
        self.error_code = "FFMPEG_NOT_FOUND"


class FFmpegExecutionException(RenderingException):
    """Raised when an FFmpeg subprocess execution fails with non-zero exit code."""
    def __init__(self, message: str, exit_code: int, cmd: Optional[str] = None, stderr: Optional[str] = None):
        details = {"cmd": cmd, "stderr": stderr}
        super().__init__(message, exit_code=exit_code, details=details)
        self.error_code = "FFMPEG_EXECUTION_FAILED"


class FFprobeException(RenderingException):
    """Raised when ffprobe execution fails or output cannot be parsed."""
    def __init__(self, message: str, file_path: Optional[str] = None, stderr: Optional[str] = None):
        super().__init__(message, details={"file_path": file_path, "stderr": stderr})
        self.error_code = "FFPROBE_FAILED"


class MediaValidationException(RenderingException):
    """Raised when a media file fails technical validation (codecs, resolution, audio)."""
    def __init__(self, message: str, file_path: Optional[str] = None, validation_errors: Optional[Dict[str, Any]] = None):
        super().__init__(message, details={"file_path": file_path, "validation_errors": validation_errors or {}})
        self.error_code = "MEDIA_VALIDATION_FAILED"


class AudioTruePeakExceededException(MediaValidationException):
    """Raised when audio output exceeds the True Peak limit (-1.0 dBTP per ADR-003)."""
    def __init__(self, file_path: str, measured_peak_dbtp: float, max_allowed_dbtp: float = -1.0):
        super().__init__(
            f"Audio True Peak violation for '{file_path}': measured {measured_peak_dbtp:.2f} dBTP exceeds limit {max_allowed_dbtp:.2f} dBTP.",
            file_path=file_path,
            validation_errors={"measured_peak_dbtp": measured_peak_dbtp, "max_allowed_dbtp": max_allowed_dbtp},
        )
        self.error_code = "TRUE_PEAK_EXCEEDED"


class RenderTimeoutException(RenderingException):
    """Raised when an FFmpeg rendering subprocess exceeds the configured timeout."""
    def __init__(self, timeout_sec: float, cmd: Optional[str] = None):
        super().__init__(
            f"FFmpeg process exceeded execution timeout of {timeout_sec:.1f} seconds.",
            details={"timeout_sec": timeout_sec, "cmd": cmd},
        )
        self.error_code = "RENDER_TIMEOUT"


class RenderCancelledException(RenderingException):
    """Raised when an active FFmpeg rendering process is gracefully cancelled."""
    def __init__(self, message: str = "Render task was cancelled by user or worker pool shutdown."):
        super().__init__(message, details={"cancelled": True})
        self.error_code = "RENDER_CANCELLED"


class TimelineRenderException(RenderingException):
    """Raised when timeline compilation or scene concatenation fails."""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, details=details or {})
        self.error_code = "TIMELINE_RENDER_FAILED"


class DatabaseLockedException(StudioException):
    """Raised when SQLite database lock contention exceeds the busy timeout."""
    def __init__(self, message: str = "Database busy / lock timeout exceeded", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, error_code="DATABASE_LOCKED", details=details)


class PathTraversalSecurityException(StudioException):
    """Raised when an attempted file path traverses outside its authorized root directory."""
    def __init__(self, attempted_path: str, root_path: str):
        super().__init__(
            f"Security Violation: Path '{attempted_path}' traverses outside authorized root '{root_path}'.",
            error_code="PATH_TRAVERSAL_VIOLATION",
            details={"attempted_path": attempted_path, "root_path": root_path},
        )


class AuditIntegrityException(StudioException):
    """Raised when audit log hash chaining, sequence, or integrity verification fails."""
    def __init__(self, message: str, channel_id: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        merged = {"channel_id": channel_id}
        if details:
            merged.update(details)
        super().__init__(message, error_code="AUDIT_INTEGRITY_VIOLATION", details=merged)


class AssetNotFoundException(StudioException):
    """Raised when a specified media asset cannot be found."""
    def __init__(self, asset_id: str):
        super().__init__(
            f"Media asset with ID '{asset_id}' was not found.",
            error_code="ASSET_NOT_FOUND",
            details={"asset_id": asset_id},
        )


class AssetInUseException(StudioException):
    """Raised when an asset cannot be deleted because it is actively referenced in scenes or projects."""
    def __init__(self, asset_id: str, active_references: Dict[str, Any]):
        super().__init__(
            f"Asset '{asset_id}' cannot be deleted because it is actively in use.",
            error_code="ASSET_IN_USE",
            details={"asset_id": asset_id, "active_references": active_references},
        )


class SceneNotFoundException(StudioException):
    """Raised when a specified scene cannot be found."""
    def __init__(self, scene_id: str):
        super().__init__(
            f"Scene with ID '{scene_id}' was not found.",
            error_code="SCENE_NOT_FOUND",
            details={"scene_id": scene_id},
        )


class TaskNotFoundException(StudioException):
    """Raised when a specified task cannot be found."""
    def __init__(self, task_id: str):
        super().__init__(
            f"Task with ID '{task_id}' was not found.",
            error_code="TASK_NOT_FOUND",
            details={"task_id": task_id},
        )


class StaleExecutionTokenException(StudioException):
    """Raised when an operation is attempted with an expired or overridden execution token (fencing violation)."""
    def __init__(self, task_id: str, expected_token: int, actual_token: int, message: str = "Execution token is stale"):
        super().__init__(
            f"{message} for task '{task_id}'. Expected token {expected_token}, got {actual_token}.",
            error_code="STALE_EXECUTION_TOKEN",
            details={"task_id": task_id, "expected_token": expected_token, "actual_token": actual_token},
        )


class ChannelLeaseConflictException(StudioException):
    """Raised when another task already holds an active execution lease on the channel."""
    def __init__(self, channel_id: str, active_task_id: Optional[str] = None):
        super().__init__(
            f"Channel '{channel_id}' already has an active task lease" + (f" (task '{active_task_id}')" if active_task_id else "") + ".",
            error_code="CHANNEL_LEASE_CONFLICT",
            details={"channel_id": channel_id, "active_task_id": active_task_id},
        )


class InvalidStateTransitionException(ValidationException):
    """Raised when an illegal task state transition is attempted."""
    def __init__(
        self,
        task_id: str,
        current_status: str,
        target_status: str,
        details: Optional[Dict[str, Any]] = None,
    ):
        merged = {"task_id": task_id, "current_status": current_status, "target_status": target_status}
        if details:
            merged.update(details)
        super().__init__(
            f"Invalid state transition for task '{task_id}': cannot transition from '{current_status}' to '{target_status}'.",
            details=merged,
        )
        self.error_code = "INVALID_STATE_TRANSITION"


class CyclicDependencyException(ValidationException):
    """Raised when a task dependency creates a cycle in the DAG."""
    def __init__(self, task_id: str, cycle_path: Optional[Any] = None):
        details = {"task_id": task_id, "cycle_path": cycle_path or []}
        path_str = " -> ".join(cycle_path) if cycle_path else task_id
        super().__init__(
            f"Cyclic dependency detected for task '{task_id}': {path_str}.",
            details=details,
        )
        self.error_code = "CYCLIC_DEPENDENCY"


class AssetPathCollisionException(StudioException):
    """Raised when an operation encounters an existing asset path owned by another asset with a different hash."""
    def __init__(self, channel_id: str, file_path: str, existing_asset_id: Optional[str] = None):
        super().__init__(
            f"Path collision on channel '{channel_id}': '{file_path}' is owned by asset '{existing_asset_id}' with mismatched hash. Original asset strictly preserved.",
            error_code="ASSET_PATH_COLLISION",
            details={"channel_id": channel_id, "file_path": file_path, "existing_asset_id": existing_asset_id},
        )


# ==========================================
# Storage Architecture Exceptions (Task 3.9)
# ==========================================

class StorageException(StudioException):
    """Base exception for media storage abstraction and provider operations."""
    def __init__(self, message: str, provider: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        merged = {"provider": provider}
        if details:
            merged.update(details)
        super().__init__(message, error_code="STORAGE_ERROR", details=merged)


class StorageFileNotFoundException(StorageException):
    """Raised when an asset or key is not found in the storage provider."""
    def __init__(self, key: str, provider: str = "local"):
        super().__init__(
            f"Storage key '{key}' not found in provider '{provider}'.",
            provider=provider,
            details={"key": key},
        )
        self.error_code = "STORAGE_FILE_NOT_FOUND"


class StorageKeyInvalidException(StorageException):
    """Raised when a storage key contains illegal characters or path traversal elements."""
    def __init__(self, key: str, reason: str, provider: str = "local"):
        super().__init__(
            f"Invalid storage key '{key}' for provider '{provider}': {reason}",
            provider=provider,
            details={"key": key, "reason": reason},
        )
        self.error_code = "STORAGE_KEY_INVALID"



