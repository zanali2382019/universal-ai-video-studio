"""Safe FFmpeg and FFprobe subprocess execution wrapper with timeout and lifecycle management."""
import os
import re
import sys
import time
import shutil
import logging
import threading
import subprocess
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional, Dict, Any, Union

from src.core.config import get_settings
from src.core.logger import get_logger
from src.core.exceptions import (
    FFmpegNotFoundException,
    FFmpegExecutionException,
    FFprobeException,
    RenderTimeoutException,
    RenderCancelledException,
)

logger = get_logger("studio.rendering.ffmpeg_wrapper")


class _WindowsJobObject:
    """Manages a Windows Job Object to enforce kernel-level process-tree termination.

    Sets JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE so that closing the job handle or terminating
    the job object instantaneously and atomically kills the process and all of its descendants.
    """

    def __init__(self) -> None:
        self.handle: Optional[int] = None
        if sys.platform == "win32":
            self._init_job()

    def _init_job(self) -> None:
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32
            kernel32.CreateJobObjectW.restype = wintypes.HANDLE
            kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            kernel32.SetInformationJobObject.restype = wintypes.BOOL
            kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
            kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            kernel32.TerminateJobObject.restype = wintypes.BOOL
            kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

            class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                    ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("ReadOperationCount", ctypes.c_ulonglong),
                    ("WriteOperationCount", ctypes.c_ulonglong),
                    ("OtherOperationCount", ctypes.c_ulonglong),
                    ("ReadTransferCount", ctypes.c_ulonglong),
                    ("WriteTransferCount", ctypes.c_ulonglong),
                    ("OtherTransferCount", ctypes.c_ulonglong),
                ]

            class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryLimit", ctypes.c_size_t),
                    ("PeakJobMemoryLimit", ctypes.c_size_t),
                ]

            job = kernel32.CreateJobObjectW(None, None)
            if not job:
                return

            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            # 0x2000 = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            info.BasicLimitInformation.LimitFlags = 0x2000
            JobObjectExtendedLimitInformation = 9
            success = kernel32.SetInformationJobObject(
                job,
                JobObjectExtendedLimitInformation,
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if success:
                self.handle = job
            else:
                kernel32.CloseHandle(job)
        except Exception as e:
            logger.warning(f"Failed to initialize Windows Job Object: {e}")

    def assign_process(self, proc_handle: int) -> bool:
        """Assign target process handle to Job Object."""
        if self.handle and sys.platform == "win32":
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                return bool(kernel32.AssignProcessToJobObject(self.handle, proc_handle))
            except Exception as e:
                logger.warning(f"Failed to assign process handle {proc_handle} to Job Object: {e}")
        return False

    def terminate(self) -> None:
        """Atomically kill all processes in the job object."""
        if self.handle and sys.platform == "win32":
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                kernel32.TerminateJobObject(self.handle, 1)
            except Exception as e:
                logger.warning(f"Failed to terminate Job Object: {e}")

    @staticmethod
    def resume_process(proc: subprocess.Popen) -> bool:
        """Resume a suspended process using NtResumeProcess with ToolHelp32 fallback."""
        if sys.platform != "win32":
            return True
        try:
            import ctypes
            from ctypes import wintypes

            # Primary: NtResumeProcess operates on process handle and resumes all threads atomically
            ntdll = getattr(ctypes.windll, "ntdll", None)
            if ntdll and hasattr(ntdll, "NtResumeProcess"):
                ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
                ntdll.NtResumeProcess.restype = ctypes.c_long
                proc_handle = getattr(proc, "_handle", None)
                if proc_handle:
                    status = ntdll.NtResumeProcess(int(proc_handle))
                    if status == 0:
                        return True

            # Fallback: Enumerate threads via ToolHelp32 and resume each thread
            kernel32 = ctypes.windll.kernel32
            TH32CS_SNAPTHREAD = 0x00000004
            THREAD_SUSPEND_RESUME = 0x0002

            class THREADENTRY32(ctypes.Structure):
                _fields_ = [
                    ("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ThreadID", wintypes.DWORD),
                    ("th32OwnerProcessID", wintypes.DWORD),
                    ("tpBasePri", ctypes.c_long),
                    ("tpDeltaPri", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD),
                ]

            kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
            kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
            kernel32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREADENTRY32)]
            kernel32.Thread32First.restype = wintypes.BOOL
            kernel32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREADENTRY32)]
            kernel32.Thread32Next.restype = wintypes.BOOL
            kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenThread.restype = wintypes.HANDLE
            kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
            kernel32.ResumeThread.restype = wintypes.DWORD
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL

            h_snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
            if h_snap and h_snap != -1:
                try:
                    entry = THREADENTRY32()
                    entry.dwSize = ctypes.sizeof(THREADENTRY32)
                    if kernel32.Thread32First(h_snap, ctypes.byref(entry)):
                        while True:
                            if entry.th32OwnerProcessID == proc.pid:
                                h_thread = kernel32.OpenThread(THREAD_SUSPEND_RESUME, False, entry.th32ThreadID)
                                if h_thread:
                                    kernel32.ResumeThread(h_thread)
                                    kernel32.CloseHandle(h_thread)
                            if not kernel32.Thread32Next(h_snap, ctypes.byref(entry)):
                                break
                    return True
                finally:
                    kernel32.CloseHandle(h_snap)
        except Exception as e:
            logger.warning(f"Failed to resume process {proc.pid}: {e}")
        return False

    def close(self) -> None:
        """Close job object handle safely."""
        if self.handle and sys.platform == "win32":
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                kernel32.CloseHandle(self.handle)
            except Exception:
                pass
            finally:
                self.handle = None


