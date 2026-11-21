import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from src.rendering.probe_service import MediaProbeService

benchmark_dir = Path("channels/ch_dreammatter_001/exports/tests/arabic_tts_benchmark")
probe = MediaProbeService()

sample_files = [
    benchmark_dir / "silma_f5_tts_msa_sample.mp3",
    benchmark_dir / "habibi_msa_specialized_sample.mp3",
    benchmark_dir / "coqui_xtts_v2_msa_sample.mp3",
    benchmark_dir / "omnivoice_msa_sample.mp3"
]

results = {}
for f in sample_files:
    if f.exists():
        sha256 = hashlib.sha256(f.read_bytes()).hexdigest().upper()
        p_info = probe.probe_file(f)
        results[f.name] = {
            "exists": True,
            "size_bytes": f.stat().st_size,
            "duration_sec": p_info.duration_sec,
            "format": p_info.format_name,
            "sha256": sha256
        }

print(json.dumps(results, indent=2))

