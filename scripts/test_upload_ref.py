import httpx
from pathlib import Path
import json

upload_url = "https://silma-ai-silma-tts-v1-demo.hf.space/gradio_api/upload"
ref_path = Path("channels/ch_dreammatter_001/exports/tests/arabic_tts_benchmark/silma_official_bundled_ref.wav")

with httpx.Client(timeout=30.0) as client:
    with open(ref_path, "rb") as f:
        files = {"files": ("ar.ref.24k.wav", f, "audio/wav")}
        res = client.post(upload_url, files=files)
        print("Upload status:", res.status_code)
        print("Upload response:", res.text)

