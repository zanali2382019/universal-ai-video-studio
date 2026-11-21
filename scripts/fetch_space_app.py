import urllib.request

url = "https://huggingface.co/spaces/silma-ai/silma-tts-v1-demo/raw/main/app.py"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req, timeout=10) as resp:
    content = resp.read().decode('utf-8')
    print(content[1400:])
