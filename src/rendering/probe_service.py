"""Media probe service for technical container, codec, stream, and True Peak analysis."""
import re
import json
from pathlib import Path
from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field

from src.core.logger import get_logger
from src.core.exceptions import (
    FFprobeException,
    MediaValidationException,
    AudioTruePeakExceededException,
)
from src.rendering.ffmpeg_wrapper import FFmpegWrapper

logger = get_logger("studio.rendering.probe_service")


class VideoStreamInfo(BaseModel):
    """Extracted metadata for a video stream."""
    index: int
    codec_name: str
    width: int
    height: int
    fps: float
    pix_fmt: Optional[str] = None
    duration_sec: Optional[float] = None
    bit_rate: Optional[int] = None


class AudioStreamInfo(BaseModel):
    """Extracted metadata for an audio stream."""
    index: int
    codec_name: str
    sample_rate: int
    channels: int
    channel_layout: Optional[str] = None
    duration_sec: Optional[float] = None
    bit_rate: Optional[int] = None


class ProbeResult(BaseModel):
    """Structured inspection result from ffprobe."""
    file_path: str
    format_name: str
    duration_sec: float
    size_bytes: int
    bit_rate: Optional[int] = None
    video_streams: List[VideoStreamInfo] = Field(default_factory=list)
    audio_streams: List[AudioStreamInfo] = Field(default_factory=list)

    @property
    def has_video(self) -> bool:
        return len(self.video_streams) > 0

    @property
    def has_audio(self) -> bool:
        return len(self.audio_streams) > 0

    @property
    def primary_video_stream(self) -> Optional[VideoStreamInfo]:
        return self.video_streams[0] if self.video_streams else None

    @property
    def primary_audio_stream(self) -> Optional[AudioStreamInfo]:
        return self.audio_streams[0] if self.audio_streams else None


class TruePeakResult(BaseModel):
    """Outcome of EBU R128 True Peak and Loudness evaluation."""
    file_path: str
    integrated_loudness_lufs: float
    true_peak_dbtp: float
    is_compliant: bool
    max_allowed_dbtp: float
    raw_summary: str


