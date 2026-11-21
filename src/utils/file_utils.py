"""File manipulation, path safety, and hashing utilities."""
import hashlib
import json
from pathlib import Path
from typing import Union, Dict, Any
from src.core.exceptions import PathTraversalSecurityException


def safe_resolve_path(base_root: Union[str, Path], sub_path: Union[str, Path]) -> Path:
    """Safely resolve sub_path within base_root, preventing directory traversal attacks.

    Args:
        base_root: The authorized root directory.
        sub_path: The user-supplied or relative path.

    Returns:
        Path: The validated absolute Path.

    Raises:
        PathTraversalSecurityException: If sub_path resolves outside base_root.
    """
    resolved_root = Path(base_root).resolve()
    resolved_target = (resolved_root / Path(sub_path)).resolve()

    try:
        resolved_target.relative_to(resolved_root)
    except ValueError:
        raise PathTraversalSecurityException(
            attempted_path=str(sub_path),
            root_path=str(resolved_root),
        )

    return resolved_target


def calculate_file_sha256(file_path: Union[str, Path], chunk_size: int = 65536) -> str:
    """Calculate the SHA-256 hash of a file on disk.

    Args:
        file_path: Absolute or relative path to the file.
        chunk_size: Byte chunk size for reading large media files efficiently.

    Returns:
        str: Hexadecimal SHA-256 digest.
    """
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found for hash calculation: {path}")

    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


def calculate_data_hash(data: Union[str, bytes, Dict[str, Any]]) -> str:
    """Calculate a deterministic SHA-256 hash of an arbitrary data payload.

    Used for task idempotency keys and asset caching.
    """
    if isinstance(data, dict):
        payload_bytes = json.dumps(data, sort_keys=True).encode("utf-8")
    elif isinstance(data, str):
        payload_bytes = data.encode("utf-8")
    elif isinstance(data, bytes):
        payload_bytes = data
    else:
        payload_bytes = str(data).encode("utf-8")

    return hashlib.sha256(payload_bytes).hexdigest()

