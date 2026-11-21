"""Visual transitions and aspect ratio fitting engine for FFmpeg (Task 3.14).

Provides:
- Aspect ratio normalization filters: cover, contain, blur_pad, and fit/stretch
- FFmpeg xfade transition graph builder supporting:
  fade, wipeleft, wiperight, slideup, slidedown, circleopen, smoothleft, crossfade
- Uniform geometry, timebase, and framerate enforcement before transitions
"""

from typing import List, Optional, Any, Tuple
from src.rendering.profiles import RenderProfile

SUPPORTED_TRANSITIONS = {
    "cut",
    "fade",
    "crossfade",
    "dissolve",
    "wipeleft",
    "wiperight",
    "slideup",
    "slidedown",
    "circleopen",
    "smoothleft",
}

SUPPORTED_ASPECT_MODES = {
    "cover",
    "contain",
    "blur_pad",
    "fit",
    "stretch",
}


def build_aspect_fit_filter(
    profile: Optional[Any] = None,
    mode: str = "contain",
    target_width: Optional[int] = None,
    target_height: Optional[int] = None,
    fps: Optional[int] = None,
    pix_fmt: Optional[str] = None,
) -> str:
    """Generate FFmpeg video filter for the given aspect ratio mode and target profile.
    
    Args:
        profile: Target RenderProfile or TimelineRenderProfile (optional).
        mode: Fitting mode ('cover', 'contain', 'blur_pad', or 'fit'/'stretch').
        target_width: Explicit target width override.
        target_height: Explicit target height override.
        fps: Explicit target fps override.
        pix_fmt: Explicit pixel format override.
        
    Returns:
        str: FFmpeg filter string.
    """
    clean_mode = (mode or "contain").strip().lower()
    w = target_width or (getattr(profile, "width", 1080) if profile else 1080)
    h = target_height or (getattr(profile, "height", 1920) if profile else 1920)
    target_fps = fps or (getattr(profile, "fps", 30) if profile else 30)
    target_pix_fmt = pix_fmt or (getattr(profile, "pix_fmt", "yuv420p") if profile else "yuv420p")

    if clean_mode == "cover":
        # Scale to fill completely, then center crop excess
        return (
            f"scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},"
            f"setsar=1,fps={target_fps},format={target_pix_fmt}"
        )
    elif clean_mode == "blur_pad":
        # Scale foreground to fit inside, background scaled to fill + blurred
        return (
            f"split[fg][bg];"
            f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=25:25[bg_blur];"
            f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease[fg_scaled];"
            f"[bg_blur][fg_scaled]overlay=(W-w)/2:(H-h)/2,"
            f"setsar=1,fps={target_fps},format={target_pix_fmt}"
        )
    elif clean_mode in ("fit", "stretch"):
        # Direct scale without aspect ratio preservation
        return f"scale={w}:{h},setsar=1,fps={target_fps},format={target_pix_fmt}"
    else:
        # Default: 'contain' (scale decrease and pad with black)
        return (
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1,fps={target_fps},format={target_pix_fmt}"
        )


class XfadeFilterResult(tuple):
    """Tuple of (filter_complex, final_map) that can also be used directly as str."""
    def __new__(cls, filter_complex: str, final_map: str):
        return super().__new__(cls, (filter_complex, final_map))

    def __str__(self) -> str:
        return self[0]

    @property
    def filter_complex(self) -> str:
        return self[0]

    @property
    def final_map(self) -> str:
        return self[1]


def build_xfade_filter_complex(
    inputs_count: Optional[int] = None,
    durations: Optional[List[float]] = None,
    transitions: Optional[Any] = None,
    transition_durations: Optional[List[float]] = None,
    num_inputs: Optional[int] = None,
) -> XfadeFilterResult:
    """Build a multi-scene xfade filter_complex string with accurate time offsets.
    
    Accepts either:
    - (inputs_count, durations, transitions=[(name, dur), ...])
    - (num_inputs, durations, transitions=[name, ...], transition_durations=[dur, ...])
        
    Returns:
        XfadeFilterResult: (filter_complex_str, final_map_label)
    """
    n = inputs_count or num_inputs or (len(durations) if durations else 0)
    dur_list = durations or []
    if n < 2 or len(dur_list) < 2:
        return XfadeFilterResult("", "0:v")

    t_names: List[str] = []
    t_durs: List[float] = []

    if transitions and isinstance(transitions[0], (tuple, list)):
        for item in transitions:
            t_names.append(str(item[0]))
            t_durs.append(float(item[1]) if len(item) > 1 else 0.5)
    else:
        t_names = list(transitions or [])
        t_durs = list(transition_durations or [])

    filter_parts: List[str] = []
    current_v = "0:v"
    accumulated_time = dur_list[0]

    for i in range(n - 1):
        next_v = f"{i + 1}:v"
        out_v = f"v{i + 1}" if i < n - 2 else "outv"
        
        raw_t = (t_names[i] if i < len(t_names) else "fade").lower()
        if raw_t in ("crossfade", "dissolve"):
            t_type = "fade"
        elif raw_t in SUPPORTED_TRANSITIONS and raw_t != "cut":
            t_type = raw_t
        else:
            t_type = "fade"

        raw_dur = t_durs[i] if i < len(t_durs) else 0.5
        # Cap transition duration so it cannot exceed 40% of either adjacent clip
        t_dur = min(raw_dur, dur_list[i] * 0.4, dur_list[i + 1] * 0.4)
        if t_dur < 0.1:
            t_dur = 0.1

        offset = max(0.0, accumulated_time - t_dur)

        filter_parts.append(
            f"[{current_v}][{next_v}]xfade=transition={t_type}:duration={t_dur:.3f}:offset={offset:.3f}[{out_v}]"
        )
        current_v = out_v
        accumulated_time = offset + dur_list[i + 1]

    filter_str = ";".join(filter_parts)
    final_label = f"[{current_v}]"
    return XfadeFilterResult(filter_str, final_label)
