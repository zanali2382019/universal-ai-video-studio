"""Data models and schemas for database entities."""
import json
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator
from src.core.constants import (
    AspectRatio,
    GenerationMode,
    ProjectStatus,
    VerificationStatus,
    LegalBasis,
    AssetType,
    SceneType,
    SceneStatus,
    FileOperationState,
    FileOperationType,
    TaskStatus,
    TaskType,
    TaskPriority,
)


class ChannelModel(BaseModel):
    """Full channel entity matching database schema."""
    id: str = Field(..., description="Unique alphanumeric identifier (e.g. ch_dreammatter_001)")
    name: str = Field(..., min_length=2, max_length=100)
    handle: str = Field(..., min_length=2, max_length=100, description="Unique YouTube handle (e.g. @DreamMatterWorld)")
    niche: str = Field(..., min_length=2, max_length=100)
    default_language: str = Field(default="en", max_length=10)
    supported_locales: List[str] = Field(default_factory=lambda: ["en"])
    coppa_default: str = Field(default="not_made_for_kids")
    synthetic_media_declaration: bool = Field(default=True)
    watermark_path: Optional[str] = None
    watermark_position: str = Field(default="top_right")
    primary_font: Optional[str] = None
    primary_color: Optional[str] = None
    default_aspect_ratio: AspectRatio = Field(default=AspectRatio.VERTICAL_9_16)
    default_template_id: Optional[str] = None
    default_tts_voice_id: Optional[str] = None
    default_generation_mode: GenerationMode = Field(default=GenerationMode.IMAGE_TO_VIDEO)
    is_active: bool = Field(default=True)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @field_validator("handle")
    @classmethod
    def validate_handle(cls, v: str) -> str:
        clean = v.strip()
        if not clean.startswith("@"):
            clean = f"@{clean}"
        return clean

    @classmethod
    def from_row(cls, row: Any) -> "ChannelModel":
        """Convert a sqlite3.Row object to ChannelModel."""
        data = dict(row)
        # Parse JSON fields
        if isinstance(data.get("supported_locales"), str):
            try:
                data["supported_locales"] = json.loads(data["supported_locales"])
            except Exception:
                data["supported_locales"] = ["en"]
        data["synthetic_media_declaration"] = bool(data.get("synthetic_media_declaration", 1))
        data["is_active"] = bool(data.get("is_active", 1))
        return cls(**data)


class ChannelCreate(BaseModel):
    """Input payload for creating a new channel."""
    id: str = Field(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    name: str = Field(..., min_length=2, max_length=100)
    handle: str = Field(..., min_length=2, max_length=100)
    niche: str = Field(..., min_length=2, max_length=100)
    default_language: str = Field(default="en")
    supported_locales: List[str] = Field(default_factory=lambda: ["en"])
    coppa_default: str = Field(default="not_made_for_kids")
    synthetic_media_declaration: bool = Field(default=True)
    watermark_path: Optional[str] = None
    watermark_position: str = Field(default="top_right")
    primary_font: Optional[str] = None
    primary_color: Optional[str] = None
    default_aspect_ratio: AspectRatio = Field(default=AspectRatio.VERTICAL_9_16)
    default_template_id: Optional[str] = None
    default_tts_voice_id: Optional[str] = None
    default_generation_mode: GenerationMode = Field(default=GenerationMode.IMAGE_TO_VIDEO)

    @field_validator("handle")
    @classmethod
    def validate_handle(cls, v: str) -> str:
        clean = v.strip()
        if not clean.startswith("@"):
            clean = f"@{clean}"
        return clean


class ChannelUpdate(BaseModel):
    """Input payload for updating an existing channel."""
    name: Optional[str] = Field(default=None, min_length=2, max_length=100)
    handle: Optional[str] = Field(default=None, min_length=2, max_length=100)
    niche: Optional[str] = Field(default=None, min_length=2, max_length=100)
    default_language: Optional[str] = None
    supported_locales: Optional[List[str]] = None
    coppa_default: Optional[str] = None
    synthetic_media_declaration: Optional[bool] = None
    watermark_path: Optional[str] = None
    watermark_position: Optional[str] = None
    primary_font: Optional[str] = None
    primary_color: Optional[str] = None
    default_aspect_ratio: Optional[AspectRatio] = None
    default_template_id: Optional[str] = None
    default_tts_voice_id: Optional[str] = None
    default_generation_mode: Optional[GenerationMode] = None
    is_active: Optional[bool] = None


# ==========================================
# Project Models
# ==========================================

class ProjectModel(BaseModel):
    """Video project entity."""
    id: str
    channel_id: str
    title: str
    description: str = ""
    aspect_ratio: AspectRatio = AspectRatio.VERTICAL_9_16
    status: ProjectStatus = ProjectStatus.DRAFT
    target_duration_sec: float = 60.0
    is_archived: bool = False
    metadata_json: Dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: Any) -> "ProjectModel":
        data = dict(row)
        if isinstance(data.get("metadata_json"), str):
            try:
                data["metadata_json"] = json.loads(data["metadata_json"])
            except Exception:
                data["metadata_json"] = {}
        data["is_archived"] = bool(data.get("is_archived", 0))
        return cls(**data)


