import subprocess
import json
import re
from pathlib import Path
import sys
sys.path.insert(0, str(Path.cwd()))
from src.rendering.probe_service import MediaProbeService

ffmpeg_path = r"C:\Users\96777\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin\ffmpeg.exe"
wav_path = Path("channels/ch_dreammatter_001/exports/tests/arabic_tts_benchmark/reference_ahmad_fakhouri_20s.wav")

probe = MediaProbeService()
info = probe.probe_file(wav_path)
peak = probe.measure_true_peak(wav_path)

# Run astats
cmd_stats = [
    ffmpeg_path, "-i", str(wav_path),
    "-af", "astats=metadata=1:reset=1",
    "-f", "null", "-"
]
p = subprocess.run(cmd_stats, capture_output=True, text=True)
stderr = p.stderr

# Run silencedetect to analyze pauses and speech pacing
cmd_silence = [
    ffmpeg_path, "-i", str(wav_path),
    "-af", "silencedetect=noise=-30dB:d=0.25",
    "-f", "null", "-"
]
p_silence = subprocess.run(cmd_silence, capture_output=True, text=True)

# Parse stats
rms_match = re.search(r"RMS level dB:\s+([-\d\.]+)", stderr)
peak_match = re.search(r"Peak level dB:\s+([-\d\.]+)", stderr)
flat_factor = re.search(r"Flat factor:\s+([-\d\.]+)", stderr)
dynamic_range = re.search(r"Dynamic range:\s+([-\d\.]+)", stderr)

# Parse silences
silences = re.findall(r"silence_start:\s+([\d\.]+).*?silence_duration:\s+([\d\.]+)", p_silence.stderr, re.DOTALL)

print("--- ACOUSTIC ANALYSIS OF AHMAD FAKHOURI REFERENCE (00:00 - 00:20) ---")
print(f"Duration: {info.duration_sec:.2f} s")
print(f"Sample Rate: {info.primary_audio_stream.sample_rate} Hz")
print(f"Channels: {info.primary_audio_stream.channels}")
print(f"True Peak: {peak.true_peak_dbtp:.2f} dBTP")
print(f"Integrated Loudness: {peak.integrated_loudness_lufs:.2f} LUFS")
print(f"RMS Level: {rms_match.group(1) if rms_match else 'N/A'} dB")
print(f"Peak Level: {peak_match.group(1) if peak_match else 'N/A'} dB")
print(f"Dynamic Range: {dynamic_range.group(1) if dynamic_range else 'N/A'} dB")

print(f"\nDetected Natural Pauses (>250ms at -30dB): {len(silences)}")
total_silence_dur = 0.0
for idx, (s_start, s_dur) in enumerate(silences):
    dur = float(s_dur)
    start = float(s_start)
    total_silence_dur += dur
    print(f"  Pause {idx+1}: Start={start:.2f}s, Duration={dur:.2f}s")

active_speech_time = info.duration_sec - total_silence_dur
print(f"Active Speech Time: {active_speech_time:.2f}s ({(active_speech_time/info.duration_sec)*100:.1f}%)")
print(f"Pause Ratio: {(total_silence_dur/info.duration_sec)*100:.1f}%")
