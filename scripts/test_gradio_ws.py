import asyncio
import json
import uuid
from pathlib import Path
import websockets
import httpx

passage_path = Path("channels/ch_dreammatter_001/exports/tests/arabic_tts_benchmark/benchmark_passage.txt")
full_text = passage_path.read_text(encoding="utf-8").strip()

base_url = "https://silma-ai-silma-tts-v1-demo.hf.space/gradio_api"
upload_url = f"{base_url}/upload"
ws_url = "wss://silma-ai-silma-tts-v1-demo.hf.space/gradio_api/queue/join"

ref_path = Path("channels/ch_dreammatter_001/exports/tests/arabic_tts_benchmark/silma_official_bundled_ref.wav")
ref_text = "ويدقق النظر في القرآن الكريم وسائر الكتب السماوية ويتبع مسالك الرسل العظام عليهم الصلاة والسلام."

async def run_ws():
    session_hash = str(uuid.uuid4())[:8]
    print(f"Session hash: {session_hash}")
    
    # 1. Upload file
    with httpx.Client(timeout=30.0) as client:
        with open(ref_path, "rb") as f:
            r = client.post(upload_url, files={"files": ("ar.ref.24k.wav", f, "audio/wav")})
            up_path = r.json()[0]
            print(f"Uploaded: {up_path}")
    
    file_obj = {
        "path": up_path,
        "url": f"{base_url}/file={up_path}",
        "orig_name": "ar.ref.24k.wav",
        "mime_type": "audio/wav",
        "meta": {"_type": "gradio.FileData"}
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Origin": "https://silma-ai-silma-tts-v1-demo.hf.space"
    }
    
    async with websockets.connect(ws_url, additional_headers=headers) as ws:
        async for msg in ws:
            data = json.loads(msg)
            msg_type = data.get("msg")
            print(f"WS Recv: {msg_type} -> {str(data)[:150]}")
            
            if msg_type == "send_hash":
                await ws.send(json.dumps({
                    "fn_index": 1,
                    "session_hash": session_hash
                }))
            elif msg_type == "send_data":
                await ws.send(json.dumps({
                    "fn_index": 1,
                    "session_hash": session_hash,
                    "data": [file_obj, ref_text, full_text]
                }))
            elif msg_type == "process_completed":
                print("SUCCESS! Output data:")
                out = data.get("output", {}).get("data")
                print(out)
                if out and len(out) > 0 and isinstance(out[0], dict) and "url" in out[0]:
                    audio_url = out[0]["url"]
                    print(f"Downloading from {audio_url}...")
                    with httpx.Client(timeout=60.0) as client:
                        r_audio = client.get(audio_url)
                        out_wav = Path("channels/ch_dreammatter_001/exports/tests/arabic_tts_benchmark/silma_complete_passage.wav")
                        out_wav.write_bytes(r_audio.content)
                        print(f"Saved: {out_wav} ({out_wav.stat().st_size} bytes)")
                break

if __name__ == "__main__":
    asyncio.run(run_ws())