class ProjectCreate(BaseModel):
    """Input payload for creating a project."""
    id: str = Field(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    channel_id: str
    title: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    aspect_ratio: AspectRatio = AspectRatio.VERTICAL_9_16
    target_duration_sec: float = 60.0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ProjectUpdate(BaseModel):
    """Input payload for updating a project."""
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = None
    aspect_ratio: Optional[AspectRatio] = None
    status: Optional[ProjectStatus] = None
    target_duration_sec: Optional[float] = None
    is_archived: Optional[bool] = None
    metadata: Optional[Dict[str, Any]] = None


# ==========================================
# Scene Models
# ==========================================

class SceneModel(BaseModel):
    """Scene within a video project."""
    id: str
    project_id: str
    channel_id: str
    sequence_number: int
    title: str = ""
    narration_text: str = ""
    visual_prompt: str = ""
    duration_seconds: float = 5.0
    scene_type: SceneType = SceneType.STANDARD
    status: SceneStatus = SceneStatus.DRAFT
    metadata_json: Dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: Any) -> "SceneModel":
        data = dict(row)
        if isinstance(data.get("metadata_json"), str):
            try:
                data["metadata_json"] = json.loads(data["metadata_json"])
            except Exception:
                data["metadata_json"] = {}
        return cls(**data)


class SceneCreate(BaseModel):
    """Input payload for creating a scene."""
    id: str = Field(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    project_id: str
    channel_id: str
    sequence_number: int = Field(..., ge=1)
    title: str = ""
    narration_text: str = ""
    visual_prompt: str = ""
    duration_seconds: float = Field(default=5.0, gt=0)
    scene_type: SceneType = SceneType.STANDARD
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SceneUpdate(BaseModel):
    """Input payload for updating a scene."""
    sequence_number: Optional[int] = Field(default=None, ge=1)
    title: Optional[str] = None
    narration_text: Optional[str] = None
    visual_prompt: Optional[str] = None
    duration_seconds: Optional[float] = Field(default=None, gt=0)
    scene_type: Optional[SceneType] = None
    status: Optional[SceneStatus] = None
    metadata: Optional[Dict[str, Any]] = None


# ==========================================
# Media Asset Models
# ==========================================

class MediaAssetModel(BaseModel):
    """Registered media asset in MAM."""
    id: str
    channel_id: str
    project_id: Optional[str] = None
    scene_id: Optional[str] = None
    character_id: Optional[str] = None
    file_name: str
    file_path: str
    file_sha256: str
    file_size_bytes: int
    mime_type: str
    asset_type: AssetType
    asset_category: str = "general"
    storage_provider: str = "local"
    storage_key: Optional[str] = None
    license_reference: Optional[str] = None
    is_active: bool = True
    is_archived: bool = False
    metadata_json: Dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: Any) -> "MediaAssetModel":
        data = dict(row)
        if isinstance(data.get("metadata_json"), str):
            try:
                data["metadata_json"] = json.loads(data["metadata_json"])
            except Exception:
                data["metadata_json"] = {}
        data["is_active"] = bool(data.get("is_active", 1))
        data["is_archived"] = bool(data.get("is_archived", 0))
        if not data.get("storage_provider"):
            data["storage_provider"] = "local"
        if not data.get("storage_key"):
            data["storage_key"] = data.get("file_path")
        return cls(**data)


