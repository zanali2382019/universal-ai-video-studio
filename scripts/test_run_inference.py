import httpx
import json
from pathlib import Path

passage_path = Path("channels/ch_dreammatter_001/exports/tests/arabic_tts_benchmark/benchmark_passage.txt")
full_text = passage_path.read_text(encoding="utf-8").strip()

base_url = "https://silma-ai-silma-tts-v1-demo.hf.space/gradio_api"
upload_url = f"{base_url}/upload"
run_url = f"{base_url}/run/do_inference"

ref_path = Path("channels/ch_dreammatter_001/exports/tests/arabic_tts_benchmark/silma_official_bundled_ref.wav")
ref_text = "ويدقق النظر في القرآن الكريم وسائر الكتب السماوية ويتبع مسالك الرسل العظام عليهم الصلاة والسلام."

with httpx.Client(timeout=120.0) as client:
    with open(ref_path, "rb") as f:
        files = {"files": ("ar.ref.24k.wav", f, "audio/wav")}
        r_up = client.post(upload_url, files=files)
        uploaded_path = r_up.json()[0]
        print(f"Uploaded: {uploaded_path}", flush=True)

    file_obj = {
        "path": uploaded_path,
        "url": f"{base_url}/file={uploaded_path}",
        "orig_name": "ar.ref.24k.wav",
        "mime_type": "audio/wav",
        "meta": {"_type": "gradio.FileData"}
    }

    payload = {
        "data": [file_obj, ref_text, full_text]
    }

    print("Posting to /run/do_inference...", flush=True)
    r_run = client.post(run_url, json=payload)
    print("Run status:", r_run.status_code)
    print("Run body:", r_run.text[:500])

