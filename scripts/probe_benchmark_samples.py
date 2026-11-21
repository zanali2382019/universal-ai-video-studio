import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
import json
from src.rendering.probe_service import MediaProbeService

probe = MediaProbeService()
benchmark_dir = Path("channels/ch_dreammatter_001/exports/tests/arabic_tts_benchmark")

results = {}
for mp3 in sorted(benchmark_dir.glob("*.mp3")):
    info = probe.probe_file(mp3)
    peak = probe.measure_true_peak(mp3)
    audio = info.primary_audio_stream
    results[mp3.name] = {
        "duration_sec": round(info.duration_sec, 2),
        "format": info.format_name,
        "sample_rate": audio.sample_rate if audio else None,
        "channels": audio.channels if audio else None,
        "bit_rate_kbps": round(audio.bit_rate / 1000, 1) if audio and audio.bit_rate else None,
        "measured_true_peak_dbtp": peak.true_peak_dbtp,
        "integrated_loudness_lufs": peak.integrated_loudness_lufs,
        "is_compliant": peak.is_compliant
    }

report_path = benchmark_dir / "benchmark_report.json"
with open(report_path, "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)

print("Probing completed. Results:")
print(json.dumps(results, indent=2))
