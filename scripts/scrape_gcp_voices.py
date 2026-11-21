import re

with open("scripts/scrape_gcp_voices.py") as f:
    pass

import urllib.request
url = "https://cloud.google.com/text-to-speech/docs/voices"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req) as resp:
    html = resp.read().decode('utf-8')

base = re.search(r'<base\s+href="([^"]+)"', html)
print("Base href:", base.group(1) if base else "None")

cdn = re.findall(r'(https?://[^\s\"<>]*storage\.googleapis\.com[^\s\"<>]*)', html)
print("Storage URLs:", len(cdn), cdn[:3])
