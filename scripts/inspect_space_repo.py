import urllib.request
import json

url = "https://huggingface.co/api/spaces/silma-ai/silma-tts-v1-demo"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode('utf-8'))
        print("Space id:", data.get('id'))
        siblings = [s.get('rfilename') for s in data.get('siblings', [])]
        print("Space files:", siblings)
except Exception as e:
    print("Error:", e)