class MediaAssetCreate(BaseModel):
    """Input payload for registering an asset."""
    id: str = Field(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    channel_id: str
    project_id: Optional[str] = None
    scene_id: Optional[str] = None
    character_id: Optional[str] = None
    file_name: str
    file_path: str
    file_sha256: str
    file_size_bytes: int = Field(..., ge=0)
    mime_type: str
    asset_type: AssetType
    asset_category: str = "general"
    storage_provider: str = "local"
    storage_key: Optional[str] = None
    license_reference: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MediaAssetUpdate(BaseModel):
    """Input payload for updating an asset."""
    project_id: Optional[str] = None
    scene_id: Optional[str] = None
    character_id: Optional[str] = None
    asset_category: Optional[str] = None
    storage_provider: Optional[str] = None
    storage_key: Optional[str] = None
    license_reference: Optional[str] = None
    is_active: Optional[bool] = None
    is_archived: Optional[bool] = None
    metadata: Optional[Dict[str, Any]] = None


# ==========================================
# Asset License Models
# ==========================================

class AssetLicenseModel(BaseModel):
    """Legal license and verification record for an asset."""
    id: str
    asset_id: str
    channel_id: str
    legal_basis: LegalBasis
    verification_status: VerificationStatus = VerificationStatus.PENDING_REVIEW
    commercial_use_allowed: bool = False
    attribution_required: bool = False
    attribution_text: Optional[str] = None
    source_uri: Optional[str] = None
    rights_owner: Optional[str] = None
    proof_file_path: Optional[str] = None
    proof_sha256: Optional[str] = None
    notes: Optional[str] = None
    verified_at: Optional[str] = None
    verified_by: Optional[str] = None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: Any) -> "AssetLicenseModel":
        data = dict(row)
        data["commercial_use_allowed"] = bool(data.get("commercial_use_allowed", 0))
        data["attribution_required"] = bool(data.get("attribution_required", 0))
        return cls(**data)


class AssetLicenseCreate(BaseModel):
    """Input payload for registering a license record."""
    id: str = Field(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    asset_id: str
    channel_id: str
    legal_basis: LegalBasis
    verification_status: VerificationStatus = VerificationStatus.PENDING_REVIEW
    commercial_use_allowed: bool = False
    attribution_required: bool = False
    attribution_text: Optional[str] = None
    source_uri: Optional[str] = None
    rights_owner: Optional[str] = None
    proof_file_path: Optional[str] = None
    proof_sha256: Optional[str] = None
    notes: Optional[str] = None


class AssetLicenseUpdate(BaseModel):
    """Input payload for updating/verifying a license record."""
    legal_basis: Optional[LegalBasis] = None
    verification_status: Optional[VerificationStatus] = None
    commercial_use_allowed: Optional[bool] = None
    attribution_required: Optional[bool] = None
    attribution_text: Optional[str] = None
    source_uri: Optional[str] = None
    rights_owner: Optional[str] = None
    proof_file_path: Optional[str] = None
    proof_sha256: Optional[str] = None
    notes: Optional[str] = None
    verified_by: Optional[str] = None


# ==========================================
# Crash Recovery Journal & Outbox Models
# ==========================================

class FileOperationModel(BaseModel):
    """Persistent file operation record."""
    id: str
    operation_type: FileOperationType
    channel_id: str
    asset_id: Optional[str] = None
    source_path: Optional[str] = None
    temp_path: str
    target_path: str
    file_sha256: Optional[str] = None
    file_size_bytes: Optional[int] = None
    state: FileOperationState
    error_message: Optional[str] = None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: Any) -> "FileOperationModel":
        return cls(**dict(row))


class AuditEventOutboxModel(BaseModel):
    """Transactional outbox event for audit logging."""
    seq: int
    idempotency_key: str
    channel_id: str
    action: str
    actor: str = "system"
    payload_json: str
    previous_hash: str
    entry_hash: str
    file_flushed: bool = False
    created_at: str

    @classmethod
    def from_row(cls, row: Any) -> "AuditEventOutboxModel":
        data = dict(row)
        data["file_flushed"] = bool(data.get("file_flushed", 0))
        return cls(**data)


class AuditChainStateModel(BaseModel):
    """State anchoring the latest trusted hash of a channel's audit log."""
    channel_id: str
    last_seq: int
    latest_audit_hash: str
    is_locked: bool = False
    lock_reason: Optional[str] = None
    updated_at: str

    @classmethod
    def from_row(cls, row: Any) -> "AuditChainStateModel":
        data = dict(row)
        data["is_locked"] = bool(data.get("is_locked", 0))
        return cls(**data)


