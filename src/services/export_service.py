"""Export & Publishing Preparation Service (Task 3.16).

Orchestrates:
1. Strict License Preflight (ADR-008).
2. Platform Export Profile selection (YouTube Shorts, Reels, TikTok, YouTube Standard).
3. Arabic Thumbnail generation (cross-platform fonts, RTL wrapping, contrast styling).
4. Final Media Quality Gates (EBU R128 loudness, True Peak ceiling, video/audio specs).
5. Grounded, non-hallucinated platform metadata generation.
6. Atomic release packaging with manifest and relative ZIP archive.
7. Persistence in SQLite via ExportRepository.
"""
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Union

from src.core.logger import get_logger, log_context
from src.core.exceptions import (
    ValidationException,
    LicenseVerificationException,
    MediaValidationException,
)
from src.database.connection import DatabaseManager
from src.database.models import (
    ExportPackageModel,
    ExportPackageCreate,
    ProjectModel,
    SceneModel,
    ChannelModel,
)
from src.database.repositories.export_repository import ExportRepository
from src.database.repositories.project_repository import ProjectRepository
from src.database.repositories.scene_repository import SceneRepository
from src.database.repositories.channel_repository import ChannelRepository
from src.export.profiles import (
    PlatformExportProfile,
    get_profile_by_id,
    get_all_profiles,
    DEFAULT_PROFILE_ID,
)
from src.export.preflight import LicensePreflightService, PreflightAuditReport
from src.export.quality_gates import MediaQualityGateService, QualityGateReport
from src.export.thumbnail import ArabicThumbnailGenerator
from src.export.metadata_generator import PlatformMetadataGenerator, PlatformMetadataPackage
from src.export.package_assembler import ExportPackageAssembler, calculate_sha256
from src.services.license_service import LicenseService

logger = get_logger("studio.services.export")


