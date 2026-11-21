import urllib.request
import re

req = urllib.request.Request('https://js.puter.com/v2/', headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req, timeout=10) as r:
    content = r.read().decode('utf-8')

for m in re.finditer(r'/drivers/call', content):
    pos = m.start()
    print('Match at', pos)
    print(content[max(0, pos-150):min(len(content), pos+200)])