class MediaProbeService:
    """Provides technical inspection of media files and True Peak acoustic compliance measurement."""

    def __init__(self, ffmpeg_wrapper: Optional[FFmpegWrapper] = None) -> None:
        self.wrapper = ffmpeg_wrapper or FFmpegWrapper()

    def probe_file(self, file_path: Union[str, Path]) -> ProbeResult:
        """Probe media container and streams using ffprobe.

        Args:
            file_path: Path to media file on disk.

        Returns:
            ProbeResult: Parsed streams and format information.

        Raises:
            FFprobeException: If the file does not exist, is corrupt, or cannot be parsed.
        """
        p = Path(file_path).resolve()
        if not p.is_file():
            raise FFprobeException(f"Media file not found for probing: '{p}'", file_path=str(p))

        args = [
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(p),
        ]

        result = self.wrapper.run_ffprobe(args, timeout_sec=20.0, check=True)

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise FFprobeException(
                f"Failed to decode ffprobe JSON output for '{p}': {e}",
                file_path=str(p),
                stderr=result.stderr,
            )

        fmt = data.get("format", {})
        streams = data.get("streams", [])

        # Parse duration
        duration_sec = 0.0
        try:
            duration_sec = float(fmt.get("duration", 0.0))
        except (ValueError, TypeError):
            duration_sec = 0.0

        # Parse size
        size_bytes = 0
        try:
            size_bytes = int(fmt.get("size", p.stat().st_size))
        except (ValueError, TypeError, OSError):
            size_bytes = 0

        # Parse bit rate
        bit_rate: Optional[int] = None
        try:
            if fmt.get("bit_rate"):
                bit_rate = int(fmt["bit_rate"])
        except (ValueError, TypeError):
            pass

        video_streams: List[VideoStreamInfo] = []
        audio_streams: List[AudioStreamInfo] = []

        for s in streams:
            codec_type = s.get("codec_type")
            idx = int(s.get("index", 0))
            c_name = str(s.get("codec_name", "unknown"))

            if codec_type == "video":
                fps = self._parse_frame_rate(s.get("r_frame_rate") or s.get("avg_frame_rate") or "0/1")
                v_dur = None
                try:
                    if s.get("duration"):
                        v_dur = float(s["duration"])
                except (ValueError, TypeError):
                    pass

                v_br = None
                try:
                    if s.get("bit_rate"):
                        v_br = int(s["bit_rate"])
                except (ValueError, TypeError):
                    pass

                video_streams.append(
                    VideoStreamInfo(
                        index=idx,
                        codec_name=c_name,
                        width=int(s.get("width", 0)),
                        height=int(s.get("height", 0)),
                        fps=fps,
                        pix_fmt=s.get("pix_fmt"),
                        duration_sec=v_dur,
                        bit_rate=v_br,
                    )
                )

            elif codec_type == "audio":
                a_dur = None
                try:
                    if s.get("duration"):
                        a_dur = float(s["duration"])
                except (ValueError, TypeError):
                    pass

                a_br = None
                try:
                    if s.get("bit_rate"):
                        a_br = int(s["bit_rate"])
                except (ValueError, TypeError):
                    pass

                audio_streams.append(
                    AudioStreamInfo(
                        index=idx,
                        codec_name=c_name,
                        sample_rate=int(s.get("sample_rate", 44100)),
                        channels=int(s.get("channels", 1)),
                        channel_layout=s.get("channel_layout"),
                        duration_sec=a_dur,
                        bit_rate=a_br,
                    )
                )

        return ProbeResult(
            file_path=str(p),
            format_name=str(fmt.get("format_name", "unknown")),
            duration_sec=duration_sec,
            size_bytes=size_bytes,
            bit_rate=bit_rate,
            video_streams=video_streams,
            audio_streams=audio_streams,
        )

    def measure_true_peak(
        self,
        file_path: Union[str, Path],
        max_allowed_dbtp: float = -1.0,
    ) -> TruePeakResult:
        """Measure audio True Peak and Integrated Loudness using FFmpeg's ebur128 audio filter.

        Runs: ffmpeg -nostats -i <file> -af "ebur128=peak=true" -f null -
        Parses stderr for 'True peak: Peak: X dBTP/dBFS' and 'Integrated loudness: I: Y LUFS'.

        Args:
            file_path: Path to media file with audio.
            max_allowed_dbtp: Maximum allowable peak in dBTP (-1.0 dBTP per ADR-003).

        Returns:
            TruePeakResult: Acoustic measurement and compliance determination.

        Raises:
            FFprobeException: If the file cannot be inspected.
            MediaValidationException: If the file has no audio stream to measure.
        """
        p = Path(file_path).resolve()
        if not p.is_file():
            raise FFprobeException(f"File not found for true peak measurement: '{p}'", file_path=str(p))

        args = [
            "-nostats",
            "-i", str(p),
            "-af", "ebur128=peak=true",
            "-f", "null",
            "-",
        ]

        result = self.wrapper.run_ffmpeg(args, timeout_sec=60.0, check=True)
        stderr = result.stderr

        # Parse integrated loudness: "I:\s+([-\d.]+)\s+LUFS"
        i_match = re.search(r"Integrated loudness:\s+I:\s+([-\d.]+)\s+LUFS", stderr)
        integrated_lufs = float(i_match.group(1)) if i_match else -70.0

        # Parse true peak: "True peak:\s+Peak:\s+([-\d.]+|-\s*inf)\s+(?:dBFS|dBTP)"
        tp_match = re.search(r"True peak:\s+Peak:\s+([-\d.]+|-\s*inf)\s+(?:dBFS|dBTP)", stderr, re.IGNORECASE)
        if not tp_match:
            # Fallback pattern if summary formatting differs slightly
            tp_match = re.search(r"Peak:\s+([-\d.]+|-\s*inf)\s+(?:dBFS|dBTP)", stderr, re.IGNORECASE)

        if not tp_match:
            raise MediaValidationException(
                f"Failed to extract True Peak reading from FFmpeg output for '{p}'. Audio stream may be missing or empty.",
                file_path=str(p),
            )

        tp_str = tp_match.group(1).replace(" ", "").lower()
        measured_tp = float("-inf") if "inf" in tp_str else float(tp_str)
        is_compliant = (measured_tp <= max_allowed_dbtp)

        logger.info(
            f"True Peak analysis for '{p.name}': Measured = {measured_tp:.2f} dBTP, "
            f"Limit = {max_allowed_dbtp:.2f} dBTP -> Compliant: {is_compliant} "
            f"(Integrated Loudness = {integrated_lufs:.1f} LUFS)."
        )

        return TruePeakResult(
            file_path=str(p),
            integrated_loudness_lufs=integrated_lufs,
            true_peak_dbtp=measured_tp,
            is_compliant=is_compliant,
            max_allowed_dbtp=max_allowed_dbtp,
            raw_summary=stderr[-1000:],
        )

    def validate_media(
        self,
        file_path: Union[str, Path],
        expected_width: Optional[int] = None,
        expected_height: Optional[int] = None,
        expected_fps: Optional[float] = None,
        expected_video_codec: Optional[str] = None,
        expected_audio_codec: Optional[str] = None,
        require_audio: bool = False,
        max_true_peak_dbtp: Optional[float] = -1.0,
    ) -> Dict[str, Any]:
        """Validate media file against technical criteria, raising clear exceptions on violations."""
        p = Path(file_path).resolve()
        probe = self.probe_file(p)
        errors: Dict[str, Any] = {}

        # 1. Video stream checks
        if expected_width or expected_height or expected_fps or expected_video_codec:
            if not probe.has_video:
                errors["video"] = "No video stream found in media file."
            else:
                v = probe.primary_video_stream
                if expected_width and v.width != expected_width:
                    errors["width"] = f"Expected width {expected_width}, found {v.width}"
                if expected_height and v.height != expected_height:
                    errors["height"] = f"Expected height {expected_height}, found {v.height}"
                if expected_fps and abs(v.fps - expected_fps) > 0.5:
                    errors["fps"] = f"Expected fps {expected_fps:.2f}, found {v.fps:.2f}"
                if expected_video_codec and v.codec_name.lower() != expected_video_codec.lower():
                    errors["video_codec"] = f"Expected codec '{expected_video_codec}', found '{v.codec_name}'"

        # 2. Audio stream checks
        if require_audio and not probe.has_audio:
            errors["audio"] = "Audio stream is required but none was found."

        if probe.has_audio and expected_audio_codec:
            a = probe.primary_audio_stream
            if a.codec_name.lower() != expected_audio_codec.lower():
                errors["audio_codec"] = f"Expected audio codec '{expected_audio_codec}', found '{a.codec_name}'"

        if errors:
            raise MediaValidationException(
                f"Media validation failed for '{p.name}': {errors}",
                file_path=str(p),
                validation_errors=errors,
            )

        # 3. Acoustic True Peak check
        true_peak_res: Optional[TruePeakResult] = None
        if probe.has_audio and max_true_peak_dbtp is not None:
            true_peak_res = self.measure_true_peak(p, max_allowed_dbtp=max_true_peak_dbtp)
            if not true_peak_res.is_compliant:
                raise AudioTruePeakExceededException(
                    file_path=str(p),
                    measured_peak_dbtp=true_peak_res.true_peak_dbtp,
                    max_allowed_dbtp=max_true_peak_dbtp,
                )

        return {
            "status": "valid",
            "file_path": str(p),
            "probe": probe.model_dump(),
            "true_peak": true_peak_res.model_dump() if true_peak_res else None,
        }

    @staticmethod
    def _parse_frame_rate(rate_str: str) -> float:
        """Parse frame rate fractional string like '30/1' or '29.97' into float."""
        try:
            if "/" in rate_str:
                num, den = rate_str.split("/", 1)
                den_f = float(den)
                return float(num) / den_f if den_f != 0 else 0.0
            return float(rate_str)
        except Exception:
            return 0.0

