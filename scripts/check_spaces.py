import urllib.request
import json

spaces = ["silma-ai/silma-tts-v1-demo", "NightPrince/Fasih-TTS-V1"]

for space in spaces:
    url = f"https://huggingface.co/api/spaces/{space}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode('utf-8'))
            print(f"Space: {space} -> Stage: {data.get('runtime', {}).get('stage')}, Hardware: {data.get('runtime', {}).get('hardware')}")
    except Exception as e:
        print(f"Space: {space} -> Error: {e}")

