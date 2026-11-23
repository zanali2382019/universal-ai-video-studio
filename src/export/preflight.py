"""Strict License Preflight Service enforcing ADR-008 legal basis verification.

Ensures that every asset (video, voice, music, SFX, logos, watermarks, reference images)
undergoes rigorous license provenance checking before External Final Export and final package approval.
"""
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

from src.core.constants import VerificationStatus, LegalBasis
from src.core.exceptions import LicenseVerificationException
from src.core.logger import get_logger, log_context
from src.database.models import MediaAssetModel
from src.database.repositories.asset_repository import AssetRepository
from src.database.repositories.scene_repository import SceneRepository
from src.database.repositories.channel_repository import ChannelRepository
from src.services.license_service import LicenseService

logger = get_logger("studio.export.preflight")


class AssetPreflightRecord(BaseModel):
    """Individual asset preflight check record."""
    asset_id: str
    asset_name: str
    asset_type: str
    file_path: Optional[str] = None
    file_sha256: Optional[str] = None
    legal_basis: Optional[str] = None
    verification_status: str
    commercial_use_allowed: bool
    proof_provided: bool
    passed: bool
    failure_reason: Optional[str] = None


class PreflightAuditReport(BaseModel):
    """Complete preflight audit report for project assets."""
    project_id: str
    channel_id: str
    passed: bool
    total_assets: int
    verified_count: int
    unverified_count: int
    hard_locked: bool = False
    lock_reason: Optional[str] = None
    rejection_reasons: List[str] = Field(default_factory=list)
    asset_details: List[AssetPreflightRecord] = Field(default_factory=list)
    checked_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class LicensePreflightService:
    """Rigorous preflight license verification service."""

    def __init__(
        self,
        license_service: LicenseService,
        asset_repo: Optional[AssetRepository] = None,
        scene_repo: Optional[SceneRepository] = None,
        channel_repo: Optional[ChannelRepository] = None,
    ) -> None:
        self._license_service = license_service
        self._asset_repo = asset_repo or AssetRepository(db_manager=license_service.db_manager)
        self._scene_repo = scene_repo or SceneRepository(db_manager=license_service.db_manager)
        self._channel_repo = channel_repo or ChannelRepository(db_manager=license_service.db_manager)

    def collect_project_asset_ids(self, channel_id: str, project_id: str) -> List[str]:
        """Collect all distinct asset IDs associated with a project.

        Gathers from:
        1. Media assets directly tagged with project_id in MAM.
        2. Media assets referenced in project scene metadata (voice, visual, music, sfx).
        3. Channel brand assets (watermark, logo) if configured.
        """
        asset_ids: set[str] = set()

        # 1. Direct project assets in MAM
        project_assets = self._asset_repo.list_by_project(channel_id, project_id)
        for a in project_assets:
            if a.is_active:
                asset_ids.add(a.id)

        # 2. Scene-referenced assets
        scenes = self._scene_repo.list_by_project(channel_id, project_id)
        for sc in scenes:
            meta = sc.metadata_json or {}
            # Voiceover asset
            if meta.get("voice_asset_id"):
                asset_ids.add(str(meta["voice_asset_id"]))
            if meta.get("assigned_asset_id"):
                asset_ids.add(str(meta["assigned_asset_id"]))
            # Visual asset
            if meta.get("visual_asset_id"):
                asset_ids.add(str(meta["visual_asset_id"]))
            if meta.get("image_asset_id"):
                asset_ids.add(str(meta["image_asset_id"]))
            if meta.get("video_asset_id"):
                asset_ids.add(str(meta["video_asset_id"]))
            # Music and SFX
            if meta.get("music_asset_id"):
                asset_ids.add(str(meta["music_asset_id"]))
            for sfx_id in meta.get("sfx_asset_ids", []):
                if sfx_id:
                    asset_ids.add(str(sfx_id))
            for ref_id in meta.get("reference_image_ids", []):
                if ref_id:
                    asset_ids.add(str(ref_id))

        # 3. Channel watermark / logo assets
        channel = self._channel_repo.get_by_id(channel_id)
        if channel:
            prof = getattr(channel, "profile_json", {}) or {}
            if isinstance(prof, dict):
                if prof.get("watermark_asset_id"):
                    asset_ids.add(str(prof["watermark_asset_id"]))
                if prof.get("logo_asset_id"):
                    asset_ids.add(str(prof["logo_asset_id"]))
            wm_path = getattr(channel, "watermark_path", None)
            if wm_path and "/" not in wm_path and "\\" not in wm_path:
                asset_ids.add(wm_path)

        return sorted(list(asset_ids))

    def run_preflight_audit(
        self,
        channel_id: str,
        project_id: str,
        asset_ids: Optional[List[str]] = None,
    ) -> PreflightAuditReport:
        """Execute comprehensive license preflight audit (ADR-008).

        Returns PreflightAuditReport detailing pass/fail status and all asset evaluations.
        """
        clean_ch = channel_id.strip()
        clean_proj = project_id.strip()

        with log_context(channel_id=clean_ch, project_id=clean_proj):
            # Check hard lock first
            is_locked, lock_reason = self._license_service._audit.is_export_locked(clean_ch)
            if is_locked:
                logger.critical(f"Export gate HARD LOCKED on channel '{clean_ch}': {lock_reason}")
                return PreflightAuditReport(
                    project_id=clean_proj,
                    channel_id=clean_ch,
                    passed=False,
                    total_assets=0,
                    verified_count=0,
                    unverified_count=0,
                    hard_locked=True,
                    lock_reason=lock_reason,
                    rejection_reasons=[f"Export gate is HARD LOCKED for channel: {lock_reason}"],
                    asset_details=[],
                )

            if asset_ids is None:
                asset_ids = self.collect_project_asset_ids(clean_ch, clean_proj)

            records: List[AssetPreflightRecord] = []
            rejection_reasons: List[str] = []
            verified_count = 0
            unverified_count = 0

            for aid in asset_ids:
                media_asset = self._asset_repo.get_by_id(clean_ch, aid)
                lic = self._license_service.get_license_by_asset(clean_ch, aid)

                name = media_asset.file_name if media_asset else aid
                atype = media_asset.asset_type.value if media_asset else "unknown"
                fpath = media_asset.file_path if media_asset else None
                fsha = media_asset.file_sha256 if media_asset else None

                if not lic:
                    fail_msg = f"Asset '{aid}' ({name}) has no registered license record."
                    rejection_reasons.append(fail_msg)
                    unverified_count += 1
                    records.append(AssetPreflightRecord(
                        asset_id=aid,
                        asset_name=name,
                        asset_type=atype,
                        file_path=fpath,
                        file_sha256=fsha,
                        legal_basis=None,
                        verification_status=VerificationStatus.PENDING_REVIEW.value,
                        commercial_use_allowed=False,
                        proof_provided=False,
                        passed=False,
                        failure_reason=fail_msg,
                    ))
                    continue

                # License exists, verify status
                legal_basis_val = lic.legal_basis.value if lic.legal_basis else None
                has_proof = bool(lic.proof_file_path or lic.proof_sha256 or lic.source_uri)
                failures: List[str] = []

                if lic.verification_status != VerificationStatus.VERIFIED:
                    failures.append(
                        f"License status is '{lic.verification_status.value}' (must be '{VerificationStatus.VERIFIED.value}')."
                    )

                if not lic.commercial_use_allowed:
                    failures.append("Commercial use rights are not granted.")

                if failures:
                    fail_str = f"Asset '{aid}' ({name}): " + " | ".join(failures)
                    rejection_reasons.append(fail_str)
                    unverified_count += 1
                    records.append(AssetPreflightRecord(
                        asset_id=aid,
                        asset_name=name,
                        asset_type=atype,
                        file_path=fpath,
                        file_sha256=fsha,
                        legal_basis=legal_basis_val,
                        verification_status=lic.verification_status.value,
                        commercial_use_allowed=lic.commercial_use_allowed,
                        proof_provided=has_proof,
                        passed=False,
                        failure_reason=fail_str,
                    ))
                else:
                    verified_count += 1
                    records.append(AssetPreflightRecord(
                        asset_id=aid,
                        asset_name=name,
                        asset_type=atype,
                        file_path=fpath,
                        file_sha256=fsha,
                        legal_basis=legal_basis_val,
                        verification_status=lic.verification_status.value,
                        commercial_use_allowed=lic.commercial_use_allowed,
                        proof_provided=has_proof,
                        passed=True,
                        failure_reason=None,
                    ))

            passed = (unverified_count == 0) and not is_locked
            report = PreflightAuditReport(
                project_id=clean_proj,
                channel_id=clean_ch,
                passed=passed,
                total_assets=len(asset_ids),
                verified_count=verified_count,
                unverified_count=unverified_count,
                hard_locked=is_locked,
                lock_reason=lock_reason,
                rejection_reasons=rejection_reasons,
                asset_details=records,
            )
            return report

    def assert_export_preflight(
        self,
        channel_id: str,
        project_id: str,
        asset_ids: Optional[List[str]] = None,
    ) -> PreflightAuditReport:
        """Run audit and raise LicenseVerificationException immediately if failed.

        Zero UI/CLI bypass possible.
        """
        report = self.run_preflight_audit(channel_id, project_id, asset_ids)
        if not report.passed:
            first_reason = report.rejection_reasons[0] if report.rejection_reasons else "Preflight license audit failed."
            raise LicenseVerificationException(
                asset_id=f"Project:{project_id}",
                reason=first_reason,
                details={
                    "total_assets": report.total_assets,
                    "unverified_count": report.unverified_count,
                    "rejections": report.rejection_reasons[:5],
                },
            )
        return report
