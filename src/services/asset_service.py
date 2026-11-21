"""Media Asset Management (MAM) service implementing deduplication, two-phase journaling, and recovery."""
import os
import shutil
import uuid
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any

from src.database.repositories.asset_repository import AssetRepository
from src.database.repositories.license_repository import LicenseRepository
from src.database.repositories.file_operation_repository import FileOperationRepository
from src.database.models import (
    MediaAssetModel,
    MediaAssetCreate,
    MediaAssetUpdate,
    AssetLicenseCreate,
    FileOperationModel,
)
from src.services.audit_service import AuditService
from src.services.recovery_service import RecoveryService
from src.storage.service import StorageService
from src.core.config import get_settings
from src.core.constants import AssetType, LegalBasis, VerificationStatus, FileOperationState, FileOperationType
from src.core.exceptions import (
    AssetNotFoundException,
    AssetInUseException,
    ValidationException,
    PathTraversalSecurityException,
    StudioException,
)
from src.utils.file_utils import calculate_file_sha256, safe_resolve_path
from src.core.logger import get_logger, log_context

logger = get_logger("studio.services.asset")


class AssetService:
    """Central Media Asset Management service providing deduplication, journaling, and safe deletion."""

    def __init__(
        self,
        asset_repository: Optional[AssetRepository] = None,
        license_repository: Optional[LicenseRepository] = None,
        file_operation_repository: Optional[FileOperationRepository] = None,
        audit_service: Optional[AuditService] = None,
        storage_service: Optional[StorageService] = None,
        service_logger: Optional[logging.Logger] = None,
    ) -> None:
        self._asset_repo = asset_repository or AssetRepository()
        self._license_repo = license_repository or LicenseRepository()
        self._file_op_repo = file_operation_repository or FileOperationRepository()
        self._audit = audit_service or AuditService()
        self._storage = storage_service or StorageService()
        self._logger = service_logger or logger

    @property
    def storage(self) -> StorageService:
        """Access underlying StorageService facade."""
        return self._storage

    def ingest_asset(
        self,
        channel_id: str,
        source_path: Path,
        asset_type: AssetType,
        asset_category: str = "general",
        project_id: Optional[str] = None,
        scene_id: Optional[str] = None,
        character_id: Optional[str] = None,
        legal_basis: LegalBasis = LegalBasis.PROPRIETARY_OWNERSHIP,
        proof_file_path: Optional[str] = None,
        actor: str = "system",
    ) -> MediaAssetModel:
        """Ingest and register a media file with SHA-256 deduplication and crash recovery journaling.

        Args:
            channel_id: Channel ID owner.
            source_path: Path to the source file to ingest.
            asset_type: Type of asset (image, audio, video, font).
            asset_category: Organizational category (e.g. b_roll, sfx, turnaround).
            project_id: Optional project link.
            scene_id: Optional scene link.
            character_id: Optional character link.
            legal_basis: Legal basis for license record.
            proof_file_path: Path to license proof document if available.
            actor: Operator or user ingesting the file.

        Returns:
            MediaAssetModel: Persisted asset model.
        """
        clean_ch = channel_id.strip()
        settings = get_settings()
        channel_dir = settings.channels_root / clean_ch

        if not source_path.exists() or not source_path.is_file():
            raise ValidationException(f"Source file does not exist: {source_path}")

        file_sha256 = calculate_file_sha256(source_path)
        file_size = source_path.stat().st_size
        file_name = source_path.name
        asset_id = f"ast_{uuid.uuid4().hex[:12]}"
        now_utc = source_path.stat().st_mtime

        with log_context(channel_id=clean_ch):
            # 1. Deduplication check within the channel
            existing_match = self._asset_repo.get_by_sha256(clean_ch, file_sha256)
            if existing_match:
                self._logger.info(
                    f"Deduplication match found for SHA-256 '{file_sha256[:10]}...'. "
                    f"Reusing existing physical file: {existing_match.file_path}"
                )
                lic_ref = f"lic_{uuid.uuid4().hex[:12]}"
                # Create asset entry pointing to existing physical path
                asset_create = MediaAssetCreate(
                    id=asset_id,
                    channel_id=clean_ch,
                    project_id=project_id,
                    scene_id=scene_id,
                    character_id=character_id,
                    file_name=file_name,
                    file_path=existing_match.file_path,
                    file_sha256=file_sha256,
                    file_size_bytes=file_size,
                    mime_type=existing_match.mime_type,
                    asset_type=asset_type,
                    asset_category=asset_category,
                    storage_provider=getattr(existing_match, "storage_provider", "local") or "local",
                    storage_key=getattr(existing_match, "storage_key", None) or existing_match.file_path,
                    license_reference=lic_ref,
                )
                created_asset = self._asset_repo.create(asset_create)

                # Register license
                self._license_repo.create(
                    AssetLicenseCreate(
                        id=lic_ref,
                        asset_id=created_asset.id,
                        channel_id=clean_ch,
                        legal_basis=legal_basis,
                        verification_status=VerificationStatus.PENDING_REVIEW,
                        proof_file_path=proof_file_path,
                    )
                )

                # Audit record
                self._audit.record_event(
                    channel_id=clean_ch,
                    action="INGEST_ASSET_DEDUP",
                    actor=actor,
                    payload={"asset_id": created_asset.id, "sha256": file_sha256, "reused_path": existing_match.file_path},
                    idempotency_key=f"INGEST_DEDUP:{created_asset.id}",
                )
                return created_asset

            # 2. Unique file ingestion via Crash Recovery Journal
            type_folder = f"{asset_type.value}s" if not asset_type.value.endswith("s") else asset_type.value
            target_rel_path = Path("media_vault") / "originals" / type_folder / f"{asset_id}_{file_name}"
            target_abs_path = safe_resolve_path(channel_dir, target_rel_path)

            op_id = f"op_{uuid.uuid4().hex}"
            temp_rel_path = Path("media_vault") / "cache" / f"upload_{op_id}.tmp"
            temp_abs_path = safe_resolve_path(channel_dir, temp_rel_path)
            temp_abs_path.parent.mkdir(parents=True, exist_ok=True)
            target_abs_path.parent.mkdir(parents=True, exist_ok=True)

            # Step 1: PENDING journal
            op_record = FileOperationModel(
                id=op_id,
                operation_type=FileOperationType.IMPORT,
                channel_id=clean_ch,
                asset_id=asset_id,
                source_path=str(source_path),
                temp_path=str(temp_abs_path),
                target_path=str(target_abs_path),
                file_sha256=file_sha256,
                file_size_bytes=file_size,
                state=FileOperationState.PENDING,
                error_message=None,
                created_at=str(now_utc),
                updated_at=str(now_utc),
            )
            self._file_op_repo.create(op_record)

            try:
                # Step 2: Copy to temp location & verify
                shutil.copy2(source_path, temp_abs_path)
                written_sha = calculate_file_sha256(temp_abs_path)
                if written_sha != file_sha256:
                    raise StudioException("Checksum mismatch after copying to temporary holding path.")

                # Update journal to FILE_WRITTEN
                self._file_op_repo.update_state(op_id, FileOperationState.FILE_WRITTEN)

                # Step 3: Insert DB records (asset & license) & DB_COMMITTED
                mime = f"{asset_type.value}/{source_path.suffix.lstrip('.')}"
                lic_ref = f"lic_{uuid.uuid4().hex[:12]}"
                canonical_storage_key = StorageService.build_channel_asset_key(
                    channel_id=clean_ch,
                    category=type_folder,
                    file_name=file_name,
                    asset_id=asset_id,
                )
                asset_create = MediaAssetCreate(
                    id=asset_id,
                    channel_id=clean_ch,
                    project_id=project_id,
                    scene_id=scene_id,
                    character_id=character_id,
                    file_name=file_name,
                    file_path=str(target_rel_path),
                    file_sha256=file_sha256,
                    file_size_bytes=file_size,
                    mime_type=mime,
                    asset_type=asset_type,
                    asset_category=asset_category,
                    storage_provider="local",
                    storage_key=canonical_storage_key,
                    license_reference=lic_ref,
                )
                created_asset = self._asset_repo.create(asset_create)

                self._license_repo.create(
                    AssetLicenseCreate(
                        id=lic_ref,
                        asset_id=created_asset.id,
                        channel_id=clean_ch,
                        legal_basis=legal_basis,
                        verification_status=VerificationStatus.PENDING_REVIEW,
                        proof_file_path=proof_file_path,
                    )
                )

                self._file_op_repo.update_state(op_id, FileOperationState.DB_COMMITTED)

                # Step 4: Atomic rename from temp to target
                os.replace(temp_abs_path, target_abs_path)
                self._file_op_repo.update_state(op_id, FileOperationState.FILE_FINALIZED)

                # Step 5: Mark COMPLETED
                self._file_op_repo.update_state(op_id, FileOperationState.COMPLETED)

                # Audit record
                self._audit.record_event(
                    channel_id=clean_ch,
                    action="INGEST_ASSET_UNIQUE",
                    actor=actor,
                    payload={"asset_id": created_asset.id, "sha256": file_sha256, "path": str(target_rel_path)},
                    idempotency_key=f"INGEST_UNIQUE:{created_asset.id}",
                )
                return created_asset

            except Exception as e:
                self._logger.error(f"Asset ingestion failed for '{source_path}': {e}", exc_info=True)
                self._file_op_repo.update_state(op_id, FileOperationState.FAILED, error_message=str(e))
                if temp_abs_path.exists():
                    temp_abs_path.unlink(missing_ok=True)
                raise

    def get_asset(self, channel_id: str, asset_id: str) -> MediaAssetModel:
        """Retrieve an asset by ID."""
        clean_ch = channel_id.strip()
        asset = self._asset_repo.get_by_id(clean_ch, asset_id.strip())
        if not asset:
            raise AssetNotFoundException(asset_id)
        return asset

    def get_asset_by_storage_key(self, channel_id: str, storage_key: str) -> Optional[MediaAssetModel]:
        """Retrieve an asset by storage key."""
        clean_ch = channel_id.strip()
        return self._asset_repo.get_by_storage_key(clean_ch, storage_key.strip())

    def list_assets(
        self,
        channel_id: str,
        asset_type: Optional[AssetType] = None,
        include_archived: bool = False,
    ) -> List[MediaAssetModel]:
        """List assets for a channel."""
        return self._asset_repo.list_by_channel(channel_id.strip(), asset_type, include_archived)

    def disable_asset(self, channel_id: str, asset_id: str, actor: str = "system") -> MediaAssetModel:
        """Level 1: Disable asset, preventing future scene usage while preserving files and links."""
        clean_ch = channel_id.strip()
        self.get_asset(clean_ch, asset_id)
        updated = self._asset_repo.update(clean_ch, asset_id, MediaAssetUpdate(is_active=False))

        self._audit.record_event(
            channel_id=clean_ch,
            action="DISABLE_ASSET",
            actor=actor,
            payload={"asset_id": asset_id},
            idempotency_key=f"DISABLE:{asset_id}",
        )
        return updated

    def archive_asset(self, channel_id: str, asset_id: str, actor: str = "system") -> MediaAssetModel:
        """Level 2: Archive asset, hiding it from library picker while retaining originals & proofs."""
        clean_ch = channel_id.strip()
        self.get_asset(clean_ch, asset_id)
        updated = self._asset_repo.update(clean_ch, asset_id, MediaAssetUpdate(is_archived=True, is_active=False))

        self._audit.record_event(
            channel_id=clean_ch,
            action="ARCHIVE_ASSET",
            actor=actor,
            payload={"asset_id": asset_id},
            idempotency_key=f"ARCHIVE:{asset_id}",
        )
        return updated

    def remove_association(
        self,
        channel_id: str,
        asset_id: str,
        project_id: Optional[str] = None,
        scene_id: Optional[str] = None,
        character_id: Optional[str] = None,
    ) -> MediaAssetModel:
        """Level 3: Unlink asset from specific project, scene, or character without touching physical file."""
        clean_ch = channel_id.strip()
        self.get_asset(clean_ch, asset_id)
        updates = MediaAssetUpdate(project_id=project_id, scene_id=scene_id, character_id=character_id)
        return self._asset_repo.update(clean_ch, asset_id, updates)

    def purge_asset_physical(self, channel_id: str, asset_id: str, actor: str = "admin") -> bool:
        """Level 4: Permanent physical deletion of asset.

        Strict Conditions:
        1. Must query active references across scenes, projects, and channels. If any active reference exists -> raises AssetInUseException.
        2. Permanent license proofs in licenses/proofs/ and audit records are NEVER deleted!
        """
        clean_ch = channel_id.strip()
        asset = self.get_asset(clean_ch, asset_id)

        # Active references check
        refs = self._asset_repo.get_active_references(clean_ch, asset_id)
        has_refs = any(len(v) > 0 for v in refs.values())
        if has_refs:
            raise AssetInUseException(asset_id, active_references=refs)

        # Check if other asset records share the exact same physical file (deduplication safeguard)
        settings = get_settings()
        file_abs_path = safe_resolve_path(settings.channels_root / clean_ch, Path(asset.file_path))

        with log_context(channel_id=clean_ch):
            self._logger.warning(f"Purging asset '{asset_id}' physically from channel '{clean_ch}'.")

            # Delete DB record
            self._asset_repo.delete(clean_ch, asset_id)

            # Check if any remaining assets point to the same file
            remaining = self._asset_repo.get_by_sha256(clean_ch, asset.file_sha256)
            if not remaining and file_abs_path.exists():
                file_abs_path.unlink(missing_ok=True)
                self._logger.info(f"Physical file '{file_abs_path}' removed from disk.")

            # Record audit event (license proofs are retained permanently)
            self._audit.record_event(
                channel_id=clean_ch,
                action="PURGE_ASSET_PHYSICAL",
                actor=actor,
                payload={"asset_id": asset_id, "sha256": asset.file_sha256, "file_path": asset.file_path},
                idempotency_key=f"PURGE:{asset_id}",
            )
            return True

    def reconcile_recovery(self, channel_id: Optional[str] = None) -> int:
        """Scan file_operations and perform crash recovery according to the recovery matrix."""
        recovery_svc = RecoveryService(
            file_operation_repository=self._file_op_repo,
            asset_repository=self._asset_repo,
            audit_service=self._audit,
            service_logger=self._logger,
        )
        summary = recovery_svc.reconcile_recovery(channel_id)
        return summary.reconciled_ops

