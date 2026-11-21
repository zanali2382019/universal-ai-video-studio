import httpx
import json

url = "https://silma-ai-silma-tts-v1-demo.hf.space/gradio_api/openapi.json"
with httpx.Client(timeout=10.0) as client:
    r = client.get(url)
    data = r.json()
    paths = data.get("paths", {})
    for path, methods in paths.items():
        if "do_inference" in path:
            print("Path:", path)
            for m, spec in methods.items():
                print("  Method:", m)
                print("  ReqBody:", json.dumps(spec.get("requestBody", {}), indent=2))

