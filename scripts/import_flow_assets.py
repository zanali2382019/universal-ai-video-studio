#!/usr/bin/env python3
"""CLI utility for Google Flow Inbound Asset Ingestion (Task 3.13).

Usage:
  # Import a Google Takeout archive or folder:
  python scripts/import_flow_assets.py import --channel ch_dreammatter_001 --project proj_tommy_ep3 --source path/to/takeout.zip

  # Dry-run validation and matching preview:
  python scripts/import_flow_assets.py import --channel ch_dreammatter_001 --project proj_tommy_ep3 --source path/to/folder --dry-run

  # Check episode scene completion status (imported vs missing):
  python scripts/import_flow_assets.py status --channel ch_dreammatter_001 --project proj_tommy_ep3

  # Switch active take for a scene:
  python scripts/import_flow_assets.py activate-take --channel ch_dreammatter_001 --project proj_tommy_ep3 --scene sc_tommy3_01 --take 2
"""

import sys
import json
import argparse
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.database.connection import DatabaseManager
from src.database.migrations import MigrationManager
from src.storage.service import StorageService
from src.flow.inbound import FlowInboundService


def get_inbound_service() -> FlowInboundService:
    db = DatabaseManager()
    MigrationManager(db).run_migrations()
    storage = StorageService()
    return FlowInboundService(db_manager=db, storage_service=storage)


def cmd_import(args: argparse.Namespace) -> int:
    service = get_inbound_service()
    source_path = Path(args.source).resolve()

    if not source_path.exists():
        print(f"[ERROR] Source path does not exist: {source_path}", file=sys.stderr)
        return 1

    print(f"======================================================================")
    print(f" Google Flow Inbound Asset Ingestion {'[DRY-RUN]' if args.dry_run else ''}")
    print(f"======================================================================")
    print(f" Channel ID : {args.channel}")
    print(f" Project ID : {args.project}")
    print(f" Source     : {source_path}")
    print(f" Dry Run    : {args.dry_run}")
    print(f"======================================================================\n")

    try:
        report = service.import_assets(
            channel_id=args.channel,
            project_id=args.project,
            source_path=source_path,
            package_id=args.package_id,
            dry_run=args.dry_run,
        )

        print(f"--- Inbound Session Summary ---")
        print(f" Record ID         : {report.inbound_record_id}")
        print(f" Source Type       : {report.source_type.value}")
        print(f" Total Inspected   : {report.total_inspected}")
        print(f" Imported (New)    : {report.imported_count}")
        print(f" Duplicates Skipped: {report.duplicate_count}")
        print(f" Versions Updated  : {report.updated_version_count}")
        print(f" Failed Items      : {report.failed_count}")
        print(f" Missing Scenes    : {len(report.missing_scenes)} ({', '.join(report.missing_scenes) if report.missing_scenes else 'None - Complete!'})")
        print(f"-------------------------------\n")

        print("Processed Items:")
        for idx, item in enumerate(report.items, 1):
            status_symbol = {
                "imported": "[NEW]",
                "skipped_duplicate": "[DUP]",
                "updated_version": "[VER]",
                "failed": "[ERR]",
            }.get(item.status.value, "[?]")
            print(
                f" {idx:2d}. {status_symbol} {item.file_name} -> Scene: '{item.scene_id}', "
                f"Take: #{item.take_number} ({item.asset_role}) | Status: {item.status.value}"
            )
            if item.error:
                print(f"     Error: {item.error}")
            elif item.storage_key:
                print(f"     Storage: {item.storage_key}")

        if report.missing_scenes:
            print(f"\n[WARNING] Project has {len(report.missing_scenes)} missing scene(s) without video: {report.missing_scenes}")

        if args.json:
            print("\nJSON Report:")
            print(json.dumps(report.model_dump(), indent=2))

        return 0 if report.failed_count == 0 else 2

    except Exception as e:
        print(f"[FATAL] Inbound import failed: {e}", file=sys.stderr)
        return 1


