"""StorageService: Central facade coordinating media storage providers (Task 3.9)."""
from pathlib import Path
from typing import Optional, Dict, Any, List, Union, BinaryIO

from src.storage.base import StorageProvider, StorageMetadata
from src.storage.local_provider import LocalStorageProvider
from src.core.config import get_settings
from src.core.exceptions import StorageException
from src.core.logger import get_logger

logger = get_logger("studio.storage.service")


class StorageService:
    """Unified storage service coordinating Local and future Cloud (S3/R2) media storage."""

    def __init__(
        self,
        default_provider_name: str = "local",
        local_base_dir: Optional[Path] = None,
    ):
        settings = get_settings()
        # Storage root defaults to studio_data_dir (or project_root if legacy)
        base = local_base_dir or settings.studio_data_dir
        self._providers: Dict[str, StorageProvider] = {
            "local": LocalStorageProvider(base_dir=base),
        }
        self._default_provider_name = default_provider_name

    def register_provider(self, name: str, provider: StorageProvider) -> None:
        """Register a new storage provider (e.g. 's3', 'r2', 'minio')."""
        clean_name = name.strip().lower()
        self._providers[clean_name] = provider
        logger.info(f"Registered storage provider '{clean_name}' ({provider.__class__.__name__}).")

    def get_provider(self, name: Optional[str] = None) -> StorageProvider:
        """Resolve a storage provider by name or return the default provider."""
        provider_name = (name or self._default_provider_name).strip().lower()
        if provider_name not in self._providers:
            raise StorageException(
                f"Storage provider '{provider_name}' is not registered. Available: {list(self._providers.keys())}",
                provider=provider_name,
            )
        return self._providers[provider_name]

    @property
    def default_provider(self) -> StorageProvider:
        """Return the default active storage provider."""
        return self.get_provider(self._default_provider_name)

    # -------------------------------------------------------------------------
    # Key Construction & Normalization Helpers
    # -------------------------------------------------------------------------
    @staticmethod
    def build_channel_asset_key(
        channel_id: str,
        category: str,
        file_name: str,
        asset_id: Optional[str] = None,
    ) -> str:
        """Build canonical storage key for a channel media asset."""
        clean_ch = channel_id.strip()
        clean_cat = category.strip()
        prefix = f"{asset_id}_" if asset_id and not file_name.startswith(asset_id) else ""
        return f"channels/{clean_ch}/media_vault/originals/{clean_cat}/{prefix}{file_name}"

    @staticmethod
    def build_export_key(channel_id: str, file_name: str) -> str:
        """Build canonical storage key for an exported video or master."""
        clean_ch = channel_id.strip()
        return f"channels/{clean_ch}/exports/videos/{file_name}"

    # -------------------------------------------------------------------------
    # Unified File Operations
    # -------------------------------------------------------------------------
    def save_file(
        self,
        key: str,
        data: Union[bytes, BinaryIO, Path],
        provider_name: Optional[str] = None,
        mime_type: Optional[str] = None,
        custom_metadata: Optional[Dict[str, Any]] = None,
    ) -> StorageMetadata:
        """Save a file through the designated storage provider."""
        provider = self.get_provider(provider_name)
        return provider.save(key=key, data=data, mime_type=mime_type, custom_metadata=custom_metadata)

    def load_bytes(self, key: str, provider_name: Optional[str] = None) -> bytes:
        """Load full file content bytes through the designated storage provider."""
        provider = self.get_provider(provider_name)
        return provider.load(key)

    def get_local_path(self, key: str, provider_name: Optional[str] = None) -> Path:
        """Resolve a local filesystem Path (for FFmpeg rendering or local processing)."""
        provider = self.get_provider(provider_name)
        return provider.get_local_path(key)

    def exists(self, key: str, provider_name: Optional[str] = None) -> bool:
        """Check if storage key exists in the designated provider."""
        provider = self.get_provider(provider_name)
        return provider.exists(key)

    def delete(self, key: str, provider_name: Optional[str] = None) -> bool:
        """Delete file at key in the designated provider."""
        provider = self.get_provider(provider_name)
        return provider.delete(key)

    def get_metadata(self, key: str, provider_name: Optional[str] = None) -> StorageMetadata:
        """Retrieve metadata for storage key."""
        provider = self.get_provider(provider_name)
        return provider.get_metadata(key)

    def list_keys(self, prefix: str = "", provider_name: Optional[str] = None) -> List[str]:
        """List keys with given prefix."""
        provider = self.get_provider(provider_name)
        return provider.list_keys(prefix=prefix)

