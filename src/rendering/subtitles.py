"""Arabic & multilingual subtitle generation, styling, and typography engine (Task 3.14).

Provides:
- SRT and ASS (Advanced SubStation Alpha) subtitle generation
- Native RTL and Arabic text handling for libass (avoiding double-shaping)
- Pre-shaping via arabic-reshaper and python-bidi for drawtext/Pillow pathways
- Font discovery and auto-selection across Windows and Linux CI
- Outline/stroke, drop shadow, and translucent background box styling
- Word-level and sentence-level timing synchronization
"""

import os
import re
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

import arabic_reshaper
from bidi.algorithm import get_display

from src.core.logger import get_logger

logger = get_logger("studio.rendering.subtitles")


@dataclass
class WordTiming:
    """Word-level timing information."""
    word: str
    start_sec: float
    end_sec: float


@dataclass
class SubtitleItem:
    """Individual subtitle event with timing and text."""
    index: int = 1
    start_sec: float = 0.0
    end_sec: float = 0.0
    text: str = ""
    words: Optional[List[WordTiming]] = None

    def __init__(
        self,
        index: int = 1,
        start_sec: Optional[float] = None,
        end_sec: Optional[float] = None,
        text: str = "",
        words: Optional[List[WordTiming]] = None,
        start_seconds: Optional[float] = None,
        end_seconds: Optional[float] = None,
    ):
        self.index = index
        self.start_sec = start_sec if start_sec is not None else (start_seconds or 0.0)
        self.end_sec = end_sec if end_sec is not None else (end_seconds or 0.0)
        self.text = text
        self.words = words

    @property
    def start_seconds(self) -> float:
        return self.start_sec

    @property
    def end_seconds(self) -> float:
        return self.end_sec

    @property
    def duration_sec(self) -> float:
        return max(0.0, self.end_sec - self.start_sec)


@dataclass
class SubtitleStyle:
    """Styling specification for ASS subtitles."""
    font_name: str = "Segoe UI"
    font_size: int = 38
    primary_color: str = "&H00FFFFFF"   # White (AABBGGRR)
    secondary_color: str = "&H0000FFFF" # Yellow highlight
    outline_color: str = "&H00000000"   # Black outline
    back_color: str = "&H80000000"      # 50% translucent black box
    bold: bool = True
    italic: bool = False
    border_style: int = 1               # 1 = Outline + drop shadow, 3 = Opaque/translucent box
    outline_width: float = 2.5
    shadow_depth: float = 1.5
    alignment: int = 2                  # 2 = Bottom-center
    margin_l: int = 40
    margin_r: int = 40
    margin_v: int = 80                  # Vertical distance from bottom


