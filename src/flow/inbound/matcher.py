"""Filename and metadata matcher for Google Flow Inbound items (Task 3.13).

Provides:
- Naming convention parsing for manual drop and PromptPackage alignment
- Direct scene_id extraction and scene sequence index mapping
- Take/version number extraction (e.g. _v2, _take2)
- Asset role deduction (video, image, audio)
"""

import re
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from src.core.logger import get_logger
from src.flow.inbound.schemas import MatchedMediaItem

logger = get_logger("studio.flow.inbound.matcher")


class MediaMatcher:
    """Matches filenames and files to project scenes and takes."""

    def __init__(self, known_scenes: Optional[List[Dict[str, Any]]] = None) -> None:
        """Initialize matcher with optional list of project scenes.
        
        Args:
            known_scenes: List of scene dicts, each with 'id' and 'sequence_number'.
        """
        self._scenes = known_scenes or []
        self._scene_id_map: Dict[str, Dict[str, Any]] = {
            s["id"]: s for s in self._scenes if "id" in s
        }
        self._seq_map: Dict[int, Dict[str, Any]] = {
            s["sequence_number"]: s for s in self._scenes if "sequence_number" in s
        }

    def match_file(self, file_path: str) -> Optional[MatchedMediaItem]:
        """Match a file path against known scenes or naming patterns.
        
        Args:
            file_path: Relative or absolute path to a media file.
            
        Returns:
            Optional[MatchedMediaItem]: Matched item info, or None if unmatchable.
        """
        p = Path(file_path)
        stem = p.stem.lower()
        ext = p.suffix.lower()

        # 1. Determine asset role
        asset_role = "video"
        if ext in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
            asset_role = "image"
        elif ext in {".wav", ".mp3", ".aac", ".flac", ".m4a"}:
            asset_role = "audio"

        # 2. Extract take / version number
        take_number = self._extract_take_number(stem)

        # 3. Check exact scene_id match
        matched_scene_id: Optional[str] = None
        prompt_version = 1

        # Look for explicit scene IDs in stem
        for scene_id in sorted(self._scene_id_map.keys(), key=len, reverse=True):
            if scene_id.lower() in stem:
                matched_scene_id = scene_id
                break

        # 4. If no exact ID match, try sequence index extraction (e.g. sc_01, scene_1, ep3_scene1)
        if not matched_scene_id:
            seq_idx = self._extract_sequence_index(stem)
            if seq_idx is not None:
                if seq_idx in self._seq_map:
                    matched_scene_id = self._seq_map[seq_idx]["id"]
                else:
                    # Fallback scene_id formatted from sequence index
                    matched_scene_id = f"sc_{seq_idx:02d}"

        # 5. If still no match but filename starts with sc_, take the base
        if not matched_scene_id:
            m = re.match(r"^(sc_[a-z0-9_-]+?)(?:_v\d+|_take\d+|$)", stem)
            if m:
                matched_scene_id = m.group(1)

        if not matched_scene_id:
            logger.debug(f"Could not match file '{p.name}' to any scene.")
            return None

        return MatchedMediaItem(
            source_path=str(p),
            file_name=p.name,
            scene_id=matched_scene_id,
            take_number=take_number,
            prompt_version=prompt_version,
            asset_role=asset_role,
        )

    def _extract_take_number(self, stem: str) -> int:
        """Extract take/version number from filename stem."""
        # Check patterns: _v2, _take2, _take_2, _ver2, _iteration2
        patterns = [
            r"(?:_|-)(?:v|ver|version)(\d+)\b",
            r"(?:_|-)(?:take|take_)(\d+)\b",
            r"(?:_|-)(?:iter|iteration)(\d+)\b",
        ]
        for pat in patterns:
            match = re.search(pat, stem)
            if match:
                try:
                    val = int(match.group(1))
                    if val > 0:
                        return val
                except ValueError:
                    pass
        return 1

    def _extract_sequence_index(self, stem: str) -> Optional[int]:
        """Extract 1-based scene sequence number from filename."""
        patterns = [
            r"(?:scene|sc)[_\s-]*0*(\d+)\b",
            r"(?:ep\d+[_\s-]*)?(?:scene|sc)[_\s-]*0*(\d+)",
        ]
        for pat in patterns:
            match = re.search(pat, stem)
            if match:
                try:
                    val = int(match.group(1))
                    if val > 0:
                        return val
                except ValueError:
                    pass
        return None

