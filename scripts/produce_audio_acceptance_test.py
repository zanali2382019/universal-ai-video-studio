"""Isolated Audio Acceptance Test Runner for Task 3.7.

Generates an isolated 16-second audiovisual sample:
1. Synthesizes clear, natural Arabic narration using Microsoft Naayf ('ar').
2. Generates genuine acoustic background music (soft piano/harp, warm_calm, gentle_cinematic).
3. Mixes narration (vol=1.0) and music (vol=0.15) with no sound effects.
4. Validates final output using FFprobe and EBU R128 True Peak measurement (<= -1.0 dBTP).
5. Exports to: channels/ch_dreammatter_001/exports/tests/audio_acceptance_test_ar.mp4.
"""
import sys
import os
import json
from pathlib import Path

# Add project root to path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from src.core.config import get_settings
from src.core.constants import (
    MusicStyle,
    MusicMood,
    VoiceoverLanguage,
)
from src.rendering.ffmpeg_wrapper import FFmpegWrapper
from src.rendering.probe_service import MediaProbeService
from src.rendering.music_generator import BackgroundMusicGenerator
from src.services.voice_service import VoiceSynthesisService
from src.rendering.timeline_renderer import TimelineRenderer, SceneRenderItem
from src.rendering.profiles import VERTICAL_9_16
from src.utils.file_utils import calculate_file_sha256


