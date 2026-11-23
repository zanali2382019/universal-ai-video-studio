"""Arabic & multilingual thumbnail generator with typography and brand styling (Task 3.16).

Implements Guardrail C:
- Reliable Arabic text shaping via arabic_reshaper and python-bidi for Pillow.
- Word-level multi-line wrapping preserving RTL semantic order.
- Cross-platform font discovery across Windows and Linux CI (no hardcoded fixed paths).
- High-contrast typography with stroke/outline, pill badges, and channel branding.
"""
import os
import re
import sys
from pathlib import Path
from typing import Optional, List, Tuple, Union, Any
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from src.core.logger import get_logger

logger = get_logger("studio.export.thumbnail")


def find_system_font_path(preferred_name: Optional[str] = None) -> Optional[Path]:
    """Discover a valid TrueType/OpenType font on the current operating system.

    Supports Windows and Linux CI without assuming hardcoded paths.
    """
    candidates = []

    if sys.platform == "win32":
        win_fonts = Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts"
        if preferred_name:
            candidates.append(win_fonts / f"{preferred_name}.ttf")
            candidates.append(win_fonts / f"{preferred_name}.otf")
        candidates.extend([
            win_fonts / "segoeuib.ttf",   # Segoe UI Bold
            win_fonts / "arialbd.ttf",    # Arial Bold
            win_fonts / "tahomabd.ttf",   # Tahoma Bold
            win_fonts / "segoeui.ttf",    # Segoe UI Regular
            win_fonts / "arial.ttf",      # Arial Regular
            win_fonts / "tahoma.ttf",     # Tahoma Regular
        ])
    else:
        # Linux / Ubuntu CI paths
        linux_base = [
            Path("/usr/share/fonts"),
            Path("/usr/local/share/fonts"),
            Path("~/.fonts").expanduser(),
        ]
        candidates.extend([
            Path("/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"),
            Path("/usr/share/fonts/truetype/freefont/FreeSans.ttf"),
        ])
        for base in linux_base:
            if base.exists():
                candidates.extend(list(base.glob("**/*Arabic*.ttf")))
                candidates.extend(list(base.glob("**/*DejaVu*.ttf")))

    for cand in candidates:
        if cand.exists() and cand.is_file():
            return cand

    return None


def get_pillow_font(size: int = 48, preferred_name: Optional[str] = None) -> ImageFont.ImageFont:
    """Load an ImageFont instance, falling back to default if no font file is found."""
    font_path = find_system_font_path(preferred_name)
    if font_path:
        try:
            return ImageFont.truetype(str(font_path), size=size)
        except Exception as e:
            logger.warning(f"Failed to load TrueType font '{font_path}': {e}")

    try:
        return ImageFont.load_default()
    except Exception:
        return ImageFont.load_default()


