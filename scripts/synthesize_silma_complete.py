import httpx
import json
import time
from pathlib import Path

# Load benchmark passage
passage_path = Path("channels/ch_dreammatter_001/exports/tests/arabic_tts_benchmark/benchmark_passage.txt")
full_text = passage_path.read_text(encoding="utf-8").strip()

base_url = "https://silma-ai-silma-tts-v1-demo.hf.space/gradio_api"
upload_url = f"{base_url}/upload"
call_url = f"{base_url}/call/do_inference"

ref_path = Path("channels/ch_dreammatter_001/exports/tests/arabic_tts_benchmark/silma_official_bundled_ref.wav")
ref_text = "ويدقق النظر في القرآن الكريم وسائر الكتب السماوية ويتبع مسالك الرسل العظام عليهم الصلاة والسلام."

print(f"Full text length: {len(full_text)} chars, {len(full_text.split())} words")
print("Uploading bundled reference audio...", flush=True)

with httpx.Client(timeout=120.0) as client:
    with open(ref_path, "rb") as f:
        files = {"files": ("ar.ref.24k.wav", f, "audio/wav")}
        r_up = client.post(upload_url, files=files)
        uploaded_path = r_up.json()[0]
        print(f"Uploaded: {uploaded_path}", flush=True)

    # In Gradio 4, FileData object:
    file_obj = {
        "path": uploaded_path,
        "orig_name": "ar.ref.24k.wav",
        "mime_type": "audio/wav",
        "meta": {"_type": "gradio.FileData"}
    }

    payload = {
        "data": [file_obj, ref_text, full_text]
    }

    print("Sending synthesis request to SILMA TTS...", flush=True)
    r_call = client.post(call_url, json=payload)
    print("Call status:", r_call.status_code, "Body:", r_call.text, flush=True)
    
    if r_call.status_code == 200:
        event_id = r_call.json().get("event_id")
        sse_url = f"{call_url}/{event_id}"
        print(f"Connecting to event stream: {sse_url}", flush=True)
        with client.stream("GET", sse_url, timeout=300.0) as stream:
            for line in stream.iter_lines():
                if line:
                    print("Stream line:", line, flush=True)
                    if line.startswith("data:"):
                        data_str = line[5:].strip()
                        try:
                            data_json = json.loads(data_str)
                            print("Parsed data:", data_json, flush=True)
                            # If audio url is returned
                            if isinstance(data_json, list) and len(data_json) > 0:
                                res_file = data_json[0]
                                if isinstance(res_file, dict) and "url" in res_file:
                                    audio_url = res_file["url"]
                                    print("AUDIO URL:", audio_url, flush=True)
                                    # Download generated audio
                                    r_audio = client.get(audio_url)
                                    out_wav = Path("channels/ch_dreammatter_001/exports/tests/arabic_tts_benchmark/silma_complete_passage.wav")
                                    out_wav.write_bytes(r_audio.content)
                                    print(f"Downloaded complete SILMA wav: {out_wav} ({out_wav.stat().st_size} bytes)", flush=True)
                        except Exception as parse_e:
                            print("Parse error:", parse_e, flush=True)

