"""Google Takeout and Flow export archive / folder parser (Task 3.13).

Provides:
- Unpacking and parsing of Google Takeout archives (.zip)
- Parsing of Flow export JSON metadata (flexible schemas, non-destructive to unparsed fields)
- Discovers media items paired with metadata or standalone files
"""

import os
import json
import zipfile
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from src.core.logger import get_logger
from src.flow.inbound.schemas import TakeoutParsedItem

logger = get_logger("studio.flow.inbound.takeout_parser")

MEDIA_EXTENSIONS = {
    ".mp4", ".mov", ".webm", ".mkv", ".avi",
    ".png", ".jpg", ".jpeg", ".webp",
    ".wav", ".mp3", ".aac", ".flac",
}


class TakeoutParser:
    """Parses Google Takeout zip archives and unzipped Flow folders."""

    def __init__(self) -> None:
        pass

    def parse_source(self, source_path: Path, extract_target_dir: Optional[Path] = None) -> Tuple[Path, List[TakeoutParsedItem], List[Path]]:
        """Inspect and parse a source archive or directory.
        
        Args:
            source_path: Path to .zip archive or directory.
            extract_target_dir: Directory where zip should be extracted if applicable.
            
        Returns:
            Tuple[Path, List[TakeoutParsedItem], List[Path]]:
                - root_dir: Path to directory containing files.
                - parsed_items: Items matched from JSON metadata.
                - unreferenced_media_files: Media files found that had no JSON metadata entry.
        """
        source = Path(source_path).resolve()
        if not source.exists():
            raise FileNotFoundError(f"Inbound source path does not exist: {source}")

        root_dir = source
        # 1. If it's a zip file, extract it
        if source.is_file() and source.suffix.lower() == ".zip":
            if not extract_target_dir:
                extract_target_dir = source.parent / f"_extracted_{source.stem}"
            extract_target_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Extracting Takeout zip '{source.name}' to '{extract_target_dir}'...")
            with zipfile.ZipFile(source, "r") as zf:
                zf.extractall(extract_target_dir)
            root_dir = extract_target_dir

        # 2. Find all JSON files and media files in directory
        json_files: List[Path] = []
        media_files: List[Path] = []

        for p in root_dir.rglob("*"):
            if p.is_file():
                ext = p.suffix.lower()
                if ext == ".json":
                    json_files.append(p)
                elif ext in MEDIA_EXTENSIONS:
                    media_files.append(p)

        parsed_items: List[TakeoutParsedItem] = []
        referenced_paths: set = set()

        # 3. Parse JSON metadata files
        for jf in json_files:
            try:
                with open(jf, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                items = self._extract_items_from_json(data, jf.parent, root_dir)
                for item in items:
                    parsed_items.append(item)
                    referenced_paths.add((root_dir / item.relative_file_path).resolve())
            except Exception as e:
                logger.warning(f"Failed to parse metadata JSON '{jf.name}': {e}")

        # 4. Collect media files that were not referenced by any JSON
        unreferenced_media = [
            mf for mf in media_files if mf.resolve() not in referenced_paths
        ]

        logger.info(
            f"Parsed source '{source.name}': found {len(parsed_items)} metadata item(s) "
            f"and {len(unreferenced_media)} unreferenced media file(s)."
        )
        return root_dir, parsed_items, unreferenced_media

    def _extract_items_from_json(
        self, data: Any, json_dir: Path, root_dir: Path
    ) -> List[TakeoutParsedItem]:
        """Extract TakeoutParsedItem records from various JSON structures."""
        results: List[TakeoutParsedItem] = []

        raw_list: List[Dict[str, Any]] = []
        if isinstance(data, list):
            raw_list = [item for item in data if isinstance(item, dict)]
        elif isinstance(data, dict):
            # Check common keys: scenes, generations, items, outputs, assets
            for candidate_key in ("scenes", "generations", "items", "outputs", "assets", "takes"):
                if candidate_key in data and isinstance(data[candidate_key], list):
                    raw_list.extend(
                        [item for item in data[candidate_key] if isinstance(item, dict)]
                    )
            # If no list found, perhaps the dict itself represents a single scene/generation
            if not raw_list and any(k in data for k in ("scene_id", "scene", "file_name", "file_path", "video_path")):
                raw_list.append(data)

        for raw in raw_list:
            item = self._parse_single_raw_dict(raw, json_dir, root_dir)
            if item:
                results.append(item)

        return results

    def _parse_single_raw_dict(
        self, raw: Dict[str, Any], json_dir: Path, root_dir: Path
    ) -> Optional[TakeoutParsedItem]:
        """Parse a single dictionary into TakeoutParsedItem."""
        # Find media path
        path_candidates = [
            raw.get("file_path"),
            raw.get("file_name"),
            raw.get("filename"),
            raw.get("media_path"),
            raw.get("video_path"),
            raw.get("asset_path"),
            raw.get("output_file"),
        ]
        chosen_path: Optional[str] = None
        for candidate in path_candidates:
            if candidate and isinstance(candidate, str):
                chosen_path = candidate
                break

        if not chosen_path:
            return None

        # Resolve relative to root_dir
        # Try direct, relative to json_dir, or relative to root_dir
        resolved_rel: Optional[str] = None
        p_cand = Path(chosen_path)
        if (root_dir / p_cand).is_file():
            resolved_rel = p_cand.as_posix()
        elif (json_dir / p_cand).is_file():
            resolved_rel = (json_dir / p_cand).relative_to(root_dir).as_posix()
        else:
            # Match by basename inside root_dir
            for f in root_dir.rglob(p_cand.name):
                if f.is_file():
                    resolved_rel = f.relative_to(root_dir).as_posix()
                    break

        if not resolved_rel:
            resolved_rel = p_cand.as_posix()

        # Find scene identifier
        scene_id_candidates = [
            raw.get("scene_id"),
            raw.get("scene"),
            raw.get("scene_name"),
            raw.get("scene_number"),
            raw.get("sequence_number"),
        ]
        scene_identifier: str = ""
        for s_cand in scene_id_candidates:
            if s_cand is not None:
                scene_identifier = str(s_cand)
                break

        if not scene_identifier:
            scene_identifier = Path(chosen_path).stem

        # Extract take / version
        take_num = 1
        for t_cand in (raw.get("take_number"), raw.get("take"), raw.get("version"), raw.get("iteration")):
            if t_cand is not None:
                try:
                    take_num = int(t_cand)
                    break
                except ValueError:
                    pass

        # Extract prompt text
        prompt_text = raw.get("prompt") or raw.get("prompt_text") or raw.get("visual_prompt")
        generation_id = raw.get("generation_id") or raw.get("id")
        seed = raw.get("seed")

        # Determine asset role
        ext = Path(chosen_path).suffix.lower()
        asset_role = "video"
        if ext in {".png", ".jpg", ".jpeg", ".webp"}:
            asset_role = "image"
        elif ext in {".wav", ".mp3", ".aac", ".flac"}:
            asset_role = "audio"

        # Capture unparsed fields without inventing or modifying
        known_keys = {
            "file_path", "file_name", "filename", "media_path", "video_path",
            "asset_path", "output_file", "scene_id", "scene", "scene_name",
            "scene_number", "sequence_number", "take_number", "take",
            "version", "iteration", "prompt", "prompt_text", "visual_prompt",
            "generation_id", "id", "seed",
        }
        unparsed = {k: v for k, v in raw.items() if k not in known_keys}

        return TakeoutParsedItem(
            relative_file_path=resolved_rel,
            scene_identifier=scene_identifier,
            take_number=take_num,
            prompt_version=raw.get("prompt_version", 1),
            asset_role=asset_role,
            prompt_text=prompt_text,
            generation_id=generation_id,
            seed=seed,
            unparsed_metadata=unparsed,
        )