def run_audio_acceptance_test() -> dict:
    settings = get_settings()
    channel_id = "ch_dreammatter_001"
    channel_dir = settings.channels_root / channel_id
    
    test_dir = channel_dir / "exports" / "tests"
    test_dir.mkdir(parents=True, exist_ok=True)

    cache_dir = channel_dir / "media_vault" / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    wrapper = FFmpegWrapper()
    probe_service = MediaProbeService(ffmpeg_wrapper=wrapper)
    voice_service = VoiceSynthesisService(ffmpeg_wrapper=wrapper)
    music_gen = BackgroundMusicGenerator(ffmpeg_wrapper=wrapper)
    renderer = TimelineRenderer(ffmpeg_wrapper=wrapper, probe_service=probe_service)

    print("=" * 80)
    print("TASK 3.7: ISOLATED AUDIO ACCEPTANCE TEST (ARABIC + GENTLE ACOUSTIC MUSIC)")
    print("=" * 80)

    # 1. Arabic Narration
    arabic_narration_text = (
        "في ليلة هادئة، نظر تومي الصغير من نافذة غرفته، "
        "فشاهد نجمة ذهبية تبتسم له وتملأ قلبه بالدفء. "
        "عالم دريم ماتر، حيث تبدأ أجمل الأحلام."
    )
    vo_path = cache_dir / "test_ar_narration.aac"
    print(f"[1/4] Synthesizing Arabic narration ('ar') via VoiceSynthesisService...")
    vo_meta = voice_service.synthesize_voiceover(
        text=arabic_narration_text,
        language="ar",
        output_path=vo_path,
    )
    vo_probe = probe_service.probe_file(vo_path)
    vo_dur = vo_probe.duration_sec
    print(f"      - Narration synthesized: {vo_dur:.2f}s, format={vo_probe.format_name}, sample_rate={vo_probe.primary_audio_stream.sample_rate}Hz")

    # Target test duration (approx 16s, leaving 2-3s musical tail for graceful fade)
    target_dur = max(16.0, vo_dur + 2.5)

    # 2. Genuine Acoustic Background Music (Soft Piano / Celtic Harp)
    bg_music_path = cache_dir / "test_soft_piano_harp_music.aac"
    print(f"[2/4] Generating soft acoustic background music (soft piano/harp, duration={target_dur:.2f}s)...")
    music_gen.generate_music(
        output_path=bg_music_path,
        duration_sec=target_dur,
        style=MusicStyle.GENTLE_CINEMATIC,
        mood=MusicMood.WARM_CALM,
    )
    music_probe = probe_service.probe_file(bg_music_path)
    music_tp = probe_service.measure_true_peak(bg_music_path)
    print(f"      - Music track generated: {music_probe.duration_sec:.2f}s, True Peak={music_tp.true_peak_dbtp:.2f} dBTP, LUFS={music_tp.integrated_loudness_lufs:.2f}")

    # 3. Audiovisual sample assembly via existing TimelineRenderer
    # Use real Tommy night window image
    img_path = channel_dir / "media_vault" / "originals" / "images" / "tommy_ep2_scene_1.jpg"
    if not img_path.exists():
        raise FileNotFoundError(f"Source image missing: {img_path}")

    output_video_path = test_dir / "audio_acceptance_test_ar.mp4"
    print(f"[3/4] Mixing narration (vol=1.0) and music (vol=0.15) onto visual timeline ({target_dur:.2f}s)...")

    scenes = [
        SceneRenderItem(
            scene_id="sc_audio_test_01",
            asset_path=img_path,
            duration_sec=target_dur,
            transition="cut",
            transition_duration_sec=0.0,
        )
    ]

    final_mp4 = renderer.render_timeline(
        scenes=scenes,
        output_path=output_video_path,
        profile=VERTICAL_9_16,
        voiceover_track_path=vo_path,
        background_music_path=bg_music_path,
        voiceover_volume=1.0,
        background_music_volume=0.15,
        enforce_true_peak=True,
    )

    # 4. Final Technical & Acoustic Validation
    print(f"[4/4] Validating final audiovisual sample via FFprobe and EBU R128 True Peak...")
    final_probe = probe_service.probe_file(final_mp4)
    final_tp = probe_service.measure_true_peak(final_mp4)
    file_size_bytes = final_mp4.stat().st_size
    file_sha256 = calculate_file_sha256(final_mp4)

    v = final_probe.primary_video_stream
    a = final_probe.primary_audio_stream

    result = {
        "output_path": str(final_mp4.resolve()),
        "file_size_bytes": file_size_bytes,
        "file_sha256": file_sha256,
        "duration_sec": final_probe.duration_sec,
        "language": "ar",
        "narration_text": arabic_narration_text,
        "music_instrumentation": "Soft acoustic felt piano / harp arpeggios with exponential string decay",
        "music_style": "gentle_cinematic",
        "music_mood": "warm_calm",
        "sound_effects": "none",
        "video": {
            "codec": v.codec_name if v else None,
            "resolution": f"{v.width}x{v.height}" if v else None,
            "fps": v.fps if v else None,
        },
        "audio": {
            "codec": a.codec_name if a else None,
            "sample_rate": a.sample_rate if a else None,
            "channels": a.channels if a else None,
        },
        "true_peak": {
            "measured_dbtp": final_tp.true_peak_dbtp,
            "ceiling_dbtp": final_tp.max_allowed_dbtp,
            "integrated_lufs": final_tp.integrated_loudness_lufs,
            "is_compliant": final_tp.is_compliant,
        },
    }

    report_path = test_dir / "audio_acceptance_test_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 80)
    print("AUDIO ACCEPTANCE TEST REPORT")
    print("=" * 80)
    print(f"  • Final Output Path     : {final_mp4.resolve()}")
    print(f"  • File Size             : {file_size_bytes:,} bytes ({file_size_bytes / (1024*1024):.2f} MB)")
    print(f"  • SHA-256 Hash          : {file_sha256}")
    print(f"  • Duration              : {final_probe.duration_sec:.2f} seconds")
    print(f"  • Voiceover Language    : Arabic ('ar') [Microsoft Naayf]")
    print(f"  • Narration Text        : \"{arabic_narration_text}\"")
    print(f"  • Music Style & Mood    : gentle_cinematic (warm_calm) [Soft acoustic piano / harp]")
    print(f"  • Sound Effects         : None (isolated narration + music)")
    print(f"  • Video Specification   : {v.width}x{v.height} (9:16 vertical) @ {v.fps} FPS ({v.codec_name})")
    print(f"  • Audio Specification   : {a.codec_name} stereo, {a.sample_rate} Hz")
    print(f"  • Measured True Peak    : {final_tp.true_peak_dbtp:.2f} dBTP (Limit: <= -1.0 dBTP -> Compliant: {final_tp.is_compliant})")
    print(f"  • Integrated Loudness   : {final_tp.integrated_loudness_lufs:.2f} LUFS")
    print(f"  • JSON Report           : {report_path}")
    print("=" * 80 + "\n")

    return result


if __name__ == "__main__":
    run_audio_acceptance_test()

