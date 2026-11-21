import urllib.request
import json

url = "https://silma-ai-silma-tts-v1-demo.hf.space/config"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req, timeout=10) as r:
    cfg = json.loads(r.read().decode('utf-8'))

for c in cfg.get("components", []):
    props = c.get("props", {})
    if c.get("id") in [4, 5, 6, 10]:
        print(f"ID {c.get('id')} props:")
        for k, v in props.items():
            if k in ['value', 'choices', 'placeholder']:
                print(f"  {k}: {v}")

