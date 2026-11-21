"""License management service enforcing legal basis verification and strict export gating."""
import logging
from typing import Optional, List, Dict, Any, Union
from pydantic import ValidationError

from src.database.connection import DatabaseManager
from src.database.repositories.license_repository import LicenseRepository
from src.database.repositories.audit_repository import AuditRepository
from src.database.models import AssetLicenseModel, AssetLicenseCreate, AssetLicenseUpdate
from src.services.audit_service import AuditService
from src.core.constants import VerificationStatus, LegalBasis
from src.core.exceptions import LicenseVerificationException, ValidationException
from src.core.logger import get_logger, log_context

logger = get_logger("studio.services.license")


class LicenseService:
    """Manages legal provenance, license verification, and the strict Export Gate."""

    def __init__(
        self,
        license_repository: Optional[LicenseRepository] = None,
        audit_service: Optional[AuditService] = None,
        service_logger: Optional[logging.Logger] = None,
        db_manager: Optional[DatabaseManager] = None,
    ) -> None:
        self.db_manager = db_manager or (license_repository and license_repository.db_manager)
        self._repo = license_repository or LicenseRepository(db_manager=self.db_manager)
        self._audit = audit_service or AuditService(
            audit_repository=AuditRepository(db_manager=self.db_manager)
        )
        self._logger = service_logger or logger

    def register_license(
        self,
        channel_id: str,
        license_input: Union[AssetLicenseCreate, Dict[str, Any]],
        actor: str = "system",
    ) -> AssetLicenseModel:
        """Register initial license record for an asset (defaults to pending_review)."""
        clean_ch = channel_id.strip()

        if isinstance(license_input, dict):
            payload = dict(license_input)
            payload["channel_id"] = clean_ch
            try:
                data = AssetLicenseCreate(**payload)
            except ValidationError as e:
                raise ValidationException(f"Invalid license payload: {e}") from e
        elif isinstance(license_input, AssetLicenseCreate):
            data = license_input
        else:
            raise ValidationException("Unsupported license input payload.")

        with log_context(channel_id=clean_ch):
            self._logger.info(f"Registering license for asset '{data.asset_id}' (basis={data.legal_basis.value}).")
            created = self._repo.create(data)

            # Record in audit log
            idemp = f"REGISTER_LICENSE:{data.id}:{data.asset_id}"
            self._audit.record_event(
                channel_id=clean_ch,
                action="REGISTER_LICENSE",
                actor=actor,
                payload={
                    "license_id": created.id,
                    "asset_id": created.asset_id,
                    "legal_basis": created.legal_basis.value,
                    "verification_status": created.verification_status.value,
                },
                idempotency_key=idemp,
            )
            return created

    def verify_license(
        self,
        channel_id: str,
        license_id: str,
        legal_basis: LegalBasis,
        verified_by: str,
        proof_file_path: Optional[str] = None,
        proof_sha256: Optional[str] = None,
        commercial_use_allowed: bool = True,
        notes: Optional[str] = None,
    ) -> AssetLicenseModel:
        """Verify an asset license with documented legal basis.

        Important: Mere user assertion of 'I accept responsibility' is strictly rejected.
        A valid, documented legal basis is required.
        """
        clean_ch = channel_id.strip()
        if not verified_by or not verified_by.strip():
            raise ValidationException("Verification requires an identifiable actor (verified_by).")

        existing = self._repo.get_by_id(clean_ch, license_id)
        if not existing:
            raise ValidationException(f"License record '{license_id}' not found.")

        # Reject mere self-assertion of responsibility without proof/source evidence (ADR-008)
        if notes and any(phrase in notes.lower() for phrase in ["i accept responsibility", "user accepts responsibility", "at own risk"]):
            if not proof_file_path and not proof_sha256 and not existing.source_uri:
                raise ValidationException(
                    "Mere assertion of responsibility without documented legal basis or proof evidence is strictly rejected (ADR-008)."
                )

        if legal_basis == LegalBasis.EXPLICIT_USER_OVERRIDE_EXCEPTION and not proof_file_path and not notes:
            raise ValidationException("Explicit user override exception requires documented justification in notes or proof.")

        updates = AssetLicenseUpdate(
            legal_basis=legal_basis,
            verification_status=VerificationStatus.VERIFIED,
            commercial_use_allowed=commercial_use_allowed,
            proof_file_path=proof_file_path,
            proof_sha256=proof_sha256,
            notes=notes,
            verified_by=verified_by,
        )

        with log_context(channel_id=clean_ch):
            self._logger.info(
                f"Verifying license '{license_id}' for asset '{existing.asset_id}' by '{verified_by}'."
            )
            updated = self._repo.update(clean_ch, license_id, updates)

            idemp = f"VERIFY_LEGAL_BASIS:{license_id}:{legal_basis.value}:{proof_sha256 or 'none'}"
            self._audit.record_event(
                channel_id=clean_ch,
                action="VERIFY_LEGAL_BASIS",
                actor=verified_by,
                payload={
                    "license_id": updated.id,
                    "asset_id": updated.asset_id,
                    "legal_basis": updated.legal_basis.value,
                    "proof_sha256": proof_sha256,
                    "status": updated.verification_status.value,
                    "commercial_use_allowed": updated.commercial_use_allowed,
                },
                idempotency_key=idemp,
            )
            return updated

    def restrict_license(
        self,
        channel_id: str,
        license_id: str,
        reason: str,
        actor: str = "system",
    ) -> AssetLicenseModel:
        """Mark a license as restricted, blocking export."""
        clean_ch = channel_id.strip()
        existing = self._repo.get_by_id(clean_ch, license_id)
        if not existing:
            raise ValidationException(f"License '{license_id}' not found.")

        updates = AssetLicenseUpdate(
            verification_status=VerificationStatus.RESTRICTED,
            commercial_use_allowed=False,
            notes=reason,
        )
        updated = self._repo.update(clean_ch, license_id, updates)

        idemp = f"RESTRICT_LICENSE:{license_id}:{reason}"
        self._audit.record_event(
            channel_id=clean_ch,
            action="RESTRICT_LICENSE",
            actor=actor,
            payload={"license_id": license_id, "asset_id": existing.asset_id, "reason": reason},
            idempotency_key=idemp,
        )
        return updated

    def get_license_by_asset(self, channel_id: str, asset_id: str) -> Optional[AssetLicenseModel]:
        """Retrieve license for an asset."""
        return self._repo.get_by_asset_id(channel_id.strip(), asset_id.strip())

    def check_export_eligibility(
        self,
        channel_id: str,
        asset_ids: List[str],
    ) -> bool:
        """Enforce the Strict Export Gate (ADR-008).

        Returns True if all assets have verified commercial licenses and export gate is unlocked.

        Raises:
            LicenseVerificationException: If any asset is unverified, restricted, or export is locked.
        """
        clean_ch = channel_id.strip()

        with log_context(channel_id=clean_ch):
            # 1. Check if channel export gate is hard-locked due to audit tampering
            is_locked, lock_reason = self._audit.is_export_locked(clean_ch)
            if is_locked:
                self._logger.critical(
                    f"Export attempt blocked on channel '{clean_ch}': Export gate is HARD LOCKED ({lock_reason})."
                )
                raise LicenseVerificationException(
                    asset_id="ALL",
                    reason=f"Export gate is HARD LOCKED for channel '{clean_ch}': {lock_reason}",
                    details={"lock_reason": lock_reason},
                )

            # 2. Check each asset
            for asset_id in asset_ids:
                lic = self._repo.get_by_asset_id(clean_ch, asset_id)
                if not lic:
                    raise LicenseVerificationException(
                        asset_id=asset_id,
                        reason="No license record found for asset. Unregistered assets cannot be exported.",
                    )

                if lic.verification_status != VerificationStatus.VERIFIED:
                    raise LicenseVerificationException(
                        asset_id=asset_id,
                        reason=f"Asset license is not verified (current status: '{lic.verification_status.value}'). "
                        "Only verified assets can be exported.",
                        details={"status": lic.verification_status.value, "legal_basis": lic.legal_basis.value},
                    )

                if not lic.commercial_use_allowed:
                    raise LicenseVerificationException(
                        asset_id=asset_id,
                        reason="Asset does not have commercial use rights granted.",
                    )

            self._logger.info(f"Export gate cleared successfully for {len(asset_ids)} asset(s).")
            return True

