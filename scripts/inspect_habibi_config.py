import urllib.request
import json

url = "https://chenxie95-habibi-tts.hf.space/config"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        cfg = json.loads(r.read().decode('utf-8'))
        print("Title:", cfg.get('title'))
        for c in cfg.get("components", []):
            label = c.get("props", {}).get("label")
            c_type = c.get("type")
            choices = c.get("props", {}).get("choices")
            val = c.get("props", {}).get("value")
            if label or choices:
                print(f"ID {c.get('id')}: type={c_type}, label='{label}'")
                if choices:
                    print("  choices:", choices)
                if c_type in ["textbox", "dropdown", "radio"] and val:
                    print("  val:", str(val)[:100])
except Exception as e:
    print("Error:", e)

