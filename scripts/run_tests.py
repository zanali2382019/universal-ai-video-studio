#!/usr/bin/env python3
"""Universal AI Video Production Studio - Unified Test Runner.

Usage:
    python scripts/run_tests.py [--suite {portable,production,all}] [-v]

Suites:
    portable:   Runs all unit and integration tests (SQLite, StorageService,
                WorkerPool, synthetic FFmpeg rendering, licensing, etc.).
                Safe for clean CI and environments without local media assets.
    production: Runs acceptance tests that verify local production files for
                Episode 01, Episode 02, and Episode 03.
    all:        Runs both portable and production suites (default).
"""

import argparse
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.rendering.ffmpeg_wrapper import FFmpegWrapper

PORTABLE_TEST_MODULES = [
    "tests.test_config",
    "tests.test_exceptions",
    "tests.test_file_utils",
    "tests.test_logger",
    "tests.test_database",
    "tests.test_channel_repository",
    "tests.test_channel_service",
    "tests.test_channel_isolation",
    "tests.test_mam_and_licenses",
    "tests.test_project_and_scene_service",
    "tests.test_task_repository",
    "tests.test_task_service",
    "tests.test_worker_pool",
    "tests.test_checkpoint_and_recovery",
    "tests.test_load_and_resumption",
    "tests.test_ffmpeg_wrapper",
    "tests.test_timeline_renderer",
    "tests.test_render_task_integration",
    "tests.test_audit_and_crash_recovery",
    "tests.test_audio_experience_and_language",
    "tests.test_task_38_batch1_fixes",
    "tests.test_task_39_storage_separation",
    "tests.test_task_311_flow_prompts",
]

PRODUCTION_TEST_MODULES = [
    "tests.test_first_video_e2e",
    "tests.test_episode_02_e2e",
    "tests.test_episode_03_e2e",
]


def check_prerequisites():
    """Verify system prerequisites (Python version, FFmpeg, FFprobe)."""
    print("=" * 70)
    print(" Universal AI Video Production Studio - Pre-flight Check")
    print("=" * 70)
    print(f" Python Version : {sys.version.split()[0]} ({sys.executable})")

    if sys.version_info < (3, 11):
        print(" [WARNING] Python 3.11+ is recommended.")

    try:
        wrapper = FFmpegWrapper()
        versions = wrapper.get_version_info()
        print(f" FFmpeg Detected: True")
        print(f"   ffmpeg path  : {wrapper.ffmpeg_path}")
        print(f"   ffmpeg ver   : {versions.get('ffmpeg', 'N/A')}")
        print(f"   ffprobe path : {wrapper.ffprobe_path}")
        print(f"   ffprobe ver  : {versions.get('ffprobe', 'N/A')}")
        ffmpeg_detected = True
    except Exception as exc:
        print(f" FFmpeg Detected: False ({exc})")
        print(" [WARNING] FFmpeg or FFprobe not found on PATH. Synthetic rendering tests will fail.")
        ffmpeg_detected = False
    print("=" * 70)
    return ffmpeg_detected


def build_suite(suite_name: str) -> unittest.TestSuite:
    """Build the unittest TestSuite based on selection."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    modules_to_load = []
    if suite_name in ("portable", "all"):
        modules_to_load.extend(PORTABLE_TEST_MODULES)
    if suite_name in ("production", "all"):
        modules_to_load.extend(PRODUCTION_TEST_MODULES)

    for mod_name in modules_to_load:
        try:
            mod_suite = loader.loadTestsFromName(mod_name)
            suite.addTests(mod_suite)
        except Exception as exc:
            print(f"[ERROR] Failed to load module '{mod_name}': {exc}", file=sys.stderr)
            raise

    return suite


def main():
    parser = argparse.ArgumentParser(
        description="Universal AI Video Production Studio Test Automation Runner"
    )
    parser.add_argument(
        "--suite",
        "-s",
        choices=["portable", "production", "all"],
        default="all",
        help="Test suite to run (default: all)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose test output",
    )
    args = parser.parse_args()

    check_prerequisites()

    # Enforce isolated temporary data directory if STUDIO_DATA_DIR not already set
    temp_data_dir = None
    if "STUDIO_DATA_DIR" not in os.environ:
        temp_data_dir = tempfile.mkdtemp(prefix="studio_test_data_")
        os.environ["STUDIO_DATA_DIR"] = temp_data_dir
        print(f" [ISOLATION] STUDIO_DATA_DIR mapped to temporary directory: {temp_data_dir}")

    suite = build_suite(args.suite)
    verbosity = 2 if args.verbose else 1

    print(f"\nRunning test suite '{args.suite}' ({suite.countTestCases()} total test cases)...")
    start_time = time.time()
    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)
    elapsed = time.time() - start_time

    # Clean up isolated temp data dir if we created it
    if temp_data_dir and Path(temp_data_dir).exists():
        try:
            shutil.rmtree(temp_data_dir, ignore_errors=True)
        except Exception:
            pass

    passed_count = result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)

    print("\n" + "=" * 70)
    print(" Test Execution Summary")
    print("=" * 70)
    print(f" Suite        : {args.suite}")
    print(f" Total Run    : {result.testsRun}")
    print(f" Passed       : {passed_count}")
    print(f" Skipped      : {len(result.skipped)}")
    print(f" Failures     : {len(result.failures)}")
    print(f" Errors       : {len(result.errors)}")
    print(f" Duration     : {elapsed:.2f}s")
    print("=" * 70)

    if result.wasSuccessful():
        print(" RESULT: SUCCESS\n")
        sys.exit(0)
    else:
        print(" RESULT: FAILED\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
