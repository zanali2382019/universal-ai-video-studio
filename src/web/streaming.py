"""HTTP Range Request Streaming Engine for Studio WebUI (Task 3.15b).

Provides:
- HTTP 200, 206 Partial Content, and 416 Range Not Satisfiable responses.
- Safe chunked generator with immediate file descriptor closure (Windows-safe).
- Strict path traversal prevention and zero absolute path leakage.
"""

import os
import re
from pathlib import Path
from typing import Optional, List, Union, Generator
from fastapi import Request, Response
from fastapi.responses import StreamingResponse

from src.core.logger import get_logger

logger = get_logger("studio.web.streaming")

CHUNK_SIZE = 64 * 1024  # 64 KB


def parse_byte_range(range_header: str, file_size: int) -> Optional[tuple[int, int]]:
    """Parse HTTP Range header value (e.g. 'bytes=0-1024' or 'bytes=500-').

    Returns:
        (start, end) tuple (inclusive), or None if format is invalid or unsatisfiable.
    """
    if not range_header or not range_header.startswith("bytes="):
        return None

    range_val = range_header.replace("bytes=", "").strip()
    match = re.match(r"^(\d*)-(\d*)$", range_val)
    if not match:
        return None

    start_str, end_str = match.groups()

    if not start_str and not end_str:
        return None

    if start_str and end_str:
        start = int(start_str)
        end = int(end_str)
    elif start_str:
        start = int(start_str)
        end = file_size - 1
    else:  # Suffix byte range: e.g. -500 (last 500 bytes)
        suffix = int(end_str)
        if suffix == 0:
            return None
        start = max(0, file_size - suffix)
        end = file_size - 1

    if start < 0 or start >= file_size or end < start:
        return None

    end = min(end, file_size - 1)
    return (start, end)


def create_range_stream_response(
    file_path: Union[str, Path],
    request: Request,
    allowed_base_dirs: Optional[List[Path]] = None,
    media_type: str = "video/mp4",
) -> Response:
    """Generate safe, chunked HTTP Range response for streaming media.

    Args:
        file_path: Target media file path.
        request: Incoming FastAPI Request.
        allowed_base_dirs: List of directory paths within which file_path must reside.
        media_type: MIME media type for Content-Type header.

    Returns:
        FastAPI Response (200, 206 Partial Content, or 416 Range Not Satisfiable).
    """
    target = Path(file_path).resolve()

    # 1. Path traversal security check
    if allowed_base_dirs:
        is_safe = False
        for base in allowed_base_dirs:
            try:
                base_resolved = base.resolve()
                if target.is_relative_to(base_resolved):
                    is_safe = True
                    break
            except Exception:
                continue
        if not is_safe:
            logger.warning(f"Forbidden path traversal attempt for streaming: '{target}'")
            return Response(content="Forbidden", status_code=403)

    if not target.exists() or not target.is_file():
        return Response(content="Media file not found", status_code=404)

    file_size = target.stat().st_size
    if file_size == 0:
        # Zero-byte file
        return Response(
            content=b"",
            status_code=200,
            headers={
                "Content-Length": "0",
                "Content-Type": media_type,
                "Accept-Ranges": "bytes",
            },
        )

    range_header = request.headers.get("Range") or request.headers.get("range")

    # If no Range header, stream complete file (HTTP 200)
    if not range_header:
        def full_file_generator() -> Generator[bytes, None, None]:
            with open(target, "rb") as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    yield chunk

        headers = {
            "Content-Length": str(file_size),
            "Content-Type": media_type,
            "Accept-Ranges": "bytes",
        }
        return StreamingResponse(
            full_file_generator(),
            status_code=200,
            headers=headers,
            media_type=media_type,
        )

    # Range header present -> Parse requested range
    byte_range = parse_byte_range(range_header, file_size)
    if byte_range is None:
        # 416 Range Not Satisfiable
        return Response(
            status_code=416,
            headers={
                "Content-Range": f"bytes */{file_size}",
                "Accept-Ranges": "bytes",
            },
        )

    start, end = byte_range
    content_length = (end - start) + 1

    def partial_file_generator() -> Generator[bytes, None, None]:
        with open(target, "rb") as f:
            f.seek(start)
            bytes_left = content_length
            while bytes_left > 0:
                to_read = min(bytes_left, CHUNK_SIZE)
                data = f.read(to_read)
                if not data:
                    break
                bytes_left -= len(data)
                yield data

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Type": media_type,
    }

    return StreamingResponse(
        partial_file_generator(),
        status_code=206,
        headers=headers,
        media_type=media_type,
    )
