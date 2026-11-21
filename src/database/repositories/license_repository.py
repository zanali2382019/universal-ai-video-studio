"""License repository for managing asset licenses, legal basis, and verification status."""
import sqlite3
from datetime import datetime, timezone
from typing import Optional, List

from src.database.repositories.base import BaseRepository
from src.database.models import AssetLicenseModel, AssetLicenseCreate, AssetLicenseUpdate
from src.core.constants import VerificationStatus
from src.core.exceptions import ValidationException
from src.core.logger import get_logger

logger = get_logger("studio.database.license_repository")


class LicenseRepository(BaseRepository):
    """Handles license database records, legal basis tracking, and verification audits."""

    def create(self, license_data: AssetLicenseCreate) -> AssetLicenseModel:
        """Register a new asset license record.

        Args:
            license_data: AssetLicenseCreate payload.

        Returns:
            AssetLicenseModel: Persisted license record.
        """
        now_utc = datetime.now(timezone.utc).isoformat()

        sql = """
        INSERT INTO asset_licenses (
            id, asset_id, channel_id, legal_basis, verification_status,
            commercial_use_allowed, attribution_required, attribution_text,
            source_uri, rights_owner, proof_file_path, proof_sha256, notes,
            created_at, updated_at
        ) VALUES (
            :id, :asset_id, :channel_id, :legal_basis, :verification_status,
            :commercial_use_allowed, :attribution_required, :attribution_text,
            :source_uri, :rights_owner, :proof_file_path, :proof_sha256, :notes,
            :created_at, :updated_at
        );
        """

        params = {
            "id": license_data.id,
            "asset_id": license_data.asset_id,
            "channel_id": license_data.channel_id,
            "legal_basis": license_data.legal_basis.value,
            "verification_status": license_data.verification_status.value,
            "commercial_use_allowed": 1 if license_data.commercial_use_allowed else 0,
            "attribution_required": 1 if license_data.attribution_required else 0,
            "attribution_text": license_data.attribution_text,
            "source_uri": license_data.source_uri,
            "rights_owner": license_data.rights_owner,
            "proof_file_path": license_data.proof_file_path,
            "proof_sha256": license_data.proof_sha256,
            "notes": license_data.notes,
            "created_at": now_utc,
            "updated_at": now_utc,
        }

        try:
            with self.db_manager.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute(sql, params)
        except sqlite3.IntegrityError as e:
            err_msg = str(e).lower()
            if "asset_id" in err_msg or "unique" in err_msg:
                raise ValidationException(
                    f"A license record already exists for asset '{license_data.asset_id}'.",
                    details={"asset_id": license_data.asset_id},
                ) from e
            raise ValidationException(f"Database error registering license: {e}") from e

        created = self.get_by_id(license_data.channel_id, license_data.id)
        if not created:
            raise ValidationException(f"Failed to retrieve created license '{license_data.id}'.")
        return created

    def get_by_asset_id(self, channel_id: str, asset_id: str) -> Optional[AssetLicenseModel]:
        """Retrieve license details for a specific asset scoped to a channel."""
        sql = "SELECT * FROM asset_licenses WHERE asset_id = ? AND channel_id = ?;"
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(sql, (asset_id, channel_id)).fetchone()
            if not row:
                return None
            return AssetLicenseModel.from_row(row)

    def get_by_id(self, channel_id: str, license_id: str) -> Optional[AssetLicenseModel]:
        """Retrieve license by its unique ID scoped to a channel."""
        sql = "SELECT * FROM asset_licenses WHERE id = ? AND channel_id = ?;"
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(sql, (license_id, channel_id)).fetchone()
            if not row:
                return None
            return AssetLicenseModel.from_row(row)

    def list_by_channel(
        self,
        channel_id: str,
        status: Optional[VerificationStatus] = None,
    ) -> List[AssetLicenseModel]:
        """List licenses for a channel with optional status filter."""
        clauses = ["channel_id = ?"]
        params = [channel_id]

        if status is not None:
            clauses.append("verification_status = ?")
            params.append(status.value)

        sql = f"SELECT * FROM asset_licenses WHERE {' AND '.join(clauses)} ORDER BY created_at DESC;"
        with self.db_manager.connection() as conn:
            cursor = conn.cursor()
            rows = cursor.execute(sql, params).fetchall()
            return [AssetLicenseModel.from_row(r) for r in rows]

    def update(
        self,
        channel_id: str,
        license_id: str,
        updates: AssetLicenseUpdate,
    ) -> AssetLicenseModel:
        """Update license properties and verification status."""
        existing = self.get_by_id(channel_id, license_id)
        if not existing:
            raise ValidationException(f"License '{license_id}' not found.")

        update_dict = updates.model_dump(exclude_unset=True)
        if not update_dict:
            return existing

        fields: List[str] = []
        params = {"license_id": license_id, "channel_id": channel_id}

        for k, v in update_dict.items():
            if k in ("legal_basis", "verification_status") and hasattr(v, "value"):
                fields.append(f"{k} = :{k}")
                params[k] = v.value
            elif k in ("commercial_use_allowed", "attribution_required") and isinstance(v, bool):
                fields.append(f"{k} = :{k}")
                params[k] = 1 if v else 0
            else:
                fields.append(f"{k} = :{k}")
                params[k] = v

        now_utc = datetime.now(timezone.utc).isoformat()
        fields.append("updated_at = :updated_at")
        params["updated_at"] = now_utc

        # If transitioning to verified, stamp verified_at
        if updates.verification_status == VerificationStatus.VERIFIED and not existing.verified_at:
            fields.append("verified_at = :verified_at")
            params["verified_at"] = now_utc

        sql = f"UPDATE asset_licenses SET {', '.join(fields)} WHERE id = :license_id AND channel_id = :channel_id;"

        with self.db_manager.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)

        updated = self.get_by_id(channel_id, license_id)
        return updated or existing

    def delete(self, channel_id: str, license_id: str) -> bool:
        """Delete a license record."""
        sql = "DELETE FROM asset_licenses WHERE id = ? AND channel_id = ?;"
        with self.db_manager.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (license_id, channel_id))
            return cursor.rowcount > 0

