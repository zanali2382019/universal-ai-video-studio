"""Google Flow Inbound Service (Task 3.13).

Provides:
- Inbound ingestion from Google Takeout (.zip), Flow metadata folders, and manual drop folders
- Automated naming convention matching and scene reconciliation
- SHA-256 deduplication and strict idempotency
- Generation history tracking, take versioning, and active take promotion
- Media integrity validation via ffprobe and Pillow
- Safe storage via StorageService using POSIX relative keys
- MAM registration with Google Flow license attribution
- Missing scenes detection and dry-run execution
"""

import os
import uuid
import shutil
import tempfile
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timezone

from src.core.logger import get_logger
from src.database.connection import DatabaseManager
from src.database.repositories.scene_repository import SceneRepository
from src.database.repositories.flow_inbound_repository import FlowInboundRepository
from src.database.repositories.flow_prompt_repository import FlowPromptRepository
from src.database.repositories.asset_repository import AssetRepository
from src.database.repositories.license_repository import LicenseRepository
from src.database.repositories.audit_repository import AuditRepository
from src.database.models import MediaAssetCreate, AssetLicenseCreate
from src.services.audit_service import AuditService
from src.storage.service import StorageService
from src.flow.inbound.schemas import (
    InboundSourceType,
    InboundItemStatus,
    InboundImportItemReport,
    InboundImportReport,
    MatchedMediaItem,
)
from src.flow.inbound.validator import MediaValidator
from src.flow.inbound.matcher import MediaMatcher
from src.flow.inbound.takeout_parser import TakeoutParser

logger = get_logger("studio.flow.inbound.importer")


