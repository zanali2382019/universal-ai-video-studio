#!/usr/bin/env python3
"""Google Flow Outbound Package Generator CLI (Task 3.11).

Automates generation, inspection, editing, and export of Google Flow prompt packages
for Universal AI Video Production Studio.

Commands:
    inspect     Review channel, projects, and scene data
    generate    Synthesize Master and Scene Prompts into a PromptPackage
    edit        Modify a scene prompt, creating a new version without losing history
    history     Display complete version history for a scene's prompt
    export      Export PromptPackage to JSON and Markdown via StorageService

Usage Examples:
    # Inspect project scenes
    python scripts/generate_flow_package.py inspect -c ch_dreammatter_001 -m channels/ch_dreammatter_001/episodes/episode_03.json

    # Generate and export Flow package
    python scripts/generate_flow_package.py export -c ch_dreammatter_001 -m channels/ch_dreammatter_001/episodes/episode_03.json --flow-project "DreamMatter Ep 03"

    # Edit a scene prompt (saves version 2 automatically)
    python scripts/generate_flow_package.py edit -s sc_tommy3_01 --prompt "Tommy waving happily to a fluffy cloud drifting from the sky."

    # View version history
    python scripts/generate_flow_package.py history -s sc_tommy3_01
"""

import argparse
import json
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.flow.prompt_service import FlowPromptService
from src.flow.schemas import FlowGenerationMode
from src.core.config import get_settings


