import urllib.request
import json

repos = ["silma-ai/silma-tts", "SWivid/Habibi-TTS"]

for r in repos:
    url = f"https://huggingface.co/api/models/{r}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            print(f"=== {r} ===")
            print("Id:", data.get('id'))
            print("License:", data.get('cardData', {}).get('license') or data.get('tags'))
            siblings = [s.get('rfilename') for s in data.get('siblings', [])]
            print(f"Files ({len(siblings)}):", siblings[:15])
    except Exception as e:
        print(f"Error {r}:", e)