def find_system_arabic_font() -> str:
    """Detect an available Arabic-capable font on the current operating system."""
    if sys.platform == "win32":
        win_fonts = Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts"
        candidates = ["segoeui.ttf", "arial.ttf", "tahoma.ttf", "calibri.ttf"]
        for c in candidates:
            if (win_fonts / c).exists():
                return Path(c).stem.replace("ui", " UI").title()
        return "Segoe UI"
    else:
        # Linux / Ubuntu CI candidates
        linux_paths = [
            "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansArabic-Regular.otf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        ]
        for p in linux_paths:
            if Path(p).exists():
                if "noto" in p.lower():
                    return "Noto Sans Arabic"
                if "dejavu" in p.lower():
                    return "DejaVu Sans"
                return "FreeSans"
        return "DejaVu Sans"


def reshape_for_drawtext(text: str) -> str:
    """Reshape and reorder Arabic text specifically for FFmpeg drawtext or non-HarfBuzz renderers.
    
    WARNING: Do NOT use this for libass / ASS subtitles, as libass already performs
    proper HarfBuzz shaping and FriBidi layout internally.
    """
    if not text:
        return ""
    # Detect if contains Arabic characters (Unicode block 0600-06FF)
    has_arabic = bool(re.search(r"[\u0600-\u06FF]", text))
    if has_arabic:
        reshaped = arabic_reshaper.reshape(text)
        bidi_text = get_display(reshaped)
        return bidi_text
    return text


def format_ass_timestamp(seconds: float) -> str:
    """Format seconds into ASS timestamp format: H:MM:SS.cc (centiseconds)."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def format_srt_timestamp(seconds: float) -> str:
    """Format seconds into SRT timestamp format: HH:MM:SS,mmm (milliseconds)."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


class SubtitleEngine:
    """Generates styled SRT and ASS subtitle files and FFmpeg subtitle filter chains."""

    def __init__(self, default_style: Optional[SubtitleStyle] = None) -> None:
        font_name = find_system_arabic_font()
        self.style = default_style or SubtitleStyle(font_name=font_name)

    def generate_srt(self, items: List[SubtitleItem], output_path: Path) -> Path:
        """Generate a standard SRT file from subtitle items."""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        lines: List[str] = []
        for i, item in enumerate(items, 1):
            start = format_srt_timestamp(item.start_sec)
            end = format_srt_timestamp(item.end_sec)
            lines.append(f"{i}\n{start} --> {end}\n{item.text}\n")

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        logger.debug(f"Generated SRT subtitles ({len(items)} entries) at '{output_path}'.")
        return output_path

    def generate_ass(
        self,
        items: List[SubtitleItem],
        output_path: Path,
        style: Optional[SubtitleStyle] = None,
        video_width: int = 1080,
        video_height: int = 1920,
        play_res_x: Optional[int] = None,
        play_res_y: Optional[int] = None,
    ) -> Path:
        """Generate an Advanced SubStation Alpha (.ass) subtitle file.
        
        Note: Text is passed in clean Unicode so libass handles Arabic shaping and RTL
        without double-shaping artifacts.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        st = style or self.style

        res_x = play_res_x or video_width
        res_y = play_res_y or video_height

        ass_content = [
            "[Script Info]",
            "Title: Universal AI Studio Subtitles",
            "ScriptType: v4.00+",
            f"PlayResX: {res_x}",
            f"PlayResY: {res_y}",
            "WrapStyle: 0",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
            "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            f"Style: Default,{st.font_name},{st.font_size},{st.primary_color},{st.secondary_color},"
            f"{st.outline_color},{st.back_color},{-1 if st.bold else 0},{-1 if st.italic else 0},0,0,100,100,0,0,"
            f"{st.border_style},{st.outline_width:.1f},{st.shadow_depth:.1f},{st.alignment},"
            f"{st.margin_l},{st.margin_r},{st.margin_v},1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]

        for item in items:
            start_t = format_ass_timestamp(item.start_sec)
            end_t = format_ass_timestamp(item.end_sec)
            # Clean text directly (libass handles HarfBuzz RTL)
            # Replace newlines with ASS \N
            clean_text = item.text.replace("\n", "\\N").strip()
            
            # If word timing is provided, support karaoke highlight
            if item.words:
                k_text_parts = []
                for w in item.words:
                    dur_cs = int(round((w.end_sec - w.start_sec) * 100))
                    k_text_parts.append(f"{{\\k{dur_cs}}}{w.word}")
                dialogue_text = " ".join(k_text_parts)
            else:
                dialogue_text = clean_text

            ass_content.append(
                f"Dialogue: 0,{start_t},{end_t},Default,,0,0,0,,{dialogue_text}"
            )

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(ass_content) + "\n")

        logger.debug(f"Generated ASS subtitles ({len(items)} events) at '{output_path}'.")
        return output_path

    def build_subtitle_filter(self, ass_or_srt_path: Path) -> str:
        """Construct FFmpeg filter string for burning subtitles into video.
        
        Escapes paths correctly for Windows and Unix.
        """
        p = Path(ass_or_srt_path).resolve()
        # Escape path for FFmpeg filter argument
        escaped = str(p).replace("\\", "/").replace(":", "\\:")
        if p.suffix.lower() == ".ass":
            return f"ass='{escaped}'"
        else:
            return f"subtitles='{escaped}'"
