import urllib.request
import json
import time

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

req = urllib.request.Request(
    call_url,
    data=json.dumps(payload).encode('utf-8'),
    headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'}
)

try:
    with urllib.request.urlopen(req, timeout=15) as r:
        res = json.loads(r.read().decode('utf-8'))
        print("Call response:", res)
        event_id = res.get("event_id")

    if event_id:
        get_url = f"{call_url}/{event_id}"
        print(f"Waiting for event {event_id} at {get_url}...")
        for attempt in range(20):
            time.sleep(2)
            req_get = urllib.request.Request(get_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req_get, timeout=30) as r_get:
                lines = r_get.read().decode('utf-8').strip().split('\n')
                print(f"Attempt {attempt+1}: {len(lines)} lines")
                for line in lines:
                    if line.startswith("data:"):
                        print("  Data:", line[:200])
                        if "complete" in line or ".wav" in line:
                            break
except Exception as e:
    print("Error:", e)