@dataclass
class FFmpegResult:
    """Outcome of an FFmpeg or FFprobe execution."""
    cmd: List[str]
    returncode: int
    stdout: str
    stderr: str
    duration_sec: float
    pid: Optional[int] = None

    @property
    def is_success(self) -> bool:
        return self.returncode == 0


class FFmpegWrapper:
    """Safe wrapper for FFmpeg and FFprobe execution with timeout control, process-tree killing, and error diagnostics."""

    def __init__(
        self,
        ffmpeg_path: Optional[Union[str, Path]] = None,
        ffprobe_path: Optional[Union[str, Path]] = None,
        default_timeout_sec: float = 300.0,
    ) -> None:
        self.default_timeout_sec = default_timeout_sec
        settings = get_settings()

        # 1. Resolve ffmpeg path
        resolved_ffmpeg = ffmpeg_path or settings.ffmpeg_binary or shutil.which("ffmpeg")
        if not resolved_ffmpeg or not self._is_executable(resolved_ffmpeg):
            raise FFmpegNotFoundException("ffmpeg", searched_path=str(resolved_ffmpeg))
        self.ffmpeg_path = str(resolved_ffmpeg)

        # 2. Resolve ffprobe path
        resolved_ffprobe = ffprobe_path or settings.ffprobe_binary or shutil.which("ffprobe")
        if not resolved_ffprobe or not self._is_executable(resolved_ffprobe):
            raise FFmpegNotFoundException("ffprobe", searched_path=str(resolved_ffprobe))
        self.ffprobe_path = str(resolved_ffprobe)

        logger.info(f"Initialized FFmpegWrapper: ffmpeg='{self.ffmpeg_path}', ffprobe='{self.ffprobe_path}'")

    @staticmethod
    def _is_executable(path_str: Union[str, Path]) -> bool:
        """Check if the given path or binary name is executable."""
        p = Path(path_str)
        if p.is_file():
            return os.access(p, os.X_OK)
        # If it's a bare binary name, check shutil.which
        which_path = shutil.which(str(path_str))
        return which_path is not None

    def get_version_info(self) -> Dict[str, str]:
        """Query and return the version strings for both binaries."""
        res_ffmpeg = self.run_ffmpeg(["-version"], timeout_sec=10.0, check=True)
        res_ffprobe = self.run_ffprobe(["-version"], timeout_sec=10.0, check=True)

        ffmpeg_line = res_ffmpeg.stdout.splitlines()[0] if res_ffmpeg.stdout else "unknown"
        ffprobe_line = res_ffprobe.stdout.splitlines()[0] if res_ffprobe.stdout else "unknown"

        return {
            "ffmpeg": ffmpeg_line,
            "ffprobe": ffprobe_line,
        }

    def run_ffmpeg(
        self,
        args: List[str],
        timeout_sec: Optional[float] = None,
        check: bool = True,
        cancellation_token: Optional[threading.Event] = None,
    ) -> FFmpegResult:
        """Run FFmpeg with arguments, strict timeout enforcement, and subprocess tree cleanup.

        Args:
            args: Command line arguments (excluding 'ffmpeg').
            timeout_sec: Maximum execution duration before killing process.
            check: If True, raises FFmpegExecutionException on non-zero exit code.
            cancellation_token: Optional Event to cancel in-flight execution.

        Returns:
            FFmpegResult: Execution outcome with stdout, stderr, and timings.
        """
        effective_timeout = timeout_sec or self.default_timeout_sec
        cmd = [self.ffmpeg_path] + [str(a) for a in args]
        return self._execute_process(cmd, timeout_sec=effective_timeout, check=check, cancellation_token=cancellation_token, is_ffprobe=False)

    # Convenient alias
    run = run_ffmpeg

    def run_ffprobe(
        self,
        args: List[str],
        timeout_sec: float = 30.0,
        check: bool = True,
    ) -> FFmpegResult:
        """Run FFprobe with arguments and return output."""
        cmd = [self.ffprobe_path] + [str(a) for a in args]
        return self._execute_process(cmd, timeout_sec=timeout_sec, check=check, cancellation_token=None, is_ffprobe=True)

    def _execute_process(
        self,
        cmd: List[str],
        timeout_sec: float,
        check: bool,
        cancellation_token: Optional[threading.Event],
        is_ffprobe: bool,
    ) -> FFmpegResult:
        """Execute subprocess with process group isolation, timeout guard, and tree kill."""
        if cancellation_token and cancellation_token.is_set():
            raise RenderCancelledException("Render task cancelled prior to process launch.")

        start_time = time.time()
        logger.debug(f"Executing: {' '.join(cmd)}")

        creation_flags = 0
        job_object: Optional[_WindowsJobObject] = None
        if sys.platform == "win32":
            job_object = _WindowsJobObject()
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP
            if job_object.handle:
                # 0x00000004 = CREATE_SUSPENDED: Prevents process from executing any code or
                # spawning children before safe Job Object assignment, eliminating the assignment race.
                creation_flags |= 0x00000004

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creation_flags,
        )

        if job_object and job_object.handle and hasattr(proc, "_handle") and proc._handle:
            assigned = job_object.assign_process(int(proc._handle))
            if not assigned:
                logger.error(f"Failed to assign suspended process {proc.pid} to Job Object.")
            # Controlled resume: process starts executing strictly inside the Job Object
            job_object.resume_process(proc)

        # Monitor cancellation in a separate background watchdog thread if provided
        watchdog_triggered = False

        def cancellation_watchdog():
            nonlocal watchdog_triggered
            while proc.poll() is None:
                if cancellation_token and cancellation_token.is_set():
                    watchdog_triggered = True
                    self._terminate_process_tree(proc, job_object)
                    break
                time.sleep(0.05)

        watcher_thread = None
        if cancellation_token:
            watcher_thread = threading.Thread(target=cancellation_watchdog, daemon=True)
            watcher_thread.start()

        stdout = ""
        stderr = ""
        try:
            try:
                stdout, stderr = proc.communicate(timeout=timeout_sec)
            except subprocess.TimeoutExpired:
                logger.error(f"Process timed out after {timeout_sec}s: {' '.join(cmd)}")
                self._terminate_process_tree(proc, job_object)
                try:
                    stdout, stderr = proc.communicate(timeout=3.0)
                except Exception:
                    pass
                raise RenderTimeoutException(timeout_sec=timeout_sec, cmd=" ".join(cmd))
            finally:
                if watcher_thread and watcher_thread.is_alive():
                    watcher_thread.join(timeout=0.2)
        finally:
            if job_object:
                job_object.close()

        elapsed = time.time() - start_time

        if watchdog_triggered:
            raise RenderCancelledException(f"Render process '{proc.pid}' was cancelled while running.")

        result = FFmpegResult(
            cmd=cmd,
            returncode=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_sec=elapsed,
            pid=proc.pid,
        )

        if check and proc.returncode != 0:
            err_msg = stderr.strip() if stderr else stdout.strip()
            # Truncate diagnostic error message to avoid flooding logs
            short_err = err_msg[-1000:] if len(err_msg) > 1000 else err_msg
            if is_ffprobe:
                raise FFprobeException(
                    f"FFprobe failed with exit code {proc.returncode}: {short_err}",
                    file_path=cmd[-1] if cmd else None,
                    stderr=stderr,
                )
            else:
                raise FFmpegExecutionException(
                    f"FFmpeg failed with exit code {proc.returncode}: {short_err}",
                    exit_code=proc.returncode,
                    cmd=" ".join(cmd),
                    stderr=stderr,
                )

        return result

    @staticmethod
    def _terminate_process_tree(proc: subprocess.Popen, job_object: Optional[_WindowsJobObject] = None) -> None:
        """Safely kill the subprocess and all descendant children via Job Object and process kill."""
        if job_object:
            job_object.terminate()
        try:
            proc.kill()
        except Exception as e:
            logger.warning(f"Error terminating process {proc.pid}: {e}")

    # -------------------------------------------------------------------------
    # Synthetic Media Generators for Unit Tests & Zero-Fixture Verification
    # -------------------------------------------------------------------------

    def create_synthetic_image(
        self,
        output_path: Path,
        width: int = 1080,
        height: int = 1920,
        color: str = "blue",
    ) -> Path:
        """Create a synthetic test image using lavfi color source."""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        args = [
            "-y",
            "-f", "lavfi",
            "-i", f"color=c={color}:s={width}x{height}:d=1",
            "-frames:v", "1",
            str(output_path),
        ]
        self.run_ffmpeg(args, timeout_sec=15.0, check=True)
        return output_path

    def create_synthetic_audio(
        self,
        output_path: Path,
        duration_sec: float = 2.0,
        freq: int = 1000,
        volume: float = 1.0,
        sample_rate: int = 44100,
    ) -> Path:
        """Create a synthetic test audio file (AAC) using lavfi sine source."""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        args = [
            "-y",
            "-f", "lavfi",
            "-i", f"sine=frequency={freq}:duration={duration_sec}:sample_rate={sample_rate}",
            "-af", f"volume={volume}",
            "-c:a", "aac",
            str(output_path),
        ]
        self.run_ffmpeg(args, timeout_sec=15.0, check=True)
        return output_path

    def create_synthetic_video(
        self,
        output_path: Path,
        duration_sec: float = 2.0,
        width: int = 1080,
        height: int = 1920,
        fps: int = 30,
        color: str = "green",
    ) -> Path:
        """Create a synthetic test video (H.264/MP4) using lavfi color source."""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        args = [
            "-y",
            "-f", "lavfi",
            "-i", f"color=c={color}:s={width}x{height}:r={fps}:d={duration_sec}",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            str(output_path),
        ]
        self.run_ffmpeg(args, timeout_sec=25.0, check=True)
        return output_path