def cmd_inspect(args, service: FlowPromptService):
    """Inspect channel, project, and episode scenes."""
    print("=" * 70)
    print(" Google Flow - Episode & Scene Inspector")
    print("=" * 70)
    print(f" Channel : {args.channel}")

    if args.manifest:
        p = Path(args.manifest)
        if not p.is_file():
            p = get_settings().project_root / args.manifest
        if not p.is_file():
            print(f"[ERROR] Manifest file not found: {args.manifest}", file=sys.stderr)
            sys.exit(1)
        with open(p, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        print(f" Project : {manifest.get('project_id')}")
        print(f" Title   : {manifest.get('title')}")
        print(f" Ratio   : {manifest.get('aspect_ratio', '9:16')}")
        print(f" Target  : {manifest.get('target_duration_sec', 0)}s")
        print("-" * 70)
        scenes = manifest.get("scenes", [])
        print(f" Found {len(scenes)} scene(s):")
        for sc in scenes:
            print(f"   [{sc.get('sequence_number', 1)}] {sc.get('id')} - {sc.get('title')}")
            print(f"       Duration : {sc.get('duration_seconds')}s")
            print(f"       Narration: \"{sc.get('narration_text', '')}\"")
            if sc.get("visual_prompt"):
                print(f"       Visual   : {sc.get('visual_prompt')}")
            asset = sc.get("asset", {})
            if asset.get("rel_path"):
                print(f"       Asset    : {asset.get('rel_path')}")
    else:
        print("Please provide --manifest (-m) pointing to an episode JSON file.")
    print("=" * 70)


def cmd_generate(args, service: FlowPromptService):
    """Generate and display Flow PromptPackage."""
    print("=" * 70)
    print(" Google Flow - Synthesizing Prompt Package")
    print("=" * 70)

    package = service.build_package_from_episode_manifest(
        channel_id=args.channel,
        manifest_data_or_path=args.manifest,
        flow_project_name=args.flow_project,
        flow_workspace_ref=args.flow_workspace,
        outfit_id=getattr(args, "outfit", None),
    )

    print(f" Package ID   : {package.package_id}")
    print(f" Version      : {package.package_version}")
    print(f" Content Hash : {package.content_hash}")
    print(f" Flow Project : {package.flow_project_ref.flow_project_name}")
    print(f" Master Prompt: {package.master_prompt}")
    print("-" * 70)
    print(f" Scenes ({len(package.scene_specs)}):")
    for s in package.scene_specs:
        print(f"\n --- Scene {s.scene_index}: {s.title} ({s.scene_id}) [v{s.prompt_version}] ---")
        print(f"  Mode   : {s.generation_mode.value.upper()}")
        print(f"  Prompt : {s.compile_full_flow_prompt(package.global_style_ref)}")
        print(f"  Camera : {s.camera_instructions}")
        print(f"  Motion : {s.motion_instructions}")
        print(f"  Light  : {s.lighting_instructions}")

    print("\n" + "=" * 70)
    print(" Package synthesized successfully. Use 'export' to save to StorageService.")
    print("=" * 70)


def cmd_edit(args, service: FlowPromptService):
    """Edit a scene prompt, creating a new version without deleting history."""
    print("=" * 70)
    print(f" Google Flow - Modifying Scene Prompt: {args.scene}")
    print("=" * 70)

    gen_mode = FlowGenerationMode(args.mode) if args.mode else None

    result = service.modify_scene_prompt(
        scene_id=args.scene,
        visual_prompt=args.prompt,
        generation_mode=gen_mode,
        camera_instructions=args.camera,
        lighting_instructions=args.lighting,
        motion_instructions=args.motion,
        continuity_instructions=args.continuity,
        negative_prompt=args.negative_prompt,
    )

    print(f" Scene ID     : {result.scene_id}")
    print(f" New Version  : v{result.prompt_version} (Historical versions preserved)")
    print(f" Visual Prompt: {result.visual_prompt}")
    print(f" Gen Mode     : {result.generation_mode.value}")
    if result.camera_instructions:
        print(f" Camera       : {result.camera_instructions}")
    if result.motion_instructions:
        print(f" Motion       : {result.motion_instructions}")
    print("=" * 70)


def cmd_history(args, service: FlowPromptService):
    """Display complete prompt version history for a scene."""
    print("=" * 70)
    print(f" Google Flow - Prompt Version History: {args.scene}")
    print("=" * 70)

    history = service.get_scene_prompt_history(args.scene)
    if not history:
        print(f" No prompt versions found for scene '{args.scene}'.")
    else:
        for row in history:
            print(f" [v{row['version']}] Prompt ID: {row['id']} | Mode: {row['generation_mode']} | Created: {row['created_at']}")
            print(f"     Prompt: {row['visual_prompt']}")
            if row.get("camera_instructions"):
                print(f"     Camera: {row['camera_instructions']}")
            if row.get("motion_instructions"):
                print(f"     Motion: {row['motion_instructions']}")
            print("-" * 50)
    print("=" * 70)


def cmd_export(args, service: FlowPromptService):
    """Generate and export package to JSON and Markdown via StorageService."""
    print("=" * 70)
    print(" Google Flow - Exporting Production Package via StorageService")
    print("=" * 70)

    package = service.build_package_from_episode_manifest(
        channel_id=args.channel,
        manifest_data_or_path=args.manifest,
        flow_project_name=args.flow_project,
        flow_workspace_ref=args.flow_workspace,
        outfit_id=getattr(args, "outfit", None),
    )

    result = service.export_package(package)

    print(f" Package ID   : {result.package_id} (Version {result.package_version})")
    print(f" Content Hash : {result.content_hash}")
    print(f" Scene Count  : {result.scene_count}")
    print(f" Channel ID   : {result.channel_id}")
    print(f" Project ID   : {result.project_id}")
    print(f" JSON Key     : {result.json_storage_key}")
    print(f" Markdown Key : {result.markdown_storage_key}")

    # Resolve local paths for reporting
    json_path = service.storage_service.get_local_path(result.json_storage_key)
    md_path = service.storage_service.get_local_path(result.markdown_storage_key)
    print(f" JSON Path    : {json_path} ({json_path.stat().st_size} bytes)")
    print(f" Markdown Path: {md_path} ({md_path.stat().st_size} bytes)")

    print("\n" + "=" * 70)
    print(" EXPORT SUCCESSFUL: Ready for manual paste into Google Flow!")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description="Google Flow Outbound Package Generator CLI (Task 3.11)"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Inspect
    p_inspect = subparsers.add_parser("inspect", help="Inspect episode scenes and prompts")
    p_inspect.add_argument("-c", "--channel", default="ch_dreammatter_001", help="Channel ID")
    p_inspect.add_argument("-m", "--manifest", required=True, help="Path to episode manifest JSON")

    # Generate
    p_gen = subparsers.add_parser("generate", help="Synthesize Master and Scene Prompts")
    p_gen.add_argument("-c", "--channel", default="ch_dreammatter_001", help="Channel ID")
    p_gen.add_argument("-m", "--manifest", required=True, help="Path to episode manifest JSON")
    p_gen.add_argument("--outfit", help="Specific outfit profile ID (e.g. 'gardening', 'stargazer_pajamas')")
    p_gen.add_argument("--flow-project", help="Project name in Google Flow UI")
    p_gen.add_argument("--flow-workspace", help="Workspace reference in Google Flow")

    # Edit
    p_edit = subparsers.add_parser("edit", help="Modify a scene prompt creating a new version")
    p_edit.add_argument("-s", "--scene", required=True, help="Scene ID to modify")
    p_edit.add_argument("-p", "--prompt", required=True, help="New visual prompt text")
    p_edit.add_argument("--mode", choices=["text_to_video", "frames_to_video", "ingredients_to_video"], help="Flow generation mode")
    p_edit.add_argument("--camera", help="Camera instructions")
    p_edit.add_argument("--lighting", help="Lighting instructions")
    p_edit.add_argument("--motion", help="Motion instructions")
    p_edit.add_argument("--continuity", help="Continuity instructions")
    p_edit.add_argument("--negative-prompt", help="Negative prompt")

    # History
    p_hist = subparsers.add_parser("history", help="Show prompt version history for a scene")
    p_hist.add_argument("-s", "--scene", required=True, help="Scene ID")

    # Export
    p_export = subparsers.add_parser("export", help="Export package to JSON & Markdown via StorageService")
    p_export.add_argument("-c", "--channel", default="ch_dreammatter_001", help="Channel ID")
    p_export.add_argument("-m", "--manifest", required=True, help="Path to episode manifest JSON")
    p_export.add_argument("--outfit", help="Specific outfit profile ID (e.g. 'gardening', 'stargazer_pajamas')")
    p_export.add_argument("--flow-project", help="Project name in Google Flow UI")
    p_export.add_argument("--flow-workspace", help="Workspace reference in Google Flow")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    service = FlowPromptService()

    if args.command == "inspect":
        cmd_inspect(args, service)
    elif args.command == "generate":
        cmd_generate(args, service)
    elif args.command == "edit":
        cmd_edit(args, service)
    elif args.command == "history":
        cmd_history(args, service)
    elif args.command == "export":
        cmd_export(args, service)


if __name__ == "__main__":
    main()

