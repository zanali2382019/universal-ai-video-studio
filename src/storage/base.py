"""Abstract Storage Provider and Data Models for Universal AI Video Studio (Task 3.9)."""
from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any, BinaryIO, Union
from pathlib import Path
from pydantic import BaseModel, Field


class StorageMetadata(BaseModel):
    """Normalized metadata for a stored asset object."""
    key: str
    provider: str
    size_bytes: int = Field(..., ge=0)
    sha256: str
    mime_type: str = "application/octet-stream"
    modified_at: Optional[str] = None
    custom_metadata: Dict[str, Any] = Field(default_factory=dict)


class StorageProvider(ABC):
    """Abstract interface for media storage backends (Local, S3-Compatible, R2, MinIO)."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Unique provider identifier (e.g. 'local', 's3')."""
        pass

    @abstractmethod
    def save(
        self,
        key: str,
        data: Union[bytes, BinaryIO, Path],
        mime_type: Optional[str] = None,
        custom_metadata: Optional[Dict[str, Any]] = None,
    ) -> StorageMetadata:
        """Save a file or byte stream under the specified storage key.

        Args:
            key: Relative storage key (e.g. 'channels/ch1/media_vault/originals/images/ast1.jpg').
            data: Content bytes, open readable binary stream, or Path to local file.
            mime_type: Optional MIME type override.
            custom_metadata: Optional key-value metadata dict.

        Returns:
            StorageMetadata: Metadata describing the persisted object.
        """
        pass

    @abstractmethod
    def load(self, key: str) -> bytes:
        """Retrieve the complete binary content for a storage key."""
        pass

    @abstractmethod
    def open_stream(self, key: str, mode: str = "rb") -> BinaryIO:
        """Open a readable binary stream for a storage key."""
        pass

    @abstractmethod
    def get_local_path(self, key: str) -> Path:
        """Return a local filesystem path for the object (required for FFmpeg processing).

        For local providers, resolves to the real local file path.
        For cloud providers (future), downloads/caches the object locally and returns the cached path.
        """
        pass

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Check whether a storage key exists."""
        pass

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Delete an object by key. Returns True if deleted, False if not found."""
        pass

    @abstractmethod
    def get_metadata(self, key: str) -> StorageMetadata:
        """Retrieve metadata for a storage key without loading entire file contents."""
        pass

    @abstractmethod
    def list_keys(self, prefix: str = "") -> List[str]:
        """List all storage keys starting with the given prefix."""
        pass