class FlowInboundService:
    """Core service managing the ingestion of Google Flow media into the Studio."""

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        storage_service: Optional[StorageService] = None,
        validator: Optional[MediaValidator] = None,
    ) -> None:
        self._db = db_manager or DatabaseManager()
        self._storage = storage_service or StorageService()
        self._validator = validator or MediaValidator()
        self._inbound_repo = FlowInboundRepository(self._db)
        self._scene_repo = SceneRepository(self._db)
        self._prompt_repo = FlowPromptRepository(self._db)
        self._asset_repo = AssetRepository(self._db)
        self._license_repo = LicenseRepository(self._db)
        self._audit = AuditService(audit_repository=AuditRepository(self._db))
        self._parser = TakeoutParser()

    def import_assets(
        self,
        channel_id: str,
        project_id: str,
        source_path: Path,
        package_id: Optional[str] = None,
        dry_run: bool = False,
        actor: str = "operator",
    ) -> InboundImportReport:
        """Import media assets generated in Google Flow into the studio.
        
        Args:
            channel_id: Channel identifier.
            project_id: Studio project identifier.
            source_path: Path to .zip archive or folder containing generated media.
            package_id: Optional linked Outbound PromptPackage ID.
            dry_run: If True, validates and previews matching without writing to DB or storage.
            actor: Operator or workflow executing the import.
            
        Returns:
            InboundImportReport: Detailed audit and summary of the import session.
        """
        source = Path(source_path).resolve()
        if not source.exists():
            raise FileNotFoundError(f"Inbound source not found: {source}")

        # 1. Fetch expected project scenes
        expected_scene_models = self._scene_repo.list_by_project(channel_id, project_id)
        known_scenes: List[Dict[str, Any]] = [
            {"id": s.id, "sequence_number": s.sequence_number, "title": s.title}
            for s in expected_scene_models
        ]
        expected_scene_ids = [s["id"] for s in known_scenes]

        # 2. Determine source type
        if source.is_file() and source.suffix.lower() == ".zip":
            source_type = InboundSourceType.TAKEOUT_ARCHIVE
        else:
            has_json = any(source.rglob("*.json"))
            source_type = (
                InboundSourceType.FLOW_METADATA_FOLDER
                if has_json
                else InboundSourceType.MANUAL_DROP
            )

        # 3. Parse source items
        # Use temp dir for extracting zip if needed
        temp_dir_obj: Optional[tempfile.TemporaryDirectory] = None
        temp_extract_dir: Optional[Path] = None
        if source_type == InboundSourceType.TAKEOUT_ARCHIVE:
            temp_dir_obj = tempfile.TemporaryDirectory(prefix="takeout_")
            temp_extract_dir = Path(temp_dir_obj.name)

        try:
            root_dir, parsed_items, unref_media = self._parser.parse_source(
                source, extract_target_dir=temp_extract_dir
            )

            # 4. Initialize matcher with project scenes
            matcher = MediaMatcher(known_scenes=known_scenes)

            # Match all discovered files to scenes
            matched_candidates: List[Tuple[Path, MatchedMediaItem]] = []

            # (A) Items from metadata JSON
            for item in parsed_items:
                full_media_path = (root_dir / item.relative_file_path).resolve()
                if not full_media_path.is_file():
                    # Attempt basename match inside root_dir
                    candidates = list(root_dir.rglob(Path(item.relative_file_path).name))
                    if candidates and candidates[0].is_file():
                        full_media_path = candidates[0]

                # Resolve scene_id
                resolved_scene_id = item.scene_identifier
                # Check if scene_identifier is an integer sequence number
                try:
                    seq_num = int(item.scene_identifier)
                    for sc in known_scenes:
                        if sc["sequence_number"] == seq_num:
                            resolved_scene_id = sc["id"]
                            break
                except ValueError:
                    pass

                # If still not matching known scenes, run matcher on the media path
                if resolved_scene_id not in expected_scene_ids:
                    m = matcher.match_file(str(full_media_path))
                    if m:
                        resolved_scene_id = m.scene_id

                matched_item = MatchedMediaItem(
                    source_path=str(full_media_path),
                    file_name=full_media_path.name if full_media_path.exists() else Path(item.relative_file_path).name,
                    scene_id=resolved_scene_id,
                    take_number=item.take_number,
                    prompt_version=item.prompt_version,
                    asset_role=item.asset_role,
                    raw_metadata=item.unparsed_metadata,
                )
                matched_candidates.append((full_media_path, matched_item))

            # (B) Standalone / manual drop media files without metadata
            for mf in unref_media:
                m = matcher.match_file(str(mf))
                if m:
                    matched_candidates.append((mf, m))
                else:
                    logger.warning(f"Skipping unmatchable media file: '{mf.name}'")

            # 5. Process matched candidates
            item_reports: List[InboundImportItemReport] = []
            imported_count = 0
            duplicate_count = 0
            updated_count = 0
            failed_count = 0

            for full_path, match in matched_candidates:
                report_item = self._process_single_item(
                    channel_id=channel_id,
                    project_id=project_id,
                    full_path=full_path,
                    match=match,
                    dry_run=dry_run,
                    actor=actor,
                )
                item_reports.append(report_item)

                if report_item.status == InboundItemStatus.IMPORTED:
                    imported_count += 1
                elif report_item.status == InboundItemStatus.SKIPPED_DUPLICATE:
                    duplicate_count += 1
                elif report_item.status == InboundItemStatus.UPDATED_VERSION:
                    updated_count += 1
                elif report_item.status == InboundItemStatus.FAILED:
                    failed_count += 1

            # 6. Evaluate scene resolution and missing scenes
            # Determine which scenes currently have active takes in SQLite
            resolved_scenes: List[str] = []
            for sc_id in expected_scene_ids:
                active_take = self._inbound_repo.get_active_take(project_id, sc_id, "video")
                if active_take:
                    resolved_scenes.append(sc_id)
                elif dry_run:
                    # In dry-run, check if any candidate in item_reports is marked IMPORTED or UPDATED
                    if any(
                        r.scene_id == sc_id
                        and r.asset_role == "video"
                        and r.status in (InboundItemStatus.IMPORTED, InboundItemStatus.UPDATED_VERSION, InboundItemStatus.SKIPPED_DUPLICATE)
                        for r in item_reports
                    ):
                        resolved_scenes.append(sc_id)

            missing_scenes = [s for s in expected_scene_ids if s not in resolved_scenes]

            # 7. Record inbound session
            record_id = f"fib_{uuid.uuid4().hex[:12]}"
            report = InboundImportReport(
                inbound_record_id=record_id,
                channel_id=channel_id,
                project_id=project_id,
                source_type=source_type,
                source_identifier=source.name,
                is_dry_run=dry_run,
                total_inspected=len(matched_candidates),
                imported_count=imported_count,
                duplicate_count=duplicate_count,
                updated_version_count=updated_count,
                failed_count=failed_count,
                expected_scenes=expected_scene_ids,
                resolved_scenes=resolved_scenes,
                missing_scenes=missing_scenes,
                items=item_reports,
            )

            if not dry_run:
                self._inbound_repo.create_inbound_record(
                    record_id=record_id,
                    channel_id=channel_id,
                    project_id=project_id,
                    source_type=source_type.value,
                    source_identifier=source.name,
                    total_inspected=len(matched_candidates),
                    imported_count=imported_count,
                    duplicate_count=duplicate_count,
                    failed_count=failed_count,
                    package_id=package_id,
                    report_data=report.model_dump(),
                )
                self._audit.record_event(
                    channel_id=channel_id,
                    action="FLOW_INBOUND_IMPORT",
                    actor=actor,
                    payload={
                        "record_id": record_id,
                        "project_id": project_id,
                        "imported": imported_count,
                        "duplicates": duplicate_count,
                        "updated": updated_count,
                        "failed": failed_count,
                    },
                    idempotency_key=f"INBOUND_SESSION:{record_id}",
                )

            return report

        finally:
            if temp_dir_obj:
                try:
                    temp_dir_obj.cleanup()
                except Exception:
                    pass
            elif temp_extract_dir and temp_extract_dir.exists():
                try:
                    shutil.rmtree(temp_extract_dir, ignore_errors=True)
                except Exception:
                    pass

    def _process_single_item(
        self,
        channel_id: str,
        project_id: str,
        full_path: Path,
        match: MatchedMediaItem,
        dry_run: bool,
        actor: str,
    ) -> InboundImportItemReport:
        """Validate and ingest an individual media item."""
        # 1. Technical validation
        val_res = self._validator.validate_file(full_path)
        if not val_res.is_valid:
            logger.warning(
                f"Validation failed for '{full_path.name}': {val_res.error_message}"
            )
            return InboundImportItemReport(
                file_name=full_path.name,
                scene_id=match.scene_id,
                take_number=match.take_number,
                asset_role=match.asset_role,
                status=InboundItemStatus.FAILED,
                sha256=val_res.sha256,
                error=val_res.error_message,
            )

        file_sha256 = val_res.sha256

        # 2. Deduplication & Idempotency check
        # Check if identical content is already imported for this project & scene
        existing_take = self._inbound_repo.find_project_scene_take_by_sha256(
            project_id=project_id,
            scene_id=match.scene_id,
            file_sha256=file_sha256,
            asset_role=match.asset_role,
        )
        if existing_take:
            logger.info(
                f"Idempotency match: Take #{existing_take['take_number']} for scene "
                f"'{match.scene_id}' already has SHA-256 '{file_sha256[:10]}...'. Skipping duplicate."
            )
            return InboundImportItemReport(
                file_name=full_path.name,
                scene_id=match.scene_id,
                take_number=existing_take["take_number"],
                asset_role=match.asset_role,
                status=InboundItemStatus.SKIPPED_DUPLICATE,
                sha256=file_sha256,
                storage_key=existing_take["storage_key"],
                asset_id=existing_take["asset_id"],
                take_id=existing_take["id"],
                details=f"Identical take #{existing_take['take_number']} already registered.",
            )

        # 3. Determine take number and versioning status
        existing_takes = self._inbound_repo.list_takes_for_scene(
            project_id, match.scene_id, match.asset_role
        )
        if existing_takes:
            status = InboundItemStatus.UPDATED_VERSION
            # If user provided a specific take_number higher than max, use it, else next
            next_num = self._inbound_repo.get_next_take_number(
                project_id, match.scene_id, match.asset_role
            )
            take_number = max(match.take_number, next_num)
        else:
            status = InboundItemStatus.IMPORTED
            take_number = 1

        if dry_run:
            return InboundImportItemReport(
                file_name=full_path.name,
                scene_id=match.scene_id,
                take_number=take_number,
                asset_role=match.asset_role,
                status=status,
                sha256=file_sha256,
                details="Dry-run: validated and matched successfully.",
            )

        # 4. StorageService persistence with POSIX relative key
        take_id = f"take_{uuid.uuid4().hex[:12]}"
        safe_fname = full_path.name.replace(" ", "_")
        storage_key = (
            f"channels/{channel_id}/media_vault/flow_inbound/{project_id}/{match.scene_id}/{take_id}_{safe_fname}"
        )

        with open(full_path, "rb") as f:
            file_bytes = f.read()

        self._storage.save_file(storage_key, file_bytes)

        # 5. MAM (media_assets) Registration
        asset_id = f"ast_{uuid.uuid4().hex[:12]}"
        lic_ref = f"lic_{uuid.uuid4().hex[:12]}"
        asset_type = match.asset_role if match.asset_role in ("video", "image", "audio") else "video"

        # Check if asset already exists in asset_repo by SHA within channel
        existing_asset = self._asset_repo.get_by_sha256(channel_id, file_sha256)
        if existing_asset:
            # Reuse existing asset ID
            asset_id = existing_asset.id
        else:
            asset_create = MediaAssetCreate(
                id=asset_id,
                channel_id=channel_id,
                project_id=project_id,
                scene_id=match.scene_id,
                file_name=safe_fname,
                file_path=storage_key,
                file_sha256=file_sha256,
                file_size_bytes=val_res.file_size_bytes,
                mime_type=f"{asset_type}/{val_res.format_name or 'octet-stream'}",
                asset_type=asset_type,
                asset_category="flow_generated",
                storage_provider="local",
                storage_key=storage_key,
                license_reference=lic_ref,
            )
            self._asset_repo.create(asset_create)

            # Register license explicitly attributing Google Flow
            self._license_repo.create(
                AssetLicenseCreate(
                    id=lic_ref,
                    asset_id=asset_id,
                    channel_id=channel_id,
                    legal_basis="proprietary_ownership",
                    verification_status="verified",
                    commercial_use_allowed=True,
                    attribution_required=False,
                    rights_owner="Google Flow Generated Asset",
                    notes="Imported via Google Flow Inbound Integration (Task 3.13).",
                )
            )

        # 6. Save take in flow_scene_takes table
        # Retrieve prompt_version for this scene if available
        active_prompt = self._prompt_repo.get_scene_prompt(match.scene_id)
        prompt_ver = active_prompt["version"] if active_prompt else match.prompt_version

        self._inbound_repo.create_take(
            take_id=take_id,
            channel_id=channel_id,
            project_id=project_id,
            scene_id=match.scene_id,
            prompt_version=prompt_ver,
            take_number=take_number,
            asset_id=asset_id,
            asset_role=match.asset_role,
            file_sha256=file_sha256,
            source_file_name=full_path.name,
            storage_key=storage_key,
            duration_sec=val_res.duration_sec,
            width=val_res.width,
            height=val_res.height,
            fps=val_res.fps,
            is_active=True,
            metadata=match.raw_metadata,
        )

        return InboundImportItemReport(
            file_name=full_path.name,
            scene_id=match.scene_id,
            take_number=take_number,
            asset_role=match.asset_role,
            status=status,
            sha256=file_sha256,
            storage_key=storage_key,
            asset_id=asset_id,
            take_id=take_id,
            details=f"Take #{take_number} successfully registered and set active.",
        )

    def get_project_flow_status(
        self, channel_id: str, project_id: str
    ) -> Dict[str, Any]:
        """Inspect scenes and their active Flow takes."""
        scenes = self._scene_repo.list_by_project(channel_id, project_id)
        scene_status_list: List[Dict[str, Any]] = []

        for sc in scenes:
            active_take = self._inbound_repo.get_active_take(project_id, sc.id, "video")
            all_takes = self._inbound_repo.list_takes_for_scene(project_id, sc.id, "video")
            active_prompt = self._prompt_repo.get_scene_prompt(sc.id)

            scene_status_list.append({
                "scene_id": sc.id,
                "sequence_number": sc.sequence_number,
                "title": sc.title,
                "prompt_version": active_prompt["version"] if active_prompt else 1,
                "has_video": active_take is not None,
                "active_take_number": active_take["take_number"] if active_take else None,
                "active_take_id": active_take["id"] if active_take else None,
                "total_takes": len(all_takes),
                "resolution": (
                    f"{active_take['width']}x{active_take['height']}"
                    if active_take and active_take["width"] and active_take["height"]
                    else None
                ),
                "duration_sec": active_take["duration_sec"] if active_take else None,
                "fps": active_take["fps"] if active_take else None,
                "storage_key": active_take["storage_key"] if active_take else None,
            })

        missing = [s["scene_id"] for s in scene_status_list if not s["has_video"]]

        return {
            "channel_id": channel_id,
            "project_id": project_id,
            "total_scenes": len(scenes),
            "completed_scenes": len(scenes) - len(missing),
            "missing_scenes": missing,
            "is_complete": len(missing) == 0,
            "scenes": scene_status_list,
        }

    def set_active_take(
        self, project_id: str, scene_id: str, take_number: int, asset_role: str = "video"
    ) -> bool:
        """Switch the active take for a scene."""
        return self._inbound_repo.set_active_take(
            project_id=project_id,
            scene_id=scene_id,
            take_number=take_number,
            asset_role=asset_role,
        )
