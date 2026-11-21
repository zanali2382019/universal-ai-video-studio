import httpx
import json
import sys

base_url = "https://silma-ai-silma-tts-v1-demo.hf.space/gradio_api"
call_url = f"{base_url}/call/do_inference"

ref_audio = {
    "path": "/tmp/gradio/1c413e071600003e02be6c55b30102d1660ebf375b6e48f3ae37a34faf27518e/ar.ref.24k.wav",
    "url": "https://silma-ai-silma-tts-v1-demo.hf.space/gradio_api/file=/tmp/gradio/1c413e071600003e02be6c55b30102d1660ebf375b6e48f3ae37a34faf27518e/ar.ref.24k.wav",
    "orig_name": "ar.ref.24k.wav",
    "meta": {"_type": "gradio.FileData"}
}
ref_text = "ويدقق النظر في القرآن الكريم وسائر الكتب السماوية ويتبع مسالك الرسل العظام عليهم الصلاة والسلام."
gen_text = "فِي لَيْلَةٍ شَاتِيَةٍ سَاكِنَةٍ، تَطَلَّعَ الطِّفْلُ الصَّغِيرُ مِنْ نَافِذَةِ حُجْرَتِهِ نَحْوَ ظُلْمَةِ الأُفُقِ الْبَعِيدِ."

payload = {
    "data": [ref_audio, ref_text, gen_text]
}

print("Sending request to SILMA...", flush=True)
with httpx.Client(timeout=60.0) as client:
    res = client.post(call_url, json=payload)
    print("Post status:", res.status_code, "Body:", res.text, flush=True)
    if res.status_code == 200:
        event_id = res.json().get("event_id")
        sse_url = f"{call_url}/{event_id}"
        print(f"Connecting to SSE stream: {sse_url}", flush=True)
        with client.stream("GET", sse_url, timeout=120.0) as stream:
            for line in stream.iter_lines():
                if line:
                    print("SSE:", line, flush=True)
                    if line.startswith("data:"):
                        data_part = line[5:].strip()
                        try:
                            parsed = json.loads(data_part)
                            print("Parsed data:", parsed, flush=True)
                        except:
                            pass