class ExportService:
    """Domain service managing end-to-end export package creation and publishing readiness."""

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        export_repository: Optional[ExportRepository] = None,
        license_service: Optional[LicenseService] = None,
        preflight_service: Optional[LicensePreflightService] = None,
        quality_service: Optional[MediaQualityGateService] = None,
        thumbnail_generator: Optional[ArabicThumbnailGenerator] = None,
        package_assembler: Optional[ExportPackageAssembler] = None,
        project_repository: Optional[ProjectRepository] = None,
        scene_repository: Optional[SceneRepository] = None,
        channel_repository: Optional[ChannelRepository] = None,
    ) -> None:
        self.db_manager = db_manager or DatabaseManager()
        self._export_repo = export_repository or ExportRepository(db_manager=self.db_manager)
        self._proj_repo = project_repository or ProjectRepository(db_manager=self.db_manager)
        self._scene_repo = scene_repository or SceneRepository(db_manager=self.db_manager)
        self._chan_repo = channel_repository or ChannelRepository(db_manager=self.db_manager)

        lic_srv = license_service or LicenseService(db_manager=self.db_manager)
        self._preflight_service = preflight_service or LicensePreflightService(license_service=lic_srv)
        self._quality_service = quality_service or MediaQualityGateService()
        self._thumb_gen = thumbnail_generator or ArabicThumbnailGenerator()
        self._assembler = package_assembler or ExportPackageAssembler()

    def list_available_profiles(self) -> List[PlatformExportProfile]:
        """Return all supported platform export profiles."""
        return get_all_profiles()

    def get_profile(self, profile_id: str) -> Optional[PlatformExportProfile]:
        """Fetch a specific platform export profile."""
        return get_profile_by_id(profile_id)

    def run_license_preflight(
        self,
        channel_id: str,
        project_id: str,
    ) -> PreflightAuditReport:
        """Run ADR-008 preflight audit for all project assets."""
        return self._preflight_service.run_preflight_audit(channel_id, project_id)

    def evaluate_video_quality(
        self,
        video_path: Union[str, Path],
        profile_id: str = DEFAULT_PROFILE_ID,
    ) -> QualityGateReport:
        """Evaluate a video against platform profile quality gates."""
        profile = get_profile_by_id(profile_id)
        if not profile:
            raise ValidationException(f"Unknown export profile: '{profile_id}'")
        return self._quality_service.evaluate_media(video_path, profile)

    def generate_arabic_thumbnail(
        self,
        output_path: Union[str, Path],
        headline_ar: str,
        subtitle_ar: Optional[str] = None,
        badge_text: Optional[str] = None,
        background_image_path: Optional[Union[str, Path]] = None,
        width: int = 1080,
        height: int = 1920,
    ) -> Path:
        """Render a publication thumbnail with Arabic typography."""
        return self._thumb_gen.render_thumbnail(
            output_path=output_path,
            headline_ar=headline_ar,
            subtitle_ar=subtitle_ar,
            badge_text=badge_text,
            background_image_path=background_image_path,
            width=width,
            height=height,
        )

    def generate_grounded_metadata(
        self,
        channel_id: str,
        project_id: str,
        profile_id: str = DEFAULT_PROFILE_ID,
    ) -> PlatformMetadataPackage:
        """Generate platform-tailored, grounded metadata from project and channel records."""
        project = self._proj_repo.get_by_id(channel_id, project_id)
        if not project:
            raise ValidationException(f"Project '{project_id}' not found for channel '{channel_id}'.")

        channel = self._chan_repo.get_by_id(channel_id)
        if not channel:
            raise ValidationException(f"Channel '{channel_id}' not found.")

        scenes = self._scene_repo.list_by_project(channel_id, project_id)
        profile = get_profile_by_id(profile_id)
        if not profile:
            raise ValidationException(f"Profile '{profile_id}' not found.")

        return PlatformMetadataGenerator.generate_metadata(project, scenes, channel, profile)

    def create_export_package(
        self,
        channel_id: str,
        project_id: str,
        profile_id: str = DEFAULT_PROFILE_ID,
        video_file_path: Optional[Union[str, Path]] = None,
        thumbnail_file_path: Optional[Union[str, Path]] = None,
        headline_ar: Optional[str] = None,
        badge_text: Optional[str] = None,
        custom_metadata: Optional[Dict[str, Any]] = None,
        subtitles_path: Optional[Union[str, Path]] = None,
        enforce_license_gate: bool = True,
        enforce_quality_gate: bool = True,
    ) -> ExportPackageModel:
        """Create a complete, verified, atomic export package (Task 3.16).

        Raises:
            ValidationException: On invalid parameters or missing source files.
            LicenseVerificationException: If license preflight fails and enforce_license_gate is True.
            MediaValidationException: If quality gate checks fail and enforce_quality_gate is True.
        """
        clean_ch = channel_id.strip()
        clean_proj = project_id.strip()

        with log_context(channel_id=clean_ch, project_id=clean_proj):
            # 1. Fetch channel and project
            project = self._proj_repo.get_by_id(clean_ch, clean_proj)
            if not project:
                raise ValidationException(f"Project '{clean_proj}' not found in channel '{clean_ch}'.")

            channel = self._chan_repo.get_by_id(clean_ch)
            if not channel:
                raise ValidationException(f"Channel '{clean_ch}' not found.")

            profile = get_profile_by_id(profile_id)
            if not profile:
                raise ValidationException(f"Invalid export profile '{profile_id}'.")

            scenes = self._scene_repo.list_by_project(clean_ch, clean_proj)

            # 2. Strict License Preflight Gate (ADR-008)
            preflight_report = self._preflight_service.run_preflight_audit(clean_ch, clean_proj)
            if enforce_license_gate and not preflight_report.passed:
                first_reason = preflight_report.rejection_reasons[0] if preflight_report.rejection_reasons else "License preflight failed."
                logger.error(f"License preflight failed for project '{clean_proj}': {first_reason}")
                raise LicenseVerificationException(
                    asset_id=f"Project:{clean_proj}",
                    reason=f"Strict License Preflight Gate violation: {first_reason}",
                    details=preflight_report.to_dict(),
                )

            # 3. Locate or resolve Video Source File
            resolved_video_path: Optional[Path] = None
            if video_file_path:
                p = Path(video_file_path).resolve()
                if p.is_file():
                    resolved_video_path = p

            if not resolved_video_path:
                # Look in standard project render directories
                candidate_paths = [
                    self._assembler.base_dir / "channels" / clean_ch / "projects" / clean_proj / "final.mp4",
                    self._assembler.base_dir / "channels" / clean_ch / "projects" / clean_proj / f"{clean_proj}.mp4",
                    self._assembler.base_dir / "channels" / clean_ch / "renders" / f"{clean_proj}.mp4",
                ]
                for cand in candidate_paths:
                    if cand.is_file() and cand.stat().st_size > 0:
                        resolved_video_path = cand
                        break

            if not resolved_video_path:
                raise ValidationException(
                    f"No rendered video found for project '{clean_proj}'. "
                    "Render the video before creating an export package, or provide video_file_path."
                )

            # 4. Final Media Quality Gates (Guardrail D)
            quality_report = self._quality_service.evaluate_media(resolved_video_path, profile)
            if enforce_quality_gate and not quality_report.passed:
                fail_summary = "; ".join(quality_report.failure_reasons)
                logger.error(f"Quality gate checks failed for '{resolved_video_path.name}': {fail_summary}")
                raise MediaValidationException(
                    f"Final Media Quality Gate failed: {fail_summary}",
                    file_path=str(resolved_video_path),
                )

            # 5. Arabic Thumbnail Generation (Guardrail C)
            resolved_thumb_path: Optional[Path] = None
            if thumbnail_file_path:
                p = Path(thumbnail_file_path).resolve()
                if p.is_file():
                    resolved_thumb_path = p

            if not resolved_thumb_path:
                # Generate new Arabic thumbnail
                headline = headline_ar or project.title or "فيديو جديد"
                badge = badge_text or channel.name or "حلقة جديدة"
                temp_thumb = self._assembler.base_dir / "channels" / clean_ch / "projects" / clean_proj / "generated_thumbnail.png"
                temp_thumb.parent.mkdir(parents=True, exist_ok=True)

                # Look for background image in scenes or character bible
                bg_candidate: Optional[Path] = None
                for sc in scenes:
                    sc_meta = sc.metadata_json or {}
                    if sc_meta.get("image_path"):
                        ip = Path(sc_meta["image_path"]).resolve()
                        if ip.is_file():
                            bg_candidate = ip
                            break

                self.generate_arabic_thumbnail(
                    output_path=temp_thumb,
                    headline_ar=headline,
                    subtitle_ar=project.description[:50] if project.description else None,
                    badge_text=badge,
                    background_image_path=bg_candidate,
                    width=profile.width,
                    height=profile.height,
                )
                resolved_thumb_path = temp_thumb

            # 6. Generate Grounded Metadata (Guardrail F)
            grounded_meta_pkg = PlatformMetadataGenerator.generate_metadata(
                project, scenes, channel, profile
            )
            final_metadata_dict = grounded_meta_pkg.to_dict()
            if custom_metadata:
                final_metadata_dict.update(custom_metadata)

            # 7. Atomic Package Assembly (Guardrail E)
            package_id = f"pkg_{clean_proj}_{profile_id}_{uuid.uuid4().hex[:6]}"
            sub_path = Path(subtitles_path).resolve() if subtitles_path else None

            package_result = self._assembler.assemble_package(
                channel_id=clean_ch,
                project_id=clean_proj,
                package_id=package_id,
                profile_id=profile_id,
                platform=profile.platform.value,
                video_source_path=resolved_video_path,
                thumbnail_source_path=resolved_thumb_path,
                metadata_dict=final_metadata_dict,
                preflight_report_dict=preflight_report.to_dict(),
                quality_report_dict=quality_report.to_dict(),
                subtitles_source_path=sub_path,
            )

            # 8. Compute Component Hashes for Database Record
            vid_sha = calculate_sha256(resolved_video_path)
            thumb_sha = calculate_sha256(resolved_thumb_path)

            if not preflight_report.passed:
                status_str = "blocked"
            elif not quality_report.passed:
                status_str = "quality_failed"
            else:
                status_str = "completed"

            # 9. Persist in Database
            create_payload = ExportPackageCreate(
                id=package_id,
                channel_id=clean_ch,
                project_id=clean_proj,
                platform=profile.platform.value,
                profile_name=profile.name_en,
                version=1,
                status=status_str,
                video_path=package_result["package_dir"],
                video_sha256=vid_sha,
                thumbnail_path=str(resolved_thumb_path),
                thumbnail_sha256=thumb_sha,
                manifest_path=package_result["manifest_path"],
                zip_path=package_result["zip_path"],
                zip_sha256=package_result["zip_sha256"],
                license_preflight=preflight_report.to_dict(),
                quality_gates=quality_report.to_dict(),
                metadata=final_metadata_dict,
            )

            record = self._export_repo.create_package(create_payload)
            logger.info(f"Export package '{package_id}' created and registered in database.")
            return record

    def get_package(self, package_id: str) -> Optional[ExportPackageModel]:
        """Retrieve export package by ID."""
        return self._export_repo.get_by_id(package_id)

    def list_packages(
        self,
        channel_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> List[ExportPackageModel]:
        """List export packages filtered by channel or project."""
        if project_id:
            return self._export_repo.list_by_project(project_id)
        return self._export_repo.list_by_channel(channel_id)
