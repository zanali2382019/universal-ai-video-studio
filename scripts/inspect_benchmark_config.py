import urllib.request
import json

url = "https://silma-ai-opensource-arabic-tts-benchmark.hf.space/config"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req, timeout=10) as r:
    cfg = json.loads(r.read().decode('utf-8'))

for c in cfg.get("components", []):
    if c.get("id") == 4:
        props = c.get("props", {})
        val = props.get("value", {})
        print("Headers:", val.get("headers"))
        data = val.get("data", [])
        print("Total rows:", len(data))
        for row in data[:5]:
            print("Row:", row)