def contains_arabic(text: str) -> bool:
    """Check if string contains Arabic script Unicode characters."""
    if not text:
        return False
    return bool(re.search(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]", text))


def shape_line_for_pillow(text: str) -> str:
    """Shape and reorder an individual text line for Pillow LTR canvas.

    Applies Arabic reshaping and FriBidi layout algorithm when Arabic characters are present.
    """
    if not text:
        return ""
    if contains_arabic(text):
        import arabic_reshaper
        from bidi.algorithm import get_display
        reshaped = arabic_reshaper.reshape(text)
        return get_display(reshaped)
    return text


def wrap_text_for_pillow(
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    draw: ImageDraw.ImageDraw,
) -> List[str]:
    """Wrap text into lines fitting within max_width, preserving logical word order before shaping."""
    if not text:
        return []

    words = text.split()
    if not words:
        return []

    lines: List[str] = []
    current_words: List[str] = []

    for word in words:
        test_words = current_words + [word]
        test_line = " ".join(test_words)
        # Shape test line to measure actual pixel width
        shaped_test = shape_line_for_pillow(test_line)
        bbox = draw.textbbox((0, 0), shaped_test, font=font)
        line_w = bbox[2] - bbox[0]

        if line_w <= max_width or not current_words:
            current_words.append(word)
        else:
            lines.append(" ".join(current_words))
            current_words = [word]

    if current_words:
        lines.append(" ".join(current_words))

    # Return lines shaped for display
    return [shape_line_for_pillow(l) for l in lines]


class ArabicThumbnailGenerator:
    """Generates high-impact, brand-aligned video thumbnails with Arabic typography."""

    def __init__(
        self,
        default_width: int = 1080,
        default_height: int = 1920,
    ) -> None:
        self.default_width = default_width
        self.default_height = default_height

    def generate_thumbnail(
        self,
        output_path: Union[str, Path],
        title: str,
        subtitle: Optional[str] = None,
        badge_text: Optional[str] = None,
        base_image_path: Optional[Union[str, Path]] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        primary_color: str = "#FFFFFF",
        accent_color: str = "#FACC15",  # Brand Yellow
        bg_color_top: Tuple[int, int, int] = (15, 23, 42),      # Dark Navy
        bg_color_bottom: Tuple[int, int, int] = (30, 27, 75),  # Deep Indigo
        watermark_path: Optional[Union[str, Path]] = None,
    ) -> Path:
        """Create and save a thumbnail image.

        Args:
            output_path: Destination path on disk.
            title: Main headline (Arabic, English, or mixed).
            subtitle: Optional secondary subtitle.
            badge_text: Optional pill badge text (e.g. 'حلقة 03' or 'SHORTS').
            base_image_path: Optional background image to composite on.
            width: Target width (defaults to 1080).
            height: Target height (defaults to 1920).
            ...

        Returns:
            Path of saved thumbnail file.
        """
        w = width or self.default_width
        h = height or self.default_height
        target_path = Path(output_path).resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # 1. Base Canvas Preparation
        if base_image_path and Path(base_image_path).exists():
            try:
                base_img = Image.open(base_image_path).convert("RGBA")
                # Scale & crop to cover target dimensions
                canvas = self._resize_and_crop(base_img, w, h)
                # Apply slight dark gradient vignette for text legibility
                overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                overlay_draw = ImageDraw.Draw(overlay)
                for y in range(h):
                    alpha = int(140 * (y / h))  # Darken toward bottom
                    overlay_draw.line([(0, y), (w, y)], fill=(0, 0, 0, alpha))
                canvas = Image.alpha_composite(canvas, overlay)
            except Exception as e:
                logger.warning(f"Could not load base image '{base_image_path}': {e}. Creating gradient background.")
                canvas = self._create_gradient(w, h, bg_color_top, bg_color_bottom)
        else:
            canvas = self._create_gradient(w, h, bg_color_top, bg_color_bottom)

        draw = ImageDraw.Draw(canvas)

        # 2. Typography Setup
        title_font_size = int(h * 0.045)  # Responsive title size (~86px for 1920h)
        sub_font_size = int(h * 0.026)    # ~50px
        badge_font_size = int(h * 0.022)  # ~42px

        title_font = get_pillow_font(title_font_size)
        sub_font = get_pillow_font(sub_font_size)
        badge_font = get_pillow_font(badge_font_size)

        margin_x = int(w * 0.08)
        content_max_width = w - (2 * margin_x)

        # 3. Draw Badge (if provided)
        current_y = int(h * 0.12)  # Position near upper third for vertical
        if badge_text:
            shaped_badge = shape_line_for_pillow(badge_text)
            b_box = draw.textbbox((0, 0), shaped_badge, font=badge_font)
            bw = b_box[2] - b_box[0]
            bh = b_box[3] - b_box[1]

            pad_x = 24
            pad_y = 12
            badge_rect = [
                margin_x,
                current_y,
                margin_x + bw + (2 * pad_x),
                current_y + bh + (2 * pad_y),
            ]
            # Draw rounded pill badge
            draw.rounded_rectangle(badge_rect, radius=12, fill=accent_color)
            # Draw badge text in dark color
            draw.text(
                (margin_x + pad_x, current_y + pad_y - b_box[1]),
                shaped_badge,
                font=badge_font,
                fill="#0F172A",
            )
            current_y += bh + (2 * pad_y) + 30

        # 4. Wrap & Draw Title
        title_lines = wrap_text_for_pillow(title, title_font, content_max_width, draw)
        stroke_width = max(2, int(title_font_size * 0.06))

        for line in title_lines:
            bbox = draw.textbbox((0, 0), line, font=title_font)
            lw = bbox[2] - bbox[0]
            lh = bbox[3] - bbox[1]

            # Right-aligned for Arabic, Left-aligned for LTR
            if contains_arabic(title):
                x = (w - margin_x) - lw
            else:
                x = margin_x

            # Draw text with dark outline for maximum legibility
            draw.text(
                (x, current_y - bbox[1]),
                line,
                font=title_font,
                fill=primary_color,
                stroke_width=stroke_width,
                stroke_fill="#000000",
            )
            current_y += lh + int(title_font_size * 0.4)

        # 5. Draw Subtitle
        if subtitle:
            current_y += 15
            sub_lines = wrap_text_for_pillow(subtitle, sub_font, content_max_width, draw)
            sub_stroke = max(1, int(sub_font_size * 0.05))
            for s_line in sub_lines:
                s_box = draw.textbbox((0, 0), s_line, font=sub_font)
                slw = s_box[2] - s_box[0]
                slh = s_box[3] - s_box[1]

                if contains_arabic(subtitle):
                    sx = (w - margin_x) - slw
                else:
                    sx = margin_x

                draw.text(
                    (sx, current_y - s_box[1]),
                    s_line,
                    font=sub_font,
                    fill="#E2E8F0",  # Slate 200
                    stroke_width=sub_stroke,
                    stroke_fill="#000000",
                )
                current_y += slh + int(sub_font_size * 0.3)

        # 6. Watermark Overlay (optional)
        if watermark_path and Path(watermark_path).exists():
            try:
                wm = Image.open(watermark_path).convert("RGBA")
                wm_max_w = int(w * 0.18)
                wm.thumbnail((wm_max_w, int(h * 0.1)), Image.Resampling.LANCZOS)
                canvas.paste(wm, (w - wm.width - margin_x, int(h * 0.05)), wm)
            except Exception as e:
                logger.debug(f"Watermark paste notice: {e}")

        # 7. Convert & Save
        final_rgb = canvas.convert("RGB")
        final_rgb.save(target_path, quality=95, optimize=True)
        logger.info(f"Generated Arabic thumbnail saved to '{target_path}' ({w}x{h}).")
        return target_path

    def render_thumbnail(
        self,
        output_path: Union[str, Path],
        headline_ar: str,
        subtitle_ar: Optional[str] = None,
        badge_text: Optional[str] = None,
        background_image_path: Optional[Union[str, Path]] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        **kwargs: Any,
    ) -> Path:
        """Alias for generate_thumbnail matching the verification interface."""
        return self.generate_thumbnail(
            output_path=output_path,
            title=headline_ar,
            subtitle=subtitle_ar,
            badge_text=badge_text,
            base_image_path=background_image_path,
            width=width,
            height=height,
            **kwargs,
        )

    def _resize_and_crop(self, img: Image.Image, target_w: int, target_h: int) -> Image.Image:
        """Resize image preserving aspect ratio and center crop to target dimensions."""
        img_w, img_h = img.size
        target_aspect = target_w / target_h
        img_aspect = img_w / img_h

        if img_aspect > target_aspect:
            # Image is wider: fit height and crop width
            new_h = target_h
            new_w = int(new_h * img_aspect)
        else:
            # Image is taller: fit width and crop height
            new_w = target_w
            new_h = int(new_w / img_aspect)

        resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        left = (new_w - target_w) // 2
        top = (new_h - target_h) // 2
        return resized.crop((left, top, left + target_w, top + target_h))

    def _create_gradient(
        self,
        width: int,
        height: int,
        color1: Tuple[int, int, int],
        color2: Tuple[int, int, int],
    ) -> Image.Image:
        """Create a vertical gradient image."""
        base = Image.new("RGBA", (width, height), (0, 0, 0, 255))
        draw = ImageDraw.Draw(base)
        r1, g1, b1 = color1
        r2, g2, b2 = color2

        for y in range(height):
            ratio = y / max(1, height - 1)
            r = int(r1 + (r2 - r1) * ratio)
            g = int(g1 + (g2 - g1) * ratio)
            b = int(b1 + (b2 - b1) * ratio)
            draw.line([(0, y), (width, y)], fill=(r, g, b, 255))

        return base
