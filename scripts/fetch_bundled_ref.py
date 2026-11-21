import urllib.request
from pathlib import Path

url = "https://huggingface.co/spaces/silma-ai/silma-tts-v1-demo/resolve/main/ar.ref.24k.wav"
out_path = Path("channels/ch_dreammatter_001/exports/tests/arabic_tts_benchmark/silma_official_bundled_ref.wav")

req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        with open(out_path, "wb") as f:
            f.write(r.read())
    print(f"Downloaded official bundled ref audio: {out_path} ({out_path.stat().st_size} bytes)")
except Exception as e:
    print("Error:", e)

