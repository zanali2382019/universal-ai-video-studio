import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from src.rendering.probe_service import MediaProbeService

benchmark_dir = Path("channels/ch_dreammatter_001/exports/tests/arabic_tts_benchmark")
probe = MediaProbeService()

ref_files = [
    benchmark_dir / "reference_ahmad_fakhouri_20s.mp3",
    benchmark_dir / "reference_ahmad_fakhouri_20s.wav"
]

results = {}
for f in ref_files:
    if f.exists():
        sha256 = hashlib.sha256(f.read_bytes()).hexdigest().upper()
        p_info = probe.probe_file(f)
        results[f.name] = {
            "exists": True,
            "size_bytes": f.stat().st_size,
            "duration_sec": p_info.duration_sec,
            "format": p_info.format_name,
            "sample_rate": p_info.primary_audio_stream.sample_rate if p_info.primary_audio_stream else None,
            "channels": p_info.primary_audio_stream.channels if p_info.primary_audio_stream else None,
            "bit_rate": p_info.primary_audio_stream.bit_rate if p_info.primary_audio_stream else None,
            "sha256": sha256
        }
    else:
        results[f.name] = {"exists": False}

print(json.dumps(results, indent=2))

