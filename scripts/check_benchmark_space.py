import urllib.request
import json

url = "https://huggingface.co/api/spaces/silma-ai/opensource-arabic-tts-benchmark"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode('utf-8'))
        print("Benchmark space stage:", data.get('runtime', {}).get('stage'))
except Exception as e:
    print("Error:", e)

