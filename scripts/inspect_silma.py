import urllib.request
import json

url = "https://silma-ai-silma-tts-v1-demo.hf.space/config"
try:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=10) as r:
        cfg = json.loads(r.read().decode('utf-8'))
        print("Config keys:", list(cfg.keys()))
        components = cfg.get("components", [])
        print("Components count:", len(components))
        for c in components:
            props = c.get("props", {})
            label = props.get("label")
            c_type = c.get("type")
            elem_id = props.get("elem_id")
            if label or elem_id:
                print(f"  Component: id={c.get('id')}, type={c_type}, label='{label}', elem_id='{elem_id}'")
        endpoints = cfg.get("dependencies", [])
        print("Dependencies count:", len(endpoints))
        for d in endpoints:
            print(f"  Dep: id={d.get('id')}, api_name={d.get('api_name')}, inputs={d.get('inputs')}, outputs={d.get('outputs')}")
except Exception as e:
    print("Error:", e)

