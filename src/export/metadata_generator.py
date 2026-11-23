"""Platform-specific metadata generator grounded strictly in project and channel data (Guardrail F).

Enforces:
1. Pure grounded generation (zero hallucination) using actual project, scenes, and channel data.
2. Distinct, tailored metadata structures for YouTube Shorts, Instagram Reels, TikTok, and Standard YouTube.
3. Strict character length, chapter calculation, and hashtag constraints.
"""
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field

from src.database.models import ProjectModel, SceneModel, ChannelModel
from src.export.profiles import PlatformExportProfile, ExportPlatform


class PlatformMetadataPackage(BaseModel):
    """Complete metadata package tailored for a specific platform."""
    platform: str
    profile_id: str
    title: str
    description: str
    tags: List[str] = Field(default_factory=list)
    hashtags: List[str] = Field(default_factory=list)
    chapters: List[Dict[str, Any]] = Field(default_factory=list)
    custom_fields: Dict[str, Any] = Field(default_factory=dict)
    character_counts: Dict[str, int] = Field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class PlatformMetadataGenerator:
    """Generates grounded, non-hallucinated publishing metadata."""

    @staticmethod
    def generate_metadata(
        project: ProjectModel,
        scenes: List[SceneModel],
        channel: ChannelModel,
        profile: PlatformExportProfile,
    ) -> PlatformMetadataPackage:
        """Generate platform-specific grounded metadata."""
        platform = profile.platform
        proj_title = (project.title or "Untitled Video").strip()
        proj_desc = (project.description or "").strip()
        ch_name = (channel.name or channel.id).strip()
        ch_tags: List[str] = []
        if getattr(channel, "niche", None):
            ch_tags.append(channel.niche)
        if getattr(channel, "supported_locales", None) and isinstance(channel.supported_locales, list):
            ch_tags.extend(channel.supported_locales)
        prof = getattr(channel, "profile_json", {}) or {}
        if isinstance(prof, dict) and prof.get("tags"):
            extra_tags = prof["tags"]
            if isinstance(extra_tags, list):
                ch_tags.extend([str(t).strip() for t in extra_tags if str(t).strip()])
            elif isinstance(extra_tags, str):
                ch_tags.extend([t.strip() for t in extra_tags.split(",") if t.strip()])

        # Sort scenes by sequence number
        sorted_scenes = sorted(scenes, key=lambda s: s.sequence_number)

        # Build chapter markers from scenes
        chapters: List[Dict[str, Any]] = []
        current_time = 0.0
        for sc in sorted_scenes:
            m = int(current_time // 60)
            s = int(current_time % 60)
            timecode = f"{m:02d}:{s:02d}"
            sc_title = sc.title.strip() if sc.title else f"Scene {sc.sequence_number}"
            chapters.append({
                "sequence": sc.sequence_number,
                "timecode": timecode,
                "start_seconds": round(current_time, 2),
                "title": sc_title,
                "narration_summary": (sc.narration_text[:60] + "...") if len(sc.narration_text) > 60 else sc.narration_text,
            })
            current_time += sc.duration_seconds

        # Grounded tags from channel niche and project keywords
        tags: List[str] = []
        tags.extend(ch_tags)
        if project.metadata_json.get("tags"):
            p_tags = project.metadata_json["tags"]
            if isinstance(p_tags, list):
                tags.extend([str(t).strip() for t in p_tags if str(t).strip()])
            elif isinstance(p_tags, str):
                tags.extend([t.strip() for t in p_tags.split(",") if t.strip()])

        # Remove duplicates while preserving order
        unique_tags = list(dict.fromkeys(tags))

        if platform == ExportPlatform.YOUTUBE_SHORTS:
            return PlatformMetadataGenerator._build_youtube_shorts(
                proj_title, proj_desc, ch_name, sorted_scenes, unique_tags, chapters, profile
            )
        elif platform == ExportPlatform.INSTAGRAM_REELS:
            return PlatformMetadataGenerator._build_instagram_reels(
                proj_title, proj_desc, ch_name, sorted_scenes, unique_tags, profile
            )
        elif platform == ExportPlatform.TIKTOK:
            return PlatformMetadataGenerator._build_tiktok(
                proj_title, proj_desc, ch_name, sorted_scenes, unique_tags, profile
            )
        else:
            return PlatformMetadataGenerator._build_youtube_standard(
                proj_title, proj_desc, ch_name, sorted_scenes, unique_tags, chapters, profile
            )

    @staticmethod
    def _build_youtube_shorts(
        title: str,
        desc: str,
        channel_name: str,
        scenes: List[SceneModel],
        tags: List[str],
        chapters: List[Dict[str, Any]],
        profile: PlatformExportProfile,
    ) -> PlatformMetadataPackage:
        # Title limit: 100 characters. Append #Shorts if room allows.
        title_tag = " #Shorts"
        max_title_len = 100
        if len(title) + len(title_tag) <= max_title_len:
            final_title = f"{title}{title_tag}"
        else:
            final_title = title[:max_title_len].strip()

        # Description: Brief summary, scenes narration context, channel mention
        desc_parts: List[str] = []
        if desc:
            desc_parts.append(desc)

        # Narration synopsis if available
        narration_snippets = [s.narration_text.strip() for s in scenes if s.narration_text.strip()]
        if narration_snippets:
            full_narr = " ".join(narration_snippets)
            desc_parts.append("\n" + full_narr[:500])

        desc_parts.append(f"\nCreated with {channel_name} AI Video Studio.")
        desc_parts.append("#Shorts #AI #Video")

        final_desc = "\n".join(desc_parts)[:5000]

        hashtags = ["#Shorts", "#AI"]
        for t in tags[:10]:
            clean_tag = t.replace(" ", "").replace("-", "_")
            if not clean_tag.startswith("#"):
                clean_tag = f"#{clean_tag}"
            if clean_tag not in hashtags:
                hashtags.append(clean_tag)

        return PlatformMetadataPackage(
            platform=ExportPlatform.YOUTUBE_SHORTS.value,
            profile_id=profile.profile_id,
            title=final_title,
            description=final_desc,
            tags=tags[:20],
            hashtags=hashtags,
            chapters=chapters,
            custom_fields={"category_id": "22", "privacy_status": "public"},
            character_counts={
                "title": len(final_title),
                "description": len(final_desc),
                "tags_total": sum(len(t) for t in tags[:20]),
            },
        )

    @staticmethod
    def _build_instagram_reels(
        title: str,
        desc: str,
        channel_name: str,
        scenes: List[SceneModel],
        tags: List[str],
        profile: PlatformExportProfile,
    ) -> PlatformMetadataPackage:
        # Reels Caption limit: 2,200 chars.
        caption_lines: List[str] = []
        caption_lines.append(title)
        if desc:
            caption_lines.append(desc)

        # Highlight key scene narrations
        key_lines = [s.narration_text.strip() for s in scenes if s.narration_text.strip()][:3]
        if key_lines:
            caption_lines.append("\n" + "\n".join(f"• {k}" for k in key_lines))

        # Hashtags (Instagram allows up to 30)
        hashtags = ["#reels", "#reelsinstagram", "#viral", "#instavideo"]
        for t in tags[:25]:
            clean_tag = t.replace(" ", "").replace("-", "_")
            if not clean_tag.startswith("#"):
                clean_tag = f"#{clean_tag}"
            if clean_tag not in hashtags:
                hashtags.append(clean_tag)

        caption_lines.append("\n" + " ".join(hashtags[:30]))
        caption = "\n\n".join(caption_lines)[:2200]

        return PlatformMetadataPackage(
            platform=ExportPlatform.INSTAGRAM_REELS.value,
            profile_id=profile.profile_id,
            title=title[:100],
            description=caption,
            tags=tags[:25],
            hashtags=hashtags[:30],
            chapters=[],
            custom_fields={"share_to_feed": True},
            character_counts={
                "title": len(title[:100]),
                "description": len(caption),
            },
        )

    @staticmethod
    def _build_tiktok(
        title: str,
        desc: str,
        channel_name: str,
        scenes: List[SceneModel],
        tags: List[str],
        profile: PlatformExportProfile,
    ) -> PlatformMetadataPackage:
        # TikTok Caption limit: 2,200 characters
        caption_parts: List[str] = [title]
        if desc:
            caption_parts.append(desc[:300])

        hashtags = ["#fyp", "#foryou", "#tiktokvideo", "#ai"]
        for t in tags[:15]:
            clean_tag = t.replace(" ", "").replace("-", "_")
            if not clean_tag.startswith("#"):
                clean_tag = f"#{clean_tag}"
            if clean_tag not in hashtags:
                hashtags.append(clean_tag)

        caption_parts.append(" ".join(hashtags[:20]))
        final_caption = "\n\n".join(caption_parts)[:2200]

        return PlatformMetadataPackage(
            platform=ExportPlatform.TIKTOK.value,
            profile_id=profile.profile_id,
            title=title[:100],
            description=final_caption,
            tags=tags[:15],
            hashtags=hashtags[:20],
            chapters=[],
            custom_fields={"allow_duet": True, "allow_stitch": True, "allow_comments": True},
            character_counts={
                "title": len(title[:100]),
                "description": len(final_caption),
            },
        )

    @staticmethod
    def _build_youtube_standard(
        title: str,
        desc: str,
        channel_name: str,
        scenes: List[SceneModel],
        tags: List[str],
        chapters: List[Dict[str, Any]],
        profile: PlatformExportProfile,
    ) -> PlatformMetadataPackage:
        final_title = title[:100]

        desc_parts: List[str] = []
        if desc:
            desc_parts.append(desc)

        # Chapters section
        if chapters:
            desc_parts.append("\nTimestamps:")
            for ch in chapters:
                desc_parts.append(f"{ch['timecode']} - {ch['title']}")

        # Narration synopsis
        narr_texts = [s.narration_text.strip() for s in scenes if s.narration_text.strip()]
        if narr_texts:
            desc_parts.append("\nSynopsis:\n" + " ".join(narr_texts)[:1000])

        desc_parts.append(f"\nChannel: {channel_name}")
        final_desc = "\n".join(desc_parts)[:5000]

        hashtags = ["#Video", "#YouTube"]
        for t in tags[:15]:
            clean_tag = t.replace(" ", "").replace("-", "_")
            if not clean_tag.startswith("#"):
                clean_tag = f"#{clean_tag}"
            if clean_tag not in hashtags:
                hashtags.append(clean_tag)

        return PlatformMetadataPackage(
            platform=ExportPlatform.YOUTUBE_STANDARD.value,
            profile_id=profile.profile_id,
            title=final_title,
            description=final_desc,
            tags=tags[:30],
            hashtags=hashtags,
            chapters=chapters,
            custom_fields={"category_id": "22", "privacy_status": "public"},
            character_counts={
                "title": len(final_title),
                "description": len(final_desc),
                "tags_total": sum(len(t) for t in tags[:30]),
            },
        )
