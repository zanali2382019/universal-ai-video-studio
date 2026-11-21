"""Comprehensive unit and integration tests for FFmpegWrapper and MediaProbeService (Task 3.6 — Milestone 1)."""
import os
import sys
import time
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from src.core.exceptions import (
    FFmpegNotFoundException,
    FFmpegExecutionException,
    FFprobeException,
    MediaValidationException,
    AudioTruePeakExceededException,
    RenderTimeoutException,
    RenderCancelledException,
)
from src.rendering.ffmpeg_wrapper import FFmpegWrapper, FFmpegResult
from src.rendering.probe_service import MediaProbeService, ProbeResult, TruePeakResult


class TestFFmpegWrapperAndProbeService(unittest.TestCase):
    """Test suite verifying safe execution, timeout control, probing, and True Peak compliance."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.test_dir = Path(self.temp_dir.name)
        self.wrapper = FFmpegWrapper(default_timeout_sec=30.0)
        self.probe_service = MediaProbeService(ffmpeg_wrapper=self.wrapper)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_01_binary_discovery_and_version_query(self):
        """Scenario 1: Discovers system ffmpeg and ffprobe and parses version info cleanly."""
        versions = self.wrapper.get_version_info()
        self.assertIn("ffmpeg", versions)
        self.assertIn("ffprobe", versions)
        self.assertTrue(versions["ffmpeg"].lower().startswith("ffmpeg version"))
        self.assertTrue(versions["ffprobe"].lower().startswith("ffprobe version"))

    def test_02_missing_binary_raises_exception(self):
        """Scenario 2: Supplying invalid binary path raises FFmpegNotFoundException."""
        bogus_path = self.test_dir / "non_existent_binary_xyz.exe"
        with self.assertRaises(FFmpegNotFoundException):
            FFmpegWrapper(ffmpeg_path=bogus_path)

        with self.assertRaises(FFmpegNotFoundException):
            FFmpegWrapper(ffprobe_path=bogus_path)

    def test_03_synthetic_image_generation_and_probing(self):
        """Scenario 3: Creates synthetic image and probes dimensions and stream info."""
        img_path = self.test_dir / "test_frame.jpg"
        self.wrapper.create_synthetic_image(img_path, width=1080, height=1920, color="blue")
        self.assertTrue(img_path.is_file())
        self.assertGreater(img_path.stat().st_size, 0)

        probe = self.probe_service.probe_file(img_path)
        self.assertTrue(probe.has_video)
        self.assertFalse(probe.has_audio)
        self.assertEqual(probe.primary_video_stream.width, 1080)
        self.assertEqual(probe.primary_video_stream.height, 1920)

    def test_04_synthetic_audio_generation_and_probing(self):
        """Scenario 4: Creates synthetic audio file (AAC) and probes codec, sample rate, and channels."""
        audio_path = self.test_dir / "test_sine.aac"
        self.wrapper.create_synthetic_audio(audio_path, duration_sec=1.5, freq=1000, sample_rate=44100)
        self.assertTrue(audio_path.is_file())
        self.assertGreater(audio_path.stat().st_size, 0)

        probe = self.probe_service.probe_file(audio_path)
        self.assertFalse(probe.has_video)
        self.assertTrue(probe.has_audio)
        a_stream = probe.primary_audio_stream
        self.assertEqual(a_stream.codec_name.lower(), "aac")
        self.assertEqual(a_stream.sample_rate, 44100)
        self.assertAlmostEqual(probe.duration_sec, 1.5, delta=0.2)

    def test_05_synthetic_video_generation_and_probing(self):
        """Scenario 5: Creates synthetic 9:16 video (H.264/30fps) and probes container and streams."""
        video_path = self.test_dir / "test_clip.mp4"
        self.wrapper.create_synthetic_video(
            video_path,
            duration_sec=2.0,
            width=1080,
            height=1920,
            fps=30,
            color="red",
        )
        self.assertTrue(video_path.is_file())
        self.assertGreater(video_path.stat().st_size, 0)

        probe = self.probe_service.probe_file(video_path)
        self.assertTrue(probe.has_video)
        v_stream = probe.primary_video_stream
        self.assertEqual(v_stream.codec_name.lower(), "h264")
        self.assertEqual(v_stream.width, 1080)
        self.assertEqual(v_stream.height, 1920)
        self.assertEqual(v_stream.fps, 30.0)
        self.assertAlmostEqual(probe.duration_sec, 2.0, delta=0.2)

    def test_06_true_peak_measurement_compliant(self):
        """Scenario 6: Measures acoustic True Peak using FFmpeg ebur128 and asserts ADR-003 compliance."""
        # Generate tone with volume=0.2 (safe below -1.0 dBTP)
        audio_path = self.test_dir / "safe_tone.aac"
        self.wrapper.create_synthetic_audio(audio_path, duration_sec=2.0, freq=440, volume=0.2)

        tp_result = self.probe_service.measure_true_peak(audio_path, max_allowed_dbtp=-1.0)
        self.assertIsInstance(tp_result, TruePeakResult)
        self.assertLessEqual(tp_result.true_peak_dbtp, -1.0)
        self.assertTrue(tp_result.is_compliant)
        self.assertLess(tp_result.integrated_loudness_lufs, 0.0)

    def test_07_true_peak_non_compliant_violation_detection(self):
        """Scenario 7: Accurately detects non-compliant True Peak exceeding the threshold."""
        # Generate boosted tone with volume=10.0 (overdriven to +1.7 dBTP, violating the -1.0 dBTP limit)
        audio_path = self.test_dir / "loud_tone.aac"
        self.wrapper.create_synthetic_audio(audio_path, duration_sec=1.5, freq=1000, volume=10.0)

        # Direct measurement indicates non-compliance with -1.0 dBTP limit
        tp_result = self.probe_service.measure_true_peak(audio_path, max_allowed_dbtp=-1.0)
        self.assertFalse(tp_result.is_compliant)
        self.assertGreater(tp_result.true_peak_dbtp, -1.0)

        # validate_media raises AudioTruePeakExceededException
        with self.assertRaises(AudioTruePeakExceededException):
            self.probe_service.validate_media(audio_path, max_true_peak_dbtp=-1.0)

    def test_08_media_validation_success_and_failure(self):
        """Scenario 8: validate_media enforces resolution, codec, and fps requirements."""
        vid_path = self.test_dir / "valid_clip.mp4"
        self.wrapper.create_synthetic_video(vid_path, duration_sec=1.0, width=1080, height=1920, fps=30)

        # Success case
        val_res = self.probe_service.validate_media(
            vid_path,
            expected_width=1080,
            expected_height=1920,
            expected_fps=30.0,
            expected_video_codec="h264",
        )
        self.assertEqual(val_res["status"], "valid")

        # Failure: width mismatch (expected 1920 instead of 1080)
        with self.assertRaises(MediaValidationException):
            self.probe_service.validate_media(vid_path, expected_width=1920)

        # Failure: codec mismatch (expected hevc instead of h264)
        with self.assertRaises(MediaValidationException):
            self.probe_service.validate_media(vid_path, expected_video_codec="hevc")

    def test_09_ffmpeg_execution_failure_raises_exception(self):
        """Scenario 9: Invalid FFmpeg parameters raise FFmpegExecutionException with exit code."""
        with self.assertRaises(FFmpegExecutionException) as ctx:
            self.wrapper.run_ffmpeg(["-non_existent_flag_xyz"], check=True)

        self.assertNotEqual(ctx.exception.details.get("exit_code"), 0)
        self.assertIn("exit code", str(ctx.exception))

    def test_10_ffprobe_failure_on_corrupt_file(self):
        """Scenario 10: Probing a non-media corrupted file raises FFprobeException."""
        junk_path = self.test_dir / "corrupt.mp4"
        junk_path.write_text("THIS IS NOT A VALID MP4 CONTAINER", encoding="utf-8")

        with self.assertRaises(FFprobeException):
            self.probe_service.probe_file(junk_path)

    @staticmethod
    def _is_process_alive(pid: int) -> bool:
        """Check if a process is alive using Win32 API or POSIX signals."""
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            exit_code = wintypes.DWORD()
            success = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            kernel32.CloseHandle(handle)
            return bool(success and exit_code.value == STILL_ACTIVE)
        else:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False

    def test_11_subprocess_timeout_control(self):
        """Scenario 11: Subprocess and child process tree are killed when timeout expires."""
        # 1. FFmpeg timeout check
        out_path = self.test_dir / "timeout_clip.mp4"
        args = [
            "-y",
            "-f", "lavfi",
            "-i", "testsrc=duration=10:size=1920x1080:rate=60",
            "-c:v", "libx264",
            "-preset", "veryslow",
            str(out_path),
        ]
        with self.assertRaises(RenderTimeoutException) as ctx:
            self.wrapper.run_ffmpeg(args, timeout_sec=0.3, check=True)

        self.assertEqual(ctx.exception.details.get("timeout_sec"), 0.3)

        # 2. Process tree termination test: parent spawns a child process
        child_pid_file = self.test_dir / "timeout_child.pid"
        parent_script = (
            "import subprocess, sys, time\n"
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            f"with open(r'{child_pid_file}', 'w') as f:\n"
            "    f.write(str(p.pid))\n"
            "time.sleep(60)\n"
        )
        with self.assertRaises(RenderTimeoutException):
            self.wrapper._execute_process(
                [sys.executable, "-c", parent_script],
                timeout_sec=0.4,
                check=True,
                cancellation_token=None,
                is_ffprobe=False,
            )

        time.sleep(0.3)
        self.assertTrue(child_pid_file.exists(), "Child PID file was not created by parent process.")
        child_pid = int(child_pid_file.read_text().strip())
        self.assertFalse(
            self._is_process_alive(child_pid),
            f"Child process {child_pid} was not terminated by Job Object on timeout!",
        )

    def test_12_subprocess_cancellation_pre_launch_and_in_flight(self):
        """Scenario 12: Cancellation token stops process and entire child process tree safely."""
        # 1. Cancelled before launch
        cancel_token = threading.Event()
        cancel_token.set()

        with self.assertRaises(RenderCancelledException):
            self.wrapper.run_ffmpeg(["-version"], cancellation_token=cancel_token)

        # 2. Cancelled in-flight FFmpeg
        in_flight_token = threading.Event()
        out_path = self.test_dir / "cancel_clip.mp4"
        args = [
            "-y",
            "-f", "lavfi",
            "-i", "testsrc=duration=10:size=1280x720:rate=30",
            "-c:v", "libx264",
            str(out_path),
        ]

        def trigger_cancel():
            time.sleep(0.15)
            in_flight_token.set()

        t = threading.Thread(target=trigger_cancel)
        t.start()

        with self.assertRaises(RenderCancelledException):
            self.wrapper.run_ffmpeg(args, timeout_sec=10.0, cancellation_token=in_flight_token)

        t.join()

        # 3. Process tree termination test on cancellation: parent spawns child process
        child_pid_file = self.test_dir / "cancel_child.pid"
        parent_script = (
            "import subprocess, sys, time\n"
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            f"with open(r'{child_pid_file}', 'w') as f:\n"
            "    f.write(str(p.pid))\n"
            "time.sleep(60)\n"
        )
        tree_cancel_token = threading.Event()

        def trigger_tree_cancel():
            time.sleep(0.2)
            tree_cancel_token.set()

        t2 = threading.Thread(target=trigger_tree_cancel)
        t2.start()

        with self.assertRaises(RenderCancelledException):
            self.wrapper._execute_process(
                [sys.executable, "-c", parent_script],
                timeout_sec=10.0,
                check=True,
                cancellation_token=tree_cancel_token,
                is_ffprobe=False,
            )

        t2.join()
        time.sleep(0.3)
        self.assertTrue(child_pid_file.exists(), "Child PID file was not created by parent process.")
        child_pid = int(child_pid_file.read_text().strip())
        self.assertFalse(
            self._is_process_alive(child_pid),
            f"Child process {child_pid} was not terminated by Job Object on cancellation!",
        )

    def test_13_suspended_process_race_prevention_and_early_spawn(self):
        """Scenario 13: Suspended process creation eliminates race window for processes that spawn children at line 1."""
        if sys.platform != "win32":
            return

        from src.rendering.ffmpeg_wrapper import _WindowsJobObject

        # 1. Direct Job Object unit verification: Process cannot execute instructions before assignment
        job = _WindowsJobObject()
        self.assertIsNotNone(job.handle, "Windows Job Object handle could not be initialized.")

        gc_pid_file = self.test_dir / "instant_spawn.pid"
        script = (
            "import subprocess, sys, time\n"
            "gc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            f"with open(r'{gc_pid_file}', 'w') as f:\n"
            "    f.write(str(gc.pid))\n"
            "time.sleep(60)\n"
        )

        CREATE_SUSPENDED = 0x00000004
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        proc = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP,
        )

        try:
            # Assert process is completely suspended: file has NOT been written
            time.sleep(0.1)
            self.assertFalse(gc_pid_file.exists(), "Suspended process executed instructions before resumption!")

            # Safely assign to Job Object
            assigned = job.assign_process(int(proc._handle))
            self.assertTrue(assigned, "Failed to assign suspended process to Job Object.")

            # Controlled resume: process now executes within the Job Object
            resumed = job.resume_process(proc)
            self.assertTrue(resumed, "Failed to resume suspended process.")

            # Wait for instant spawn of child
            for _ in range(50):
                if gc_pid_file.exists():
                    break
                time.sleep(0.05)

            self.assertTrue(gc_pid_file.exists(), "Instant child process was not spawned after resume.")
            gc_pid = int(gc_pid_file.read_text().strip())
            self.assertTrue(self._is_process_alive(gc_pid), "Spawned child process should be alive.")

            # Terminate Job Object and assert BOTH parent and early-spawned child are terminated
            job.terminate()
            job.close()
            proc.kill()
            proc.wait()

            time.sleep(0.3)
            self.assertFalse(self._is_process_alive(proc.pid), "Parent process is still alive!")
            self.assertFalse(self._is_process_alive(gc_pid), "Early-spawned child escaped the Job Object!")
        finally:
            job.close()
            if proc.poll() is None:
                proc.kill()
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()

        # 2. End-to-end wrapper verification: run via _execute_process with timeout
        instant_child_pid_file = self.test_dir / "wrapper_instant_child.pid"
        instant_script = (
            "import subprocess, sys, time\n"
            "gc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            f"with open(r'{instant_child_pid_file}', 'w') as f:\n"
            "    f.write(str(gc.pid))\n"
            "time.sleep(60)\n"
        )
        with self.assertRaises(RenderTimeoutException):
            self.wrapper._execute_process(
                [sys.executable, "-c", instant_script],
                timeout_sec=0.5,
                check=True,
                cancellation_token=None,
                is_ffprobe=False,
            )

        time.sleep(0.3)
        self.assertTrue(instant_child_pid_file.exists(), "Instant child PID file was not created.")
        early_child_pid = int(instant_child_pid_file.read_text().strip())
        self.assertFalse(
            self._is_process_alive(early_child_pid),
            f"Early-spawned child process {early_child_pid} escaped Job Object on timeout!",
        )


if __name__ == "__main__":
    unittest.main()

