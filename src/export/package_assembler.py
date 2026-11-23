"""Atomic Export Package Assembler enforcing Guardrail E.

Assembles video, thumbnail, metadata, license preflight, and quality gate reports into
an atomic, verified release package with an export_manifest.json and a strictly relative ZIP archive.
"""
import os
import json
import shutil
import hashlib
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field

from src.core.logger import get_logger
from src.core.exceptions import StorageException, ValidationException

logger = get_logger("studio.export.assembler")


class FileManifestItem(BaseModel):
    """Manifest record for a packaged file."""
    relative_path: str
    file_size_bytes: int
    sha256: str
    mime_type: str


class ExportManifest(BaseModel):
    """Complete manifest for an export package."""
    package_id: str
    channel_id: str
    project_id: str
    profile_id: str
    platform: str
    generated_at: str
    files: List[FileManifestItem] = Field(default_factory=list)
    total_size_bytes: int = 0
    package_sha256: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


def calculate_sha256(file_path: Path) -> str:
    """Calculate SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().upper()


class ExportPackageAssembler:
    """Handles staging, verification, manifest generation, ZIP packaging, and atomic promotion."""

    def __init__(self, base_storage_dir: Optional[Path] = None) -> None:
        self.base_dir = (base_storage_dir or Path("workspace/runtime/storage")).resolve()

    def assemble_package(
        self,
        channel_id: str,
        project_id: str,
        package_id: str,
        profile_id: str,
        platform: str,
        video_source_path: Path,
        thumbnail_source_path: Path,
        metadata_dict: Dict[str, Any],
        preflight_report_dict: Dict[str, Any],
        quality_report_dict: Dict[str, Any],
        subtitles_source_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Stage, verify, manifest, and atomically package an export release."""
        clean_ch = channel_id.strip()
        clean_pkg = package_id.strip()

        channel_export_dir = self.base_dir / "channels" / clean_ch / "exports" / "packages"
        staging_dir = channel_export_dir / f"{clean_pkg}_staging"
        final_dir = channel_export_dir / clean_pkg
        zip_path = channel_export_dir / f"{clean_pkg}.zip"

        # Ensure parent directories exist
        channel_export_dir.mkdir(parents=True, exist_ok=True)

        # Clean staging dir if previously left
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        staging_dir.mkdir(parents=True, exist_ok=True)

        try:
            # 1. Verify existence and non-empty status of source files
            if not video_source_path.is_file() or video_source_path.stat().st_size == 0:
                raise ValidationException(f"Video source file missing or empty: {video_source_path}")

            if not thumbnail_source_path.is_file() or thumbnail_source_path.stat().st_size == 0:
                raise ValidationException(f"Thumbnail source file missing or empty: {thumbnail_source_path}")

            # 2. Stage media files
            video_ext = video_source_path.suffix or ".mp4"
            staged_video = staging_dir / f"{project_id}_{profile_id}{video_ext}"
            shutil.copy2(video_source_path, staged_video)

            thumb_ext = thumbnail_source_path.suffix or ".png"
            staged_thumb = staging_dir / f"{project_id}_thumbnail{thumb_ext}"
            shutil.copy2(thumbnail_source_path, staged_thumb)

            # Stage optional subtitles
            staged_subtitles: Optional[Path] = None
            if subtitles_source_path and subtitles_source_path.is_file():
                sub_ext = subtitles_source_path.suffix or ".srt"
                staged_subtitles = staging_dir / f"{project_id}_captions{sub_ext}"
                shutil.copy2(subtitles_source_path, staged_subtitles)

            # 3. Stage JSON artifacts
            staged_meta = staging_dir / "metadata.json"
            with open(staged_meta, "w", encoding="utf-8") as f:
                json.dump(metadata_dict, f, ensure_ascii=False, indent=2)

            staged_preflight = staging_dir / "preflight_report.json"
            with open(staged_preflight, "w", encoding="utf-8") as f:
                json.dump(preflight_report_dict, f, ensure_ascii=False, indent=2)

            staged_quality = staging_dir / "quality_report.json"
            with open(staged_quality, "w", encoding="utf-8") as f:
                json.dump(quality_report_dict, f, ensure_ascii=False, indent=2)

            # 4. Compute file hashes and build manifest
            manifest_files: List[FileManifestItem] = []
            total_size = 0

            # List of all files to package (strictly relative)
            files_to_hash = [
                (staged_video, "video/mp4"),
                (staged_thumb, "image/png" if thumb_ext == ".png" else "image/jpeg"),
                (staged_meta, "application/json"),
                (staged_preflight, "application/json"),
                (staged_quality, "application/json"),
            ]
            if staged_subtitles:
                mime = "text/vtt" if staged_subtitles.suffix == ".vtt" else "application/x-subrip"
                files_to_hash.append((staged_subtitles, mime))

            for fpath, mime in files_to_hash:
                sha = calculate_sha256(fpath)
                fsize = fpath.stat().st_size
                rel = fpath.name
                total_size += fsize
                manifest_files.append(FileManifestItem(
                    relative_path=rel,
                    file_size_bytes=fsize,
                    sha256=sha,
                    mime_type=mime,
                ))

            # Write export_manifest.json
            now_iso = datetime.now(timezone.utc).isoformat()
            manifest = ExportManifest(
                package_id=clean_pkg,
                channel_id=clean_ch,
                project_id=project_id,
                profile_id=profile_id,
                platform=platform,
                generated_at=now_iso,
                files=manifest_files,
                total_size_bytes=total_size,
            )

            staged_manifest = staging_dir / "export_manifest.json"
            with open(staged_manifest, "w", encoding="utf-8") as f:
                json.dump(manifest.to_dict(), f, ensure_ascii=False, indent=2)

            # 5. Build clean ZIP archive with strictly relative paths
            temp_zip = staging_dir.parent / f"{clean_pkg}_temp.zip"
            with zipfile.ZipFile(temp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                for item in staging_dir.iterdir():
                    if item.is_file() and not item.name.startswith("."):
                        # arcname strictly inside package folder
                        arc_name = f"{clean_pkg}/{item.name}"
                        zf.write(item, arcname=arc_name)

            # Calculate ZIP sha256
            zip_sha = calculate_sha256(temp_zip)
            zip_size = temp_zip.stat().st_size

            # Move temp zip to final zip path
            if zip_path.exists():
                zip_path.unlink()
            temp_zip.replace(zip_path)

            # 6. Atomic Promotion of Directory
            if final_dir.exists():
                shutil.rmtree(final_dir, ignore_errors=True)
            staging_dir.rename(final_dir)

            logger.info(
                f"Export package '{clean_pkg}' assembled successfully: "
                f"Dir='{final_dir}', ZIP='{zip_path}' ({zip_size} bytes, SHA={zip_sha[:12]}...)"
            )

            return {
                "package_id": clean_pkg,
                "channel_id": clean_ch,
                "project_id": project_id,
                "profile_id": profile_id,
                "package_dir": str(final_dir),
                "zip_path": str(zip_path),
                "zip_size_bytes": zip_size,
                "zip_sha256": zip_sha,
                "manifest_path": str(final_dir / "export_manifest.json"),
                "files_count": len(manifest_files) + 1,  # + manifest itself
                "generated_at": now_iso,
            }

        except Exception as e:
            logger.error(f"Failed to assemble package '{clean_pkg}': {e}", exc_info=True)
            # Ensure staging directory is cleaned on failure
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
            temp_zip = staging_dir.parent / f"{clean_pkg}_temp.zip"
            if temp_zip.exists():
                temp_zip.unlink()
            raise e