# ==========================================
# Task Queue, Leases & Checkpoint Models
# ==========================================

class TaskModel(BaseModel):
    """Persistent task entity matching database schema."""
    id: str
    channel_id: str
    project_id: Optional[str] = None
    scene_id: Optional[str] = None
    task_type: TaskType
    idempotency_key: str
    status: TaskStatus = TaskStatus.QUEUED
    priority: int = 10
    dependencies: List[str] = Field(default_factory=list)
    payload: Dict[str, Any] = Field(default_factory=dict)
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    error_details: Optional[Dict[str, Any]] = None
    retry_count: int = 0
    max_retries: int = 3
    scheduled_at: str
    claimed_at: Optional[str] = None
    heartbeat_at: Optional[str] = None
    lease_expires_at: Optional[str] = None
    execution_token: int = 0
    worker_id: Optional[str] = None
    current_step: Optional[str] = None
    total_steps: int = 1
    completed_steps: int = 0
    external_job_id: Optional[str] = None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: Any) -> "TaskModel":
        """Convert a sqlite3.Row object to TaskModel."""
        data = dict(row)
        if isinstance(data.get("dependencies_json"), str):
            try:
                data["dependencies"] = json.loads(data["dependencies_json"])
            except Exception:
                data["dependencies"] = []
        elif "dependencies" not in data:
            data["dependencies"] = []

        if isinstance(data.get("payload_json"), str):
            try:
                data["payload"] = json.loads(data["payload_json"])
            except Exception:
                data["payload"] = {}
        elif "payload" not in data:
            data["payload"] = {}

        if isinstance(data.get("result_json"), str):
            try:
                data["result"] = json.loads(data["result_json"])
            except Exception:
                data["result"] = None

        if isinstance(data.get("error_details_json"), str):
            try:
                data["error_details"] = json.loads(data["error_details_json"])
            except Exception:
                data["error_details"] = None

        data["task_type"] = TaskType(data["task_type"])
        data["status"] = TaskStatus(data["status"])
        return cls(**data)


class TaskCreate(BaseModel):
    """Payload to submit a new background task."""
    id: Optional[str] = None
    channel_id: str
    project_id: Optional[str] = None
    scene_id: Optional[str] = None
    task_type: TaskType
    idempotency_key: str
    status: TaskStatus = TaskStatus.QUEUED
    priority: int = 10
    dependencies: List[str] = Field(default_factory=list)
    payload: Dict[str, Any] = Field(default_factory=dict)
    max_retries: int = 3
    scheduled_at: Optional[str] = None
    total_steps: int = 1


class TaskUpdate(BaseModel):
    """Payload to update an existing task."""
    status: Optional[TaskStatus] = None
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    error_details: Optional[Dict[str, Any]] = None
    retry_count: Optional[int] = None
    scheduled_at: Optional[str] = None
    claimed_at: Optional[str] = None
    heartbeat_at: Optional[str] = None
    lease_expires_at: Optional[str] = None
    worker_id: Optional[str] = None
    current_step: Optional[str] = None
    completed_steps: Optional[int] = None
    external_job_id: Optional[str] = None


class ActiveChannelLeaseModel(BaseModel):
    """Model representing an active execution lease on a channel."""
    channel_id: str
    task_id: str
    worker_id: str
    execution_token: int
    lease_expires_at: str

    @classmethod
    def from_row(cls, row: Any) -> "ActiveChannelLeaseModel":
        return cls(**dict(row))


class TaskCheckpointModel(BaseModel):
    """Model representing a step checkpoint for a task."""
    id: str
    task_id: str
    step_name: str
    step_index: int
    step_data: Dict[str, Any] = Field(default_factory=dict)
    execution_token: int
    created_at: str

    @classmethod
    def from_row(cls, row: Any) -> "TaskCheckpointModel":
        data = dict(row)
        if isinstance(data.get("step_data_json"), str):
            try:
                data["step_data"] = json.loads(data["step_data_json"])
            except Exception:
                data["step_data"] = {}
        elif "step_data" not in data:
            data["step_data"] = {}
        return cls(**data)


class TaskCheckpointCreate(BaseModel):
    """Payload to create or record a step checkpoint."""
    id: Optional[str] = None
    task_id: str
    step_name: str
    step_index: int
    step_data: Dict[str, Any] = Field(default_factory=dict)
    execution_token: int


