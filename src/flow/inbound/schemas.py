"""Pydantic data contracts for Google Flow Inbound Integration (Task 3.13).

Defines structured data contracts for:
- Inbound Source Types (Takeout archive, Flow folder metadata, Manual drop)
- Media Validation Results (technical verification via probe & PIL)
- Takeout / Flow metadata parsing
- Scene Take Records & Versioning
- Inbound Import Reports & Dry-run summaries
"""

from enum import Enum
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from pydantic import BaseModel, Field


class InboundSourceType(str, Enum):
    """Supported inbound ingestion sources."""
    TAKEOUT_ARCHIVE = "takeout_archive"
    FLOW_METADATA_FOLDER = "flow_metadata_folder"
    MANUAL_DROP = "manual_drop"


class InboundItemStatus(str, Enum):
    """Status outcome for an individual ingested media item."""
    IMPORTED = "imported"
    WOULD_IMPORT = "would_import"
    SKIPPED_DUPLICATE = "skipped_duplicate"
    UPDATED_VERSION = "updated_version"
    FAILED = "failed"


class MediaValidationResult(BaseModel):
    """Result of technical validation of incoming media files."""
    is_valid: bool
    media_type: str = Field(default="unknown", description="'video', 'image', 'audio', or 'unknown'")
    format_name: Optional[str] = None
    sha256: str
    file_size_bytes: int
    duration_sec: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    error_message: Optional[str] = None


class TakeoutParsedItem(BaseModel):
    """Metadata item parsed from Google Takeout or Flow JSON."""
    relative_file_path: str
    scene_identifier: str
    take_number: int = 1
    prompt_version: int = 1
    asset_role: str = "video"
    prompt_text: Optional[str] = None
    generation_id: Optional[str] = None
    seed: Optional[int] = None
    unparsed_metadata: Dict[str, Any] = Field(default_factory=dict)


class MatchedMediaItem(BaseModel):
    """Represents a discovered media file matched to a project scene."""
    source_path: str
    file_name: str
    scene_id: str
    take_number: int = 1
    prompt_version: int = 1
    asset_role: str = "video"
    raw_metadata: Dict[str, Any] = Field(default_factory=dict)


class InboundTakeModel(BaseModel):
    """Model of a persisted scene take in SQLite."""
    id: str
    channel_id: str
    project_id: str
    scene_id: str
    prompt_version: int = 1
    take_number: int = 1
    asset_id: str
    asset_role: str = "video"
    file_sha256: str
    source_file_name: str
    storage_key: str
    duration_sec: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    is_active: bool = True
    metadata_json: Dict[str, Any] = Field(default_factory=dict)
    created_at: str


class InboundImportItemReport(BaseModel):
    """Report entry for a single media item processed during inbound."""
    file_name: str
    scene_id: str
    take_number: int
    asset_role: str
    status: InboundItemStatus
    sha256: str
    storage_key: Optional[str] = None
    asset_id: Optional[str] = None
    take_id: Optional[str] = None
    details: Optional[str] = None
    error: Optional[str] = None


class InboundImportReport(BaseModel):
    """Complete summary of a Google Flow Inbound ingestion session."""
    inbound_record_id: str
    channel_id: str
    project_id: str
    source_type: InboundSourceType
    source_identifier: str
    is_dry_run: bool = False
    total_inspected: int = 0
    imported_count: int = 0
    would_import_count: int = 0
    duplicate_count: int = 0
    updated_version_count: int = 0
    failed_count: int = 0
    expected_scenes: List[str] = Field(default_factory=list)
    resolved_scenes: List[str] = Field(default_factory=list)
    missing_scenes: List[str] = Field(default_factory=list)
    items: List[InboundImportItemReport] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

