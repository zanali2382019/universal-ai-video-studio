"""Local filesystem storage provider implementing the StorageProvider interface (Task 3.9)."""
import os
import shutil
import hashlib
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, BinaryIO, Union

from src.storage.base import StorageProvider, StorageMetadata
from src.core.exceptions import (
    StorageFileNotFoundException,
    StorageKeyInvalidException,
    PathTraversalSecurityException,
)
from src.utils.file_utils import safe_resolve_path, calculate_file_sha256
from src.core.logger import get_logger

logger = get_logger("studio.storage.local")


class LocalStorageProvider(StorageProvider):
    """Stores and retrieves assets on the local filesystem under a designated root directory."""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    @property
    def provider_name(self) -> str:
        return "local"

    def _normalize_key(self, key: str) -> str:
        """Sanitize and normalize storage key. Storage keys must be relative canonical keys."""
        if not key or not str(key).strip():
            raise StorageKeyInvalidException("", "Storage key cannot be empty.", provider=self.provider_name)

        raw_key = str(key).strip()
        if raw_key.startswith("/") or raw_key.startswith("\\"):
            raise StorageKeyInvalidException(
                raw_key,
                "Storage keys must be relative and cannot start with '/' or '\\'.",
                provider=self.provider_name,
            )

        # Replace backslashes with forward slashes for cross-platform consistency
        clean_key = raw_key.replace("\\", "/")

        # Check for absolute drive letter patterns on Windows (e.g. 'C:/')
        if len(clean_key) >= 2 and clean_key[1] == ":":
            raise StorageKeyInvalidException(
                clean_key,
                "Absolute system drive paths are not valid storage keys.",
                provider=self.provider_name,
            )

        if ".." in clean_key.split("/"):
            raise StorageKeyInvalidException(
                clean_key,
                "Path traversal sequences ('..') are strictly prohibited.",
                provider=self.provider_name,
            )

        return clean_key

    def _resolve_key(self, key: str) -> Path:
        """Resolve a storage key to an absolute Path inside base_dir safely."""
        norm_key = self._normalize_key(key)
        try:
            return safe_resolve_path(self.base_dir, norm_key)
        except PathTraversalSecurityException as e:
            raise StorageKeyInvalidException(
                norm_key,
                f"Path traverses outside authorized storage base: {e}",
                provider=self.provider_name,
            ) from e

    def save(
        self,
        key: str,
        data: Union[bytes, BinaryIO, Path],
        mime_type: Optional[str] = None,
        custom_metadata: Optional[Dict[str, Any]] = None,
    ) -> StorageMetadata:
        """Save an asset file or byte stream under key with atomic commit."""
        dest_path = self._resolve_key(key)
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        temp_path = dest_path.parent / f".tmp_{dest_path.name}_{os.getpid()}_{datetime.now().timestamp()}"

        try:
            sha256_hasher = hashlib.sha256()
            total_size = 0

            if isinstance(data, bytes):
                sha256_hasher.update(data)
                total_size = len(data)
                with open(temp_path, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())

            elif isinstance(data, Path):
                if not data.exists() or not data.is_file():
                    raise FileNotFoundError(f"Source file to save does not exist: {data}")
                with open(data, "rb") as src, open(temp_path, "wb") as dst:
                    while chunk := src.read(65536):
                        sha256_hasher.update(chunk)
                        total_size += len(chunk)
                        dst.write(chunk)
                    dst.flush()
                    os.fsync(dst.fileno())

            elif hasattr(data, "read"):
                with open(temp_path, "wb") as dst:
                    while chunk := data.read(65536):
                        sha256_hasher.update(chunk)
                        total_size += len(chunk)
                        dst.write(chunk)
                    dst.flush()
                    os.fsync(dst.fileno())
            else:
                raise TypeError(f"Unsupported data type for storage save: {type(data)}")

            # Atomic replace
            os.replace(temp_path, dest_path)

            guessed_mime = mime_type or mimetypes.guess_type(str(dest_path))[0] or "application/octet-stream"
            sha256_hex = sha256_hasher.hexdigest()
            now_utc = datetime.now(timezone.utc).isoformat()

            norm_key = self._normalize_key(key)
            logger.debug(f"Saved local asset '{norm_key}' ({total_size:,} bytes, sha256={sha256_hex[:12]}...).")

            return StorageMetadata(
                key=norm_key,
                provider=self.provider_name,
                size_bytes=total_size,
                sha256=sha256_hex,
                mime_type=guessed_mime,
                modified_at=now_utc,
                custom_metadata=custom_metadata or {},
            )
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink(missing_ok=True)
                except Exception:
                    pass

    def load(self, key: str) -> bytes:
        """Read and return full binary content."""
        p = self._resolve_key(key)
        if not p.exists() or not p.is_file():
            raise StorageFileNotFoundException(key, provider=self.provider_name)
        with open(p, "rb") as f:
            return f.read()

    def open_stream(self, key: str, mode: str = "rb") -> BinaryIO:
        """Open readable binary stream for the key."""
        p = self._resolve_key(key)
        if not p.exists() or not p.is_file():
            raise StorageFileNotFoundException(key, provider=self.provider_name)
        return open(p, mode)

    def get_local_path(self, key: str) -> Path:
        """Return absolute Path on disk (required for FFmpeg and local engines)."""
        p = self._resolve_key(key)
        if not p.exists():
            raise StorageFileNotFoundException(key, provider=self.provider_name)
        return p

    def exists(self, key: str) -> bool:
        """Check if key exists."""
        try:
            p = self._resolve_key(key)
            return p.exists() and p.is_file()
        except Exception:
            return False

    def delete(self, key: str) -> bool:
        """Delete file at key."""
        try:
            p = self._resolve_key(key)
            if p.exists() and p.is_file():
                p.unlink()
                logger.debug(f"Deleted local asset '{key}'.")
                return True
            return False
        except Exception as e:
            logger.warning(f"Failed deleting key '{key}': {e}")
            return False

    def get_metadata(self, key: str) -> StorageMetadata:
        """Read metadata without reading entire file into memory."""
        p = self._resolve_key(key)
        if not p.exists() or not p.is_file():
            raise StorageFileNotFoundException(key, provider=self.provider_name)

        stat = p.stat()
        sha256_hex = calculate_file_sha256(p)
        guessed_mime = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        mtime_utc = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()

        return StorageMetadata(
            key=self._normalize_key(key),
            provider=self.provider_name,
            size_bytes=stat.st_size,
            sha256=sha256_hex,
            mime_type=guessed_mime,
            modified_at=mtime_utc,
        )

    def list_keys(self, prefix: str = "") -> List[str]:
        """List all keys matching prefix."""
        norm_prefix = self._normalize_key(prefix) if prefix else ""
        results: List[str] = []

        if norm_prefix:
            search_dir = self.base_dir / norm_prefix
            if search_dir.is_file():
                return [norm_prefix]
            if not search_dir.exists():
                # Check if it's a partial filename prefix
                search_dir = (self.base_dir / norm_prefix).parent
        else:
            search_dir = self.base_dir

        if not search_dir.exists():
            return []

        for p in search_dir.rglob("*"):
            if p.is_file():
                rel_key = str(p.relative_to(self.base_dir)).replace("\\", "/")
                if not norm_prefix or rel_key.startswith(norm_prefix):
                    results.append(rel_key)

        return sorted(results)
