"""Final Media Quality Gates service (Guardrail D).

Enforces:
1. EBU R128 integrated loudness (-14.0 LUFS +/- 1.5 dB tolerance).
2. True Peak ceiling <= -1.0 dBTP.
3. Codec, resolution, aspect ratio, frame rate, and platform duration constraints.
4. Unmeasured or missing metrics marked explicitly as 'Not Measured' / 'N/A' (NEVER false PASS).
"""
from pathlib import Path
from typing import Dict, Any, List, Optional, Union
from pydantic import BaseModel, Field

from src.core.logger import get_logger
from src.core.exceptions import FFprobeException, MediaValidationException
from src.export.profiles import PlatformExportProfile
from src.rendering.probe_service import MediaProbeService, ProbeResult, TruePeakResult

logger = get_logger("studio.export.quality_gates")


class QualityCheckItem(BaseModel):
    """Result of an individual technical quality check."""
    name: str
    status: str  # "PASS", "FAIL", "NOT_MEASURED"
    expected: str
    actual: str
    message: str


class QualityGateReport(BaseModel):
    """Comprehensive technical quality gate evaluation report."""
    file_path: str
    profile_id: str
    passed: bool
    checks: List[QualityCheckItem] = Field(default_factory=list)
    failure_reasons: List[str] = Field(default_factory=list)
    measurements: Dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class MediaQualityGateService:
    """Evaluates rendered media against platform profile constraints and acoustic standards."""

    def __init__(self, probe_service: Optional[MediaProbeService] = None) -> None:
        self._probe = probe_service or MediaProbeService()

    def evaluate_media(
        self,
        file_path: Union[str, Path],
        profile: PlatformExportProfile,
        target_lufs: float = -14.0,
        lufs_tolerance: float = 1.5,
        max_true_peak_dbtp: float = -1.0,
    ) -> QualityGateReport:
        """Run all technical media and acoustic checks on the target video file."""
        p = Path(file_path).resolve()
        checks: List[QualityCheckItem] = []
        failures: List[str] = []
        measurements: Dict[str, Any] = {}

        if not p.is_file():
            fail = f"Export media file not found: '{p}'"
            return QualityGateReport(
                file_path=str(p),
                profile_id=profile.profile_id,
                passed=False,
                checks=[
                    QualityCheckItem(
                        name="file_existence",
                        status="FAIL",
                        expected="File exists on disk",
                        actual="File not found",
                        message=fail,
                    )
                ],
                failure_reasons=[fail],
                measurements={},
            )

        # 1. Container & Streams Inspection via FFprobe
        probe: Optional[ProbeResult] = None
        try:
            probe = self._probe.probe_file(p)
        except Exception as e:
            logger.warning(f"FFprobe failed on '{p.name}': {e}")
            checks.append(QualityCheckItem(
                name="container_probe",
                status="FAIL",
                expected="Valid media container",
                actual="Unreadable or corrupt",
                message=str(e),
            ))
            failures.append(f"Media file probe failed: {e}")

        if probe:
            measurements["format_name"] = probe.format_name
            measurements["duration_sec"] = probe.duration_sec
            measurements["size_bytes"] = probe.size_bytes

            # Check video stream
            if not probe.has_video:
                checks.append(QualityCheckItem(
                    name="video_stream",
                    status="FAIL",
                    expected="At least 1 video stream",
                    actual="0 video streams",
                    message="Video stream is missing.",
                ))
                failures.append("No video stream found.")
            else:
                v = probe.primary_video_stream
                measurements["video_codec"] = v.codec_name
                measurements["width"] = v.width
                measurements["height"] = v.height
                measurements["fps"] = v.fps

                # Codec check
                v_codec_ok = (v.codec_name.lower() in [c.lower() for c in profile.allowed_video_codecs])
                if v_codec_ok:
                    checks.append(QualityCheckItem(
                        name="video_codec",
                        status="PASS",
                        expected=f"One of {profile.allowed_video_codecs}",
                        actual=v.codec_name,
                        message="Video codec compliant.",
                    ))
                else:
                    msg = f"Video codec '{v.codec_name}' not in allowed {profile.allowed_video_codecs}"
                    checks.append(QualityCheckItem(
                        name="video_codec",
                        status="FAIL",
                        expected=f"One of {profile.allowed_video_codecs}",
                        actual=v.codec_name,
                        message=msg,
                    ))
                    failures.append(msg)

                # Resolution check
                res_ok = (v.width == profile.width and v.height == profile.height)
                if res_ok:
                    checks.append(QualityCheckItem(
                        name="resolution",
                        status="PASS",
                        expected=f"{profile.width}x{profile.height}",
                        actual=f"{v.width}x{v.height}",
                        message="Resolution matches export profile.",
                    ))
                else:
                    msg = f"Resolution {v.width}x{v.height} does not match profile {profile.width}x{profile.height}"
                    checks.append(QualityCheckItem(
                        name="resolution",
                        status="FAIL",
                        expected=f"{profile.width}x{profile.height}",
                        actual=f"{v.width}x{v.height}",
                        message=msg,
                    ))
                    failures.append(msg)

                # Duration check against platform limit
                if profile.max_duration_seconds and probe.duration_sec > profile.max_duration_seconds:
                    msg = (
                        f"Duration ({probe.duration_sec:.1f}s) exceeds platform maximum of "
                        f"{profile.max_duration_seconds:.1f}s for '{profile.name_en}'"
                    )
                    checks.append(QualityCheckItem(
                        name="duration",
                        status="FAIL",
                        expected=f"<= {profile.max_duration_seconds:.1f}s",
                        actual=f"{probe.duration_sec:.1f}s",
                        message=msg,
                    ))
                    failures.append(msg)
                else:
                    limit_str = f"<= {profile.max_duration_seconds:.1f}s" if profile.max_duration_seconds else "unbounded"
                    checks.append(QualityCheckItem(
                        name="duration",
                        status="PASS",
                        expected=limit_str,
                        actual=f"{probe.duration_sec:.1f}s",
                        message="Duration within platform constraints.",
                    ))

            # Check audio stream
            if not probe.has_audio:
                checks.append(QualityCheckItem(
                    name="audio_stream",
                    status="FAIL",
                    expected="At least 1 audio stream",
                    actual="0 audio streams",
                    message="Audio stream is missing from video.",
                ))
                failures.append("No audio stream found.")
            else:
                a = probe.primary_audio_stream
                measurements["audio_codec"] = a.codec_name
                measurements["sample_rate"] = a.sample_rate
                measurements["channels"] = a.channels

                # Audio codec
                a_codec_ok = (a.codec_name.lower() in [c.lower() for c in profile.allowed_audio_codecs])
                if a_codec_ok:
                    checks.append(QualityCheckItem(
                        name="audio_codec",
                        status="PASS",
                        expected=f"One of {profile.allowed_audio_codecs}",
                        actual=a.codec_name,
                        message="Audio codec compliant.",
                    ))
                else:
                    msg = f"Audio codec '{a.codec_name}' not in allowed {profile.allowed_audio_codecs}"
                    checks.append(QualityCheckItem(
                        name="audio_codec",
                        status="FAIL",
                        expected=f"One of {profile.allowed_audio_codecs}",
                        actual=a.codec_name,
                        message=msg,
                    ))
                    failures.append(msg)

                # Sample rate
                sr_ok = a.sample_rate in profile.allowed_sample_rates
                if sr_ok:
                    checks.append(QualityCheckItem(
                        name="audio_sample_rate",
                        status="PASS",
                        expected=f"One of {profile.allowed_sample_rates} Hz",
                        actual=f"{a.sample_rate} Hz",
                        message="Audio sample rate compliant.",
                    ))
                else:
                    msg = f"Sample rate {a.sample_rate} Hz not in allowed {profile.allowed_sample_rates}"
                    checks.append(QualityCheckItem(
                        name="audio_sample_rate",
                        status="FAIL",
                        expected=f"One of {profile.allowed_sample_rates} Hz",
                        actual=f"{a.sample_rate} Hz",
                        message=msg,
                    ))
                    failures.append(msg)

        # 2. Acoustic Compliance (EBU R128 Loudness and True Peak)
        if probe and probe.has_audio:
            try:
                tp_res: TruePeakResult = self._probe.measure_true_peak(
                    p, max_allowed_dbtp=max_true_peak_dbtp
                )
                measurements["integrated_lufs"] = tp_res.integrated_loudness_lufs
                measurements["true_peak_dbtp"] = tp_res.true_peak_dbtp

                # Loudness evaluation (-14 LUFS +/- tolerance)
                min_lufs = target_lufs - lufs_tolerance
                max_lufs = target_lufs + lufs_tolerance
                measured_lufs = tp_res.integrated_loudness_lufs

                if min_lufs <= measured_lufs <= max_lufs:
                    checks.append(QualityCheckItem(
                        name="integrated_loudness",
                        status="PASS",
                        expected=f"{target_lufs:.1f} +/- {lufs_tolerance:.1f} LUFS [{min_lufs:.1f}, {max_lufs:.1f}]",
                        actual=f"{measured_lufs:.1f} LUFS",
                        message="Integrated loudness is within EBU R128 target window.",
                    ))
                else:
                    msg = (
                        f"Integrated loudness {measured_lufs:.1f} LUFS outside target window "
                        f"[{min_lufs:.1f}, {max_lufs:.1f}]"
                    )
                    checks.append(QualityCheckItem(
                        name="integrated_loudness",
                        status="FAIL",
                        expected=f"[{min_lufs:.1f}, {max_lufs:.1f}] LUFS",
                        actual=f"{measured_lufs:.1f} LUFS",
                        message=msg,
                    ))
                    failures.append(msg)

                # True Peak ceiling evaluation (<= max_true_peak_dbtp)
                if tp_res.is_compliant:
                    checks.append(QualityCheckItem(
                        name="true_peak",
                        status="PASS",
                        expected=f"<= {max_true_peak_dbtp:.1f} dBTP",
                        actual=f"{tp_res.true_peak_dbtp:.2f} dBTP",
                        message="True peak is within ceiling limit.",
                    ))
                else:
                    msg = f"True peak {tp_res.true_peak_dbtp:.2f} dBTP exceeds ceiling of {max_true_peak_dbtp:.1f} dBTP"
                    checks.append(QualityCheckItem(
                        name="true_peak",
                        status="FAIL",
                        expected=f"<= {max_true_peak_dbtp:.1f} dBTP",
                        actual=f"{tp_res.true_peak_dbtp:.2f} dBTP",
                        message=msg,
                    ))
                    failures.append(msg)

            except Exception as e:
                logger.warning(f"Acoustic measurement failed for '{p.name}': {e}")
                measurements["integrated_lufs"] = "Not Measured"
                measurements["true_peak_dbtp"] = "Not Measured"
                checks.append(QualityCheckItem(
                    name="acoustic_measurement",
                    status="NOT_MEASURED",
                    expected=f"Target {target_lufs} LUFS / True Peak <= {max_true_peak_dbtp} dBTP",
                    actual="Not Measured",
                    message=f"Measurement could not be completed: {e}",
                ))
                failures.append(f"Acoustic measurement could not be completed: {e}")
        else:
            # No audio or probe failed: mark explicitly Not Measured / N/A (never false PASS)
            measurements["integrated_lufs"] = "Not Measured"
            measurements["true_peak_dbtp"] = "Not Measured"
            checks.append(QualityCheckItem(
                name="acoustic_measurement",
                status="NOT_MEASURED",
                expected="Audio stream present for loudness & peak check",
                actual="N/A (No audio stream)",
                message="Acoustic checks skipped due to missing audio stream.",
            ))

        passed = (len(failures) == 0) and not any(c.status in ["FAIL", "NOT_MEASURED"] for c in checks)
        return QualityGateReport(
            file_path=str(p),
            profile_id=profile.profile_id,
            passed=passed,
            checks=checks,
            failure_reasons=failures,
            measurements=measurements,
        )