def cmd_status(args: argparse.Namespace) -> int:
    service = get_inbound_service()
    status = service.get_project_flow_status(args.channel, args.project)

    print(f"======================================================================")
    print(f" Google Flow Project Scene Ingestion Status")
    print(f"======================================================================")
    print(f" Channel ID : {args.channel}")
    print(f" Project ID : {args.project}")
    print(f" Progress   : {status['completed_scenes']}/{status['total_scenes']} scenes ready")
    print(f" Complete   : {'YES' if status['is_complete'] else 'NO'}")
    print(f"======================================================================\n")

    print(f"{'Seq':<4} {'Scene ID':<16} {'PromptVer':<10} {'Takes':<7} {'Active':<8} {'Res/FPS':<16} {'Duration':<10}")
    print("-" * 75)
    for sc in status["scenes"]:
        active_str = f"Take #{sc['active_take_number']}" if sc["has_video"] else "MISSING"
        res_fps = f"{sc['resolution'] or '-'} @ {sc['fps'] or '-'}fps" if sc["has_video"] else "-"
        dur = f"{sc['duration_sec']:.2f}s" if sc["duration_sec"] else "-"
        print(
            f"{sc['sequence_number']:<4} {sc['scene_id']:<16} v{sc['prompt_version']:<9} "
            f"{sc['total_takes']:<7} {active_str:<8} {res_fps:<16} {dur:<10}"
        )

    if status["missing_scenes"]:
        print(f"\nMissing Scenes: {', '.join(status['missing_scenes'])}")
    else:
        print(f"\nAll scenes have active Flow video takes! Project is ready for local render.")

    return 0


def cmd_activate_take(args: argparse.Namespace) -> int:
    service = get_inbound_service()
    success = service.set_active_take(
        project_id=args.project,
        scene_id=args.scene,
        take_number=args.take,
        asset_role=args.role,
    )
    if success:
        print(
            f"[SUCCESS] Activated take #{args.take} ({args.role}) for scene '{args.scene}' "
            f"in project '{args.project}'."
        )
        return 0
    else:
        print(
            f"[ERROR] Take #{args.take} ({args.role}) not found for scene '{args.scene}' "
            f"in project '{args.project}'.",
            file=sys.stderr,
        )
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Google Flow Inbound Asset Ingestion and Management CLI"
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # 1. import
    p_import = subparsers.add_parser("import", help="Import Flow video/image/audio assets")
    p_import.add_argument("--channel", required=True, help="Channel ID (e.g. ch_dreammatter_001)")
    p_import.add_argument("--project", required=True, help="Project ID (e.g. proj_tommy_ep3)")
    p_import.add_argument("--source", required=True, help="Path to zip archive or folder")
    p_import.add_argument("--package-id", default=None, help="Optional Flow PromptPackage ID")
    p_import.add_argument("--dry-run", action="store_true", help="Preview matching and validation without saving")
    p_import.add_argument("--json", action="store_true", help="Output full JSON report")
    p_import.set_defaults(func=cmd_import)

    # 2. status
    p_status = subparsers.add_parser("status", help="Show project scene Flow completion status")
    p_status.add_argument("--channel", required=True, help="Channel ID")
    p_status.add_argument("--project", required=True, help="Project ID")
    p_status.set_defaults(func=cmd_status)

    # 3. activate-take
    p_act = subparsers.add_parser("activate-take", help="Set active take version for a scene")
    p_act.add_argument("--channel", required=True, help="Channel ID")
    p_act.add_argument("--project", required=True, help="Project ID")
    p_act.add_argument("--scene", required=True, help="Scene ID")
    p_act.add_argument("--take", type=int, required=True, help="Take number to activate")
    p_act.add_argument("--role", default="video", help="Asset role (default: video)")
    p_act.set_defaults(func=cmd_activate_take)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

